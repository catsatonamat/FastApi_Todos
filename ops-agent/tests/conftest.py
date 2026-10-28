import json

import pytest

from ops_agent.actions import ActionResult
from ops_agent.config import Settings
from ops_agent.llm import LLMError


class FakeObs:
    """Observability 대역: 고정된 지표·로그를 돌려준다."""

    def __init__(self, healthy_after_fix: bool = True):
        self.calls: list[tuple] = []
        self.healthy_after_fix = healthy_after_fix

    async def snapshot(self, window: str = "1m") -> dict:
        self.calls.append(("snapshot", window))
        if window == "30s":
            return {"error_rate": 0.0 if self.healthy_after_fix else 0.4, "p95_seconds": 0.01, "up": 1}
        return {"error_rate": 0.42, "p95_seconds": 0.02, "rps": 5.0, "up": 1, "storage_errors_per_sec": 2.1}

    async def problem_log_summary(self, minutes: int = 10) -> dict:
        return {"total": 30, "by_type": {"StorageUnavailableError": 30}, "latest": []}

    async def promql(self, query: str) -> list[dict]:
        self.calls.append(("promql", query))
        return [{"labels": {}, "value": 0.42}]

    async def promql_range(self, query: str, minutes: int = 15) -> list[dict]:
        self.calls.append(("promql_range", query, minutes))
        return []

    async def logs(self, query: str, minutes: int = 10, limit: int = 30) -> list[dict]:
        self.calls.append(("logs", query, minutes))
        return [{"msg": "StorageUnavailableError: [Errno 5] Input/output error", "error_type": "StorageUnavailableError"}]

    async def app_health(self) -> dict:
        return {"status_code": 503}

    async def runtime_config(self) -> dict:
        self.calls.append(("runtime_config",))
        return {"mode": "storage_error", "error_rate": 0.5, "since": "2026-10-26T03:00:00+00:00"}


REPORT = {
    "summary": "Todo API 에러율 42%",
    "severity": "critical",
    "impact": "할 일 조회·저장 요청의 42%가 실패",
    "hypotheses": [{"cause": "장애 주입 플래그 활성화로 저장소 I/O 오류", "confidence": "high",
                    "evidence": ["runtime_config mode=storage_error", "StorageUnavailableError 30건"]}],
    "recommended_actions": ["disable_fault"],
    "next_steps": ["설정 변경 이력 확인"],
}


def tool_call(name: str, args: dict, id: str = "call_1") -> dict:
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
    ]}


class FakeLLM:
    """미리 정해 둔 응답을 순서대로 돌려준다. LLMError 인스턴스를 넣으면 그 차례에 예외를 던진다."""

    def __init__(self, responses: list):
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def chat(self, messages, tools=None, json_schema=None):
        self.requests.append({"messages": list(messages), "tools": tools, "json_schema": json_schema})
        response = self.responses.pop(0)
        if isinstance(response, LLMError):
            raise response
        return response


class FakeExecutor:
    def __init__(self):
        self.runs: list[tuple] = []
        self.audits: list[tuple] = []
        self.comments: list[str] = []

    async def run(self, action_id, incident, actor):
        self.runs.append((action_id, incident.id, actor))
        return ActionResult(True, f"{action_id} done")

    def audit(self, incident, action_id, actor, result):
        self.audits.append((action_id, actor))

    async def comment_issue(self, incident, text):
        self.comments.append(text)


class RecordingNotifier:
    def __init__(self):
        self.events: list[tuple] = []

    async def incident_opened(self, incident):
        self.events.append(("opened", incident.id))

    async def step(self, incident, text):
        self.events.append(("step", text))

    async def report_ready(self, incident):
        self.events.append(("report", incident.id))

    async def action_done(self, incident, action_id, actor, result):
        self.events.append(("action", action_id, actor, result.ok))

    async def note(self, incident, text):
        self.events.append(("note", text))

    async def resolved(self, incident):
        self.events.append(("resolved", incident.id))


@pytest.fixture
def settings(tmp_path):
    return Settings(audit_log=str(tmp_path / "audit.jsonl"), incident_log=str(tmp_path / "incidents.jsonl"),
                    verify_delay=0, chaos_admin_token="t")
