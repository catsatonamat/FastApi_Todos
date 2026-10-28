"""승인형 조치 (Allowlist).

LLM은 여기 정의된 ID를 '추천'만 할 수 있고, 실행은 사람이 Discord 버튼으로 승인했을 때만 일어난다.
모든 실행은 감사 로그(audit.jsonl)에 남는다.
"""
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

from .config import Settings
from .models import Incident, utcnow

log = logging.getLogger("ops_agent.actions")
TIMEOUT = aiohttp.ClientTimeout(total=30)


@dataclass(frozen=True)
class Action:
    id: str
    label: str
    emoji: str
    risk: str
    description: str


ACTIONS: dict[str, Action] = {
    a.id: a
    for a in (
        Action("disable_fault", "장애 플래그 해제", "🧯", "low",
               "앱 런타임 설정(장애 주입 플래그)을 off로 롤백"),
        Action("restart_app", "앱 재시작", "🔄", "medium",
               "앱 컨테이너 재시작 (docker-socket-proxy, restart API만 허용)"),
        Action("create_issue", "이슈 생성", "📝", "low",
               "GitHub 이슈로 RCA 보고서 등록 (토큰이 없으면 초안만 생성)"),
    )
}


@dataclass
class ActionResult:
    ok: bool
    detail: str
    link: str | None = None


class ActionExecutor:
    def __init__(self, settings: Settings, http: aiohttp.ClientSession):
        self.s = settings
        self.http = http
        self.audit_path = Path(settings.audit_log)

    async def run(self, action_id: str, incident: Incident, actor: str) -> ActionResult:
        if action_id not in ACTIONS:
            result = ActionResult(False, f"허용되지 않은 조치: {action_id}")
        else:
            try:
                result = await getattr(self, f"_{action_id}")(incident)
            except (aiohttp.ClientError, TimeoutError) as e:
                result = ActionResult(False, f"{type(e).__name__}: {e}")
        self.audit(incident, action_id, actor, result)
        return result

    def audit(self, incident: Incident, action_id: str, actor: str, result: ActionResult) -> None:
        entry = {
            "ts": utcnow().isoformat(),
            "incident": incident.id,
            "alertname": incident.alertname,
            "action": action_id,
            "actor": actor,
            "ok": result.ok,
            "detail": result.detail,
        }
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    async def set_chaos(self, config: dict, actor: str) -> ActionResult:
        """앱의 장애 주입 설정을 바꾼다 (Discord /chaos 명령·disable_fault 조치에서 사용)."""
        if not self.s.chaos_admin_token:
            return ActionResult(False, "CHAOS_ADMIN_TOKEN 미설정")
        try:
            async with self.http.put(
                self.s.app_url + "/admin/chaos",
                json=config,
                headers={"X-Admin-Token": self.s.chaos_admin_token},
                timeout=TIMEOUT,
            ) as res:
                if res.status != 200:
                    return ActionResult(False, f"앱 응답 HTTP {res.status}")
        except (aiohttp.ClientError, TimeoutError) as e:
            return ActionResult(False, f"{type(e).__name__}: {e}")
        return ActionResult(True, f"적용됨 (by {actor})")

    # ---------- 개별 조치 ----------
    async def _disable_fault(self, incident: Incident) -> ActionResult:
        result = await self.set_chaos({"mode": "off"}, "ops-agent")
        if result.ok:
            result.detail = "장애 주입 플래그를 off로 변경했습니다."
        return result

    async def _restart_app(self, incident: Incident) -> ActionResult:
        url = f"{self.s.docker_proxy_url}/containers/{self.s.app_container}/restart"
        async with self.http.post(url, params={"t": "5"}, timeout=TIMEOUT) as res:
            if res.status != 204:
                return ActionResult(False, f"Docker API HTTP {res.status}: {(await res.text())[:200]}")
            return ActionResult(True, f"컨테이너 `{self.s.app_container}`를 재시작했습니다.")

    async def _create_issue(self, incident: Incident) -> ActionResult:
        title = f"[Incident {incident.id}] {incident.alertname}: {incident.summary}"[:120]
        body = issue_body(incident)
        if not (self.s.github_token and self.s.github_repo):
            return ActionResult(True, "GITHUB_TOKEN/GITHUB_REPO 미설정 — 이슈 초안만 생성했습니다.\n\n"
                                      f"**{title}**\n\n{body}")
        status, data = await self._post_issue({"title": title, "body": body, "labels": ["incident"]})
        if status == 422:  # 라벨을 만들 권한이 없는 등 라벨 문제 → 라벨 없이 재시도
            status, data = await self._post_issue({"title": title, "body": body})
        if status != 201:
            return ActionResult(False, f"GitHub API HTTP {status}: {str(data)[:200]}")
        incident.issue_number = data["number"]
        return ActionResult(True, f"GitHub 이슈 #{data['number']}를 만들었습니다.", data["html_url"])

    async def comment_issue(self, incident: Incident, text: str) -> None:
        """이슈를 만든 뒤 일어난 일(조치·복구 검증·RESOLVED)을 댓글로 남겨 이슈가 실제 경과와 맞도록 한다."""
        if not (incident.issue_number and self.s.github_token and self.s.github_repo):
            return
        try:
            status, data = await self._post_issue({"body": text}, f"/{incident.issue_number}/comments")
        except (aiohttp.ClientError, TimeoutError) as e:
            status, data = 0, {"error": str(e)}
        if status != 201:
            log.warning("이슈 댓글 실패", extra={"incident": incident.id, "status": status, "error": str(data)[:200]})

    async def _post_issue(self, payload: dict, path: str = "") -> tuple[int, dict]:
        async with self.http.post(
            f"https://api.github.com/repos/{self.s.github_repo}/issues{path}",
            json=payload,
            headers={
                "Authorization": f"Bearer {self.s.github_token}",
                "Accept": "application/vnd.github+json",
            },
            timeout=TIMEOUT,
        ) as res:
            return res.status, await res.json(content_type=None)


