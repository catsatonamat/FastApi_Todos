"""조사 에이전트: 기본 증거 수집 → LLM 도구 사용 루프 → 구조화 보고서."""
import json
import logging
import math
import re
import time
from collections.abc import Awaitable, Callable

from pydantic import ValidationError

from .llm import LLMClient, LLMError, parse_json
from .models import Hypothesis, Incident, Report, utcnow
from .observability import Observability
from .tools import TOOLS, run_tool

log = logging.getLogger("ops_agent.agent")

OnStep = Callable[[str], Awaitable[None]]

SYSTEM_PROMPT = """너는 'Todo API' 서비스를 운영하는 SRE 에이전트다. 알림을 받으면 도구로 메트릭·로그·설정을 조사해 근본 원인을 찾는다.

## 환경
- 서비스: FastAPI Todo API (Prometheus job="todo-app", Loki service="fastapi-app")
- 메트릭:
  - http_requests_total{method, route, status}  (counter)
  - http_request_duration_seconds_bucket{method, route, le}  (histogram)
  - todo_storage_errors_total{op="reading|writing"}  (저장소 todo.json 접근 실패, counter)
  - up{job="todo-app"}
- 로그(JSON): 라벨 {service="fastapi-app", level="INFO|WARNING|ERROR"}, 필드 msg, route, status, duration_ms, error_type
  - 파싱 예: {service="fastapi-app"} | json | error_type != ""
- 앱 런타임 설정(피처 플래그·장애 주입)은 get_runtime_config로 확인한다.
- 기본 증거에 포함된 것: metrics(에러율·p95·route별 p95), problem_logs(ERROR/WARNING 로그 유형별 개수, config_change_events), current_runtime_config

## 조사 순서
1. 언제부터: 지표가 나빠지기 시작한 시각 (query_prometheus_range)
2. 어디서: 영향을 받는 route·status
3. 무엇이: 로그의 error_type과 메시지
4. 왜: 그 시각 직전의 변경 사항 (get_runtime_config의 since, 재시작 여부)

## 규칙
- 도구는 모두 읽기 전용이다. 조치는 직접 실행하지 말고 보고서에서 ID로 추천만 한다.
- 추측하지 말고 도구 결과의 수치와 로그 문구를 근거로 든다.
- 알림 started_at보다 3분 이상 앞선 로그·지표는 이전 장애의 흔적일 수 있으니 근거로 쓰지 않는다 (로그의 ts를 확인).
- 알림 종류에 맞는 신호를 먼저 본다: 지연 알림이면 latency 히스토그램, 에러율 알림이면 status·error_type.
- 장애의 대부분은 변경에서 시작된다. 런타임 설정 변경 시각과 장애 시작 시각이 맞으면 그 변경을 가장 유력한 원인으로 본다.
- 이미 받은 기본 증거를 바탕으로, 부족한 부분만 2~4회 도구를 호출해 확인한다.
- 충분히 확인했으면 도구 호출 없이 3~4문장으로 결론(원인·근거·권장 조치)을 답한다.
- 모든 답변은 한국어로 한다.

## 추천 가능한 조치 ID
- disable_fault: 런타임 설정(장애 주입 플래그)을 off로 롤백. 최근 설정 변경이 원인일 때.
- restart_app: 앱 컨테이너 재시작. 프로세스 상태 이상·응답 없음일 때 (메모리 상의 설정도 초기화됨).
- create_issue: RCA 보고서를 GitHub 이슈로 기록. 후속 조치가 필요할 때.
"""

REPORT_PROMPT = """조사를 마쳤다. 지금까지의 근거로 운영 보고서를 아래 JSON 스키마에 맞춰 작성하라.
- summary에는 핵심 수치(에러율, 지연 등)를 포함한다.
- hypotheses는 가능성 높은 순으로 1~3개, 각 evidence에는 실제로 관찰한 값·로그를 쓴다.
- recommended_actions는 disable_fault, restart_app, create_issue 중에서만 고른다.
- 한국어로 작성한다.
JSON 스키마:
"""


