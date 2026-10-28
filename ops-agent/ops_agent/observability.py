"""관측 데이터 조회 (읽기 전용): Prometheus, Loki, 앱 헬스체크·런타임 설정."""
import json
import time
from collections import Counter

import aiohttp

from .config import Settings

JOB = 'job="todo-app"'
TIMEOUT = aiohttp.ClientTimeout(total=10)


def error_rate_expr(window: str = "1m") -> str:
    return (
        f'sum(rate(http_requests_total{{{JOB},status=~"5.."}}[{window}]))'
        f" / sum(rate(http_requests_total{{{JOB}}}[{window}]))"
    )


def p95_expr(window: str = "1m") -> str:
    return f"histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{{{JOB}}}[{window}])))"


class Observability:
    def __init__(self, settings: Settings, http: aiohttp.ClientSession):
        self.s = settings
        self.http = http

    # ---------- Prometheus ----------
    async def promql(self, query: str) -> list[dict]:
        """instant query. [{"labels": {...}, "value": float}, ...]"""
        data = await self._prom("/api/v1/query", {"query": query})
        return [
            {"labels": r["metric"], "value": _num(r["value"][1])}
            for r in data.get("result", [])
        ]

    async def promql_range(self, query: str, minutes: int = 15, points: int = 12) -> list[dict]:
        """range query를 LLM이 읽기 쉽게 요약 (시리즈별 min/max/last + 시간순 샘플)."""
        end = time.time()
        start = end - minutes * 60
        step = max(5, int(minutes * 60 / points))
        data = await self._prom(
            "/api/v1/query_range", {"query": query, "start": start, "end": end, "step": step}
        )
        series = []
        for r in data.get("result", []):
            values = [(float(t), _num(v)) for t, v in r["values"]]
            nums = [v for _, v in values if v is not None]
            series.append({
                "labels": r["metric"],
                "min": min(nums, default=None),
                "max": max(nums, default=None),
                "last": values[-1][1] if values else None,
                "samples": [
                    {"t": time.strftime("%H:%M:%S", time.localtime(t)), "v": v} for t, v in values
                ],
            })
        return series

    async def _prom(self, path: str, params: dict) -> dict:
        async with self.http.get(self.s.prometheus_url + path, params=params, timeout=TIMEOUT) as res:
            body = await res.json(content_type=None)
            if body.get("status") != "success":
                raise ValueError(body.get("error", f"HTTP {res.status}"))
            return body["data"]

    async def scalar(self, query: str) -> float | None:
        result = await self.promql(query)
        return result[0]["value"] if result else None

    # ---------- Loki ----------
    async def logs(self, query: str, minutes: int = 10, limit: int = 30) -> list[dict]:
        end = time.time_ns()
        params = {
            "query": query,
            "start": end - minutes * 60 * 10**9,
            "end": end,
            "limit": limit,
            "direction": "backward",
        }
        async with self.http.get(
            self.s.loki_url + "/loki/api/v1/query_range", params=params, timeout=TIMEOUT
        ) as res:
            body = await res.json(content_type=None)
            if res.status != 200:
                raise ValueError(body if isinstance(body, str) else json.dumps(body)[:300])
        lines = []
        for stream in body["data"]["result"]:
            for ts, line in stream["values"]:
                lines.append((int(ts), _compact_log(line)))
        lines.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in lines[:limit]]

    async def problem_log_summary(self, minutes: int = 10) -> dict:
        """최근 ERROR·WARNING 로그를 유형별로 묶어 개수를 센다. 설정 변경 이벤트는 따로 모은다."""
        entries = await self.logs('{service="fastapi-app", level=~"ERROR|WARNING"}', minutes=minutes, limit=1000)
        changes = [e for e in entries if "chaos" in e]
        problems = [e for e in entries if "chaos" not in e]
        kinds = Counter(_log_kind(e) for e in problems)
        return {
            "window_minutes": minutes,
            "total": len(problems),
            "by_type": dict(kinds.most_common(5)),
            "latest": problems[:5],
            "config_change_events": changes[:5],
        }

    # ---------- 앱 ----------
    async def app_health(self) -> dict:
        start = time.perf_counter()
        try:
            async with self.http.get(self.s.app_url + "/health", timeout=TIMEOUT) as res:
                body = await res.text()
                return {
                    "status_code": res.status,
                    "body": body[:200],
                    "latency_ms": round((time.perf_counter() - start) * 1000, 1),
                }
        except (aiohttp.ClientError, TimeoutError) as e:
            return {"status_code": None, "error": f"{type(e).__name__}: {e}"}

    async def runtime_config(self) -> dict:
        """앱의 런타임 설정(장애 주입 플래그)과 마지막 변경 시각."""
        if not self.s.chaos_admin_token:
            return {"error": "CHAOS_ADMIN_TOKEN 미설정으로 조회 불가"}
        try:
            async with self.http.get(
                self.s.app_url + "/admin/chaos",
                headers={"X-Admin-Token": self.s.chaos_admin_token},
                timeout=TIMEOUT,
            ) as res:
                return await res.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as e:
            return {"error": f"{type(e).__name__}: {e}"}

    # ---------- 스냅샷 ----------
    async def snapshot(self, window: str = "1m") -> dict:
        """에러율·지연·처리량·가용성 등 핵심 지표를 한 번에 조회한다."""
        queries = {
            "error_rate": error_rate_expr(window),
            "p95_seconds": p95_expr(window),
            "rps": f"sum(rate(http_requests_total{{{JOB}}}[{window}]))",
            "up": f"up{{{JOB}}}",
            "storage_errors_per_sec": f"sum(rate(todo_storage_errors_total{{{JOB}}}[{window}]))",
        }
        snap: dict = {"window": window}
        for key, q in queries.items():
            try:
                snap[key] = await self.scalar(q)
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                snap[key] = f"query failed: {e}"
        try:
            snap["top_error_routes"] = [
                {"route": r["labels"].get("route"), "status": r["labels"].get("status"), "rps": r["value"]}
                for r in await self.promql(
                    f'topk(3, sum by (route, status) (rate(http_requests_total{{{JOB},status=~"5.."}}[{window}])))'
                )
            ]
            snap["p95_by_route"] = [
                {"route": r["labels"].get("route"), "p95_seconds": r["value"]}
                for r in await self.promql(
                    "topk(3, histogram_quantile(0.95, sum by (le, route) "
                    f"(rate(http_request_duration_seconds_bucket{{{JOB}}}[{window}]))))"
                )
            ]
            snap["firing_alerts"] = sorted({
                r["labels"].get("alertname") for r in await self.promql('ALERTS{alertstate="firing"}')
            })
        except (aiohttp.ClientError, TimeoutError, ValueError):
            pass
        return snap


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else round(f, 4)  # NaN → None


def _log_kind(entry: dict) -> str:
    if entry.get("error_type"):
        return f"{entry['level']} {entry['error_type']}"
    if entry.get("level") == "WARNING" and "duration_ms" in entry:
        return f"WARNING slow request {entry.get('route')} (>500ms)"
    return f"{entry.get('level')} {entry.get('msg', '')[:80]}"


def _compact_log(line: str) -> dict:
    """로그 한 줄에서 분석에 필요한 필드만 남긴다."""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return {"msg": line[:300]}
    keep = ("ts", "level", "msg", "error_type", "route", "status", "duration_ms", "chaos")
    return {k: entry[k] for k in keep if k in entry}


def is_healthy(snap: dict) -> tuple[bool, list[str]]:
    """복구 검증 기준. (정상 여부, 위반 항목 설명)"""
    problems = []
    if snap.get("up") != 1:
        problems.append(f"up={snap.get('up')}")
    er = snap.get("error_rate")
    if isinstance(er, float) and er > 0.05:
        problems.append(f"에러율 {er:.1%}")
    p95 = snap.get("p95_seconds")
    if isinstance(p95, float) and p95 > 0.5:
        problems.append(f"p95 {p95:.2f}s")
    return not problems, problems