CONFIDENCE_KO = {"high": "높음", "medium": "중간", "low": "낮음"}
DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "Asia/Seoul"))


def _kst(dt) -> str:
    return dt.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def _metric_row(metrics: dict) -> str:
    er, p95, rps = metrics.get("error_rate"), metrics.get("p95_seconds"), metrics.get("rps")
    return " / ".join([
        f"{er:.1%}" if isinstance(er, float) else "0%",
        f"{p95 * 1000:.0f}ms" if isinstance(p95, float) else "N/A",
        f"{rps:.1f} req/s" if isinstance(rps, float) else "N/A",
    ])


def _clip(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def issue_body(incident: Incident) -> str:
    """GitHub 이슈 본문. Discord 진행 메시지가 아니라 구조화된 보고서·지표·도구 기록으로 만든다."""
    r = incident.report
    analysis = f"{incident.llm_label} · 도구 호출 {incident.tool_calls}회"
    if incident.time_to_report is not None:
        analysis += f" · 알림→보고 {incident.time_to_report:.0f}초"
    if incident.report_fallback:
        analysis += " · ⚠️ LLM 실패: 기본 증거만"

    lines = [
        f"> {r.summary}" if r else f"> {incident.summary}",
        "",
        "| 항목 | 내용 |",
        "|---|---|",
        f"| 알림 | `{incident.alertname}` ({incident.severity}) |",
        f"| 발생 | {_kst(incident.started_at)} |",
    ]
    if metrics := incident.evidence.get("metrics"):
        lines.append(f"| 에러율 / p95 / 처리량 | {_metric_row(metrics)} |")
    lines.append(f"| 분석 | {analysis} |")

    if r:
        lines += ["", "## 영향", r.impact, "", "## 원인 분석"]
        # 신뢰도 낮은 가설은 접어서 본문을 짧게 유지한다
        main = [h for h in r.hypotheses if h.confidence != "low"] or r.hypotheses[:1]
        rest = [h for h in r.hypotheses if h not in main]
        for i, h in enumerate(main, 1):
            lines += ["", f"**{i}. {h.cause}** — 신뢰도 {CONFIDENCE_KO[h.confidence]}"]
            lines += [f"- {_clip(e)}" for e in h.evidence[:3]]
        if rest:
            lines += ["", "<details><summary>기타 가능성</summary>", ""]
            lines += [f"- {h.cause} (신뢰도 {CONFIDENCE_KO[h.confidence]})" for h in rest]
            lines += ["", "</details>"]

        remediations = [a for a in r.recommended_actions if a != "create_issue"]
        done = [a for a in incident.actions_done if a != "create_issue"]
        lines += ["", "## 조치"]
        lines += [f"- 에이전트 권장: {', '.join(f'{ACTIONS[a].emoji} {ACTIONS[a].label}' for a in remediations) or '없음'}"]
        lines += [f"- 이슈 생성 시점까지 실행: {', '.join(ACTIONS[a].label for a in done) if done else '없음'}"]
        lines += ["- 이후 조치·복구 검증·해제 기록은 아래 댓글로 이어집니다."]

        if r.next_steps:
            lines += ["", "## 후속 확인", *[f"- [ ] {_clip(s)}" for s in r.next_steps]]

    if incident.tool_log:
        lines += ["", f"<details><summary>에이전트 조사 기록 (도구 호출 {len(incident.tool_log)}회)</summary>", "",
                  "| # | 도구 | 인자 |", "|---|---|---|"]
        for i, call in enumerate(incident.tool_log, 1):
            args = ", ".join(f"{k}=`{_clip(v, 120)}`" for k, v in call["args"].items()) or "-"
            lines.append(f"| {i} | `{call['name']}` | {args.replace('|', '\\|')} |")
        lines += ["", "</details>"]

    lines += ["", f"<sub>ops-agent · Incident `{incident.id}`</sub>"]
    return "\n".join(lines)