class Investigator:
    def __init__(self, llm: LLMClient, obs: Observability, llm_label: str, max_steps: int = 6):
        self.llm = llm
        self.obs = obs
        self.llm_label = llm_label
        self.max_steps = max_steps

    async def investigate(self, incident: Incident, on_step: OnStep) -> None:
        """incident.evidence / report / tool_calls 를 채운다."""
        started = time.perf_counter()
        incident.llm_label = self.llm_label

        await on_step("📊 기본 증거 수집: 핵심 지표 · 에러/지연 로그 · 최근 설정 변경")
        incident.evidence = await self.collect_baseline(incident)

        try:
            messages = await self.tool_loop(incident, on_step)
            await on_step("🧠 보고서 작성 중…")
            incident.report = await self.write_report(messages)
        except (LLMError, ValidationError, ValueError, KeyError) as e:
            log.warning("LLM 분석 실패, 기본 증거로 보고", extra={"incident": incident.id, "error": str(e)})
            await on_step(f"⚠️ LLM 분석 실패 ({type(e).__name__}) — 기본 증거만으로 보고합니다.")
            incident.report = fallback_report(incident)
            incident.report_fallback = True

        log.info(
            "investigation finished",
            extra={"incident": incident.id, "seconds": round(time.perf_counter() - started, 1),
                   "tool_calls": incident.tool_calls, "fallback": incident.report_fallback},
        )

    async def collect_baseline(self, incident: Incident) -> dict:
        """LLM과 무관하게 항상 수집하는 증거 (LLM이 실패해도 보고서를 만들 수 있도록)."""
        evidence: dict = {"metrics": await self.obs.snapshot()}
        try:
            evidence["problem_logs"] = await self.obs.problem_log_summary(minutes=log_window_minutes(incident))
        except Exception as e:  # Loki 장애가 조사 전체를 막지 않도록
            evidence["problem_logs"] = {"error": f"{type(e).__name__}: {e}"}
        # 변경 이력: 장애 상관분석의 1순위 단서 (상용 AIOps도 배포·설정 변경 이벤트를 함께 붙인다)
        evidence["current_runtime_config"] = await self.obs.runtime_config()
        return evidence

    async def tool_loop(self, incident: Incident, on_step: OnStep) -> list[dict]:
        alert = {
            "alertname": incident.alertname,
            "severity": incident.severity,
            "summary": incident.summary,
            "started_at": incident.started_at.isoformat(),
            "labels": incident.labels,
        }
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"알림이 발생했다. 원인을 조사하라. (현재 시각: {utcnow().isoformat(timespec='seconds')})\n\n"
                f"## 알림\n{json.dumps(alert, ensure_ascii=False)}\n\n"
                f"## 기본 증거\n{json.dumps(incident.evidence, ensure_ascii=False, default=str)}"
            )},
        ]
        seen: set[str] = set()
        for _ in range(self.max_steps):
            message = await self.llm.chat(messages, tools=TOOLS)
            calls = message.get("tool_calls") or []
            messages.append({k: v for k, v in message.items() if v is not None})
            if not calls:
                if message.get("content"):
                    await on_step(f"💬 {brief(message['content'])}")
                break
            for call in calls:
                name = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                key = f"{name}:{json.dumps(args, sort_keys=True)}"
                if key in seen:
                    result = '{"error": "같은 인자로 이미 호출했다. 다른 쿼리를 쓰거나 결론을 내려라."}'
                else:
                    seen.add(key)
                    await on_step(f"🔧 `{name}` {_fmt_args(args)}")
                    result = await run_tool(self.obs, name, args, log_window=log_window_minutes(incident))
                    incident.tool_calls += 1
                    incident.tool_log.append({"name": name, "args": args})
                messages.append({"role": "tool", "tool_call_id": call.get("id", name), "content": result})
        return messages

    async def write_report(self, messages: list[dict]) -> Report:
        schema = Report.model_json_schema()
        messages = messages + [{
            "role": "user",
            "content": REPORT_PROMPT + json.dumps(schema, ensure_ascii=False),
        }]
        message = await self.llm.chat(messages, json_schema=schema)
        return Report.model_validate(parse_json(message.get("content") or ""))


def log_window_minutes(incident: Incident) -> int:
    """알림 시작 3분 전부터 지금까지 (이전 장애의 로그가 섞이지 않도록)."""
    elapsed = (utcnow() - incident.started_at).total_seconds()
    return max(3, math.ceil(elapsed / 60) + 3)


def brief(text: str, limit: int = 300) -> str:
    """LLM의 중간 결론을 스레드용으로 줄인다: 마크다운 제목·강조를 걷어내고 문장 단위로 자른다."""
    text = re.sub(r"^#+\s*", "", text, flags=re.M)
    text = re.sub(r"\*\*|__", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("다. "), cut.rfind("다."))
    return (cut[: end + 2] if end > limit // 3 else cut).rstrip() + " …(전체 내용은 보고서 참고)"


def _fmt_args(args: dict) -> str:
    if not args:
        return ""
    text = " ".join(f"{k}=`{v}`" for k, v in args.items())
    return text[:300]


def fallback_report(incident: Incident) -> Report:
    """LLM 없이 기본 증거만으로 만든 보고서."""
    m = incident.evidence.get("metrics", {})
    logs = incident.evidence.get("problem_logs", {})
    facts = [
        f"에러율: {_pct(m.get('error_rate'))}",
        f"p95 지연: {_sec(m.get('p95_seconds'))}",
        f"처리량: {_num(m.get('rps'))} req/s, up={m.get('up')}",
        f"저장소 오류: {_num(m.get('storage_errors_per_sec'))} /s",
    ]
    by_type = logs.get("by_type") or {}
    if by_type:
        facts.append("최근 문제 로그: " + ", ".join(f"{k} {v}건" for k, v in by_type.items()))
    return Report(
        summary=f"{incident.alertname} 발생 — {incident.summary}",
        severity="high" if incident.severity == "critical" else "medium",
        impact="자동 분석을 수행하지 못했습니다. 아래 지표로 영향 범위를 확인하세요.",
        hypotheses=[Hypothesis(cause="(LLM 분석 실패) 기본 지표 참고", confidence="low", evidence=facts)],
        recommended_actions=["create_issue"],
        next_steps=["Grafana 대시보드에서 에러율·지연 추이 확인", "Loki에서 ERROR 로그 확인", "LLM 연결 상태 확인"],
    )


def _pct(v) -> str:
    return f"{v:.1%}" if isinstance(v, float) else "N/A"


def _sec(v) -> str:
    return f"{v:.2f}s" if isinstance(v, float) else "N/A"


def _num(v) -> str:
    return f"{v:.2f}" if isinstance(v, float) else str(v)
