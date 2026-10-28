import asyncio
import json
from datetime import datetime, timezone

import pytest

from conftest import REPORT, FakeLLM, FakeObs, tool_call
from ops_agent.agent import Investigator
from ops_agent.llm import LLMError, parse_json
from ops_agent.models import Incident


def make_incident() -> Incident:
    return Incident(
        group_key="g", alertname="TodoHighErrorRate", severity="critical",
        summary="Todo API 5xx 에러율 42%", started_at=datetime.now(timezone.utc), labels={},
    )


def run(investigator: Investigator, incident: Incident) -> list[str]:
    steps: list[str] = []

    async def on_step(text):
        steps.append(text)

    asyncio.run(investigator.investigate(incident, on_step))
    return steps


def test_tool_loop_then_structured_report():
    obs = FakeObs()
    llm = FakeLLM([
        tool_call("get_runtime_config", {}),
        tool_call("search_logs", {"query": '{service="fastapi-app", level="ERROR"}', "minutes": 5}, id="call_2"),
        {"role": "assistant", "content": "저장소 장애 주입 플래그가 원인으로 보인다."},
        {"role": "assistant", "content": json.dumps(REPORT, ensure_ascii=False)},
    ])
    incident = make_incident()
    steps = run(Investigator(llm, obs, "fake/model"), incident)

    assert incident.tool_calls == 2
    assert ("runtime_config",) in obs.calls
    assert ("logs", '{service="fastapi-app", level="ERROR"}', 4) in obs.calls  # 요청 5분 → 인시던트 구간 4분으로 제한
    assert incident.report.recommended_actions == ["disable_fault"]
    assert incident.report_fallback is False
    assert incident.evidence["metrics"]["error_rate"] == 0.42
    assert any("get_runtime_config" in s for s in steps)

    # 첫 요청에 기본 증거가 들어가고, 도구 결과가 tool 메시지로 이어진다
    first = llm.requests[0]["messages"][1]["content"]
    assert "StorageUnavailableError" in first
    tool_msgs = [m for m in llm.requests[2]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_1", "call_2"]
    # 마지막 요청은 도구 없이 JSON 스키마로 보고서를 요구한다
    assert llm.requests[-1]["tools"] is None
    assert llm.requests[-1]["json_schema"]["title"] == "Report"


def test_stops_after_max_steps():
    llm = FakeLLM([tool_call("query_prometheus", {"query": f"q{i}"}, id=f"c{i}") for i in range(3)]
                  + [{"role": "assistant", "content": json.dumps(REPORT)}])
    incident = make_incident()
    run(Investigator(llm, FakeObs(), "fake", max_steps=3), incident)
    assert incident.tool_calls == 3
    assert incident.report is not None


def test_falls_back_when_llm_unreachable():
    incident = make_incident()
    steps = run(Investigator(FakeLLM([LLMError("connection refused")]), FakeObs(), "fake"), incident)
    assert incident.report_fallback is True
    assert incident.report.recommended_actions == ["create_issue"]
    assert any("42.0%" in e for e in incident.report.hypotheses[0].evidence)
    assert any("LLM 분석 실패" in s for s in steps)


def test_falls_back_when_report_is_invalid():
    llm = FakeLLM([{"role": "assistant", "content": "끝"}, {"role": "assistant", "content": '{"summary": "x"}'}])
    incident = make_incident()
    run(Investigator(llm, FakeObs(), "fake"), incident)
    assert incident.report_fallback is True


def test_unknown_tool_and_bad_arguments_do_not_crash():
    bad = {"role": "assistant", "content": "", "tool_calls": [
        {"id": "a", "function": {"name": "rm_rf", "arguments": "{}"}},
        {"id": "b", "function": {"name": "query_prometheus", "arguments": "not json"}},
    ]}
    llm = FakeLLM([bad, {"role": "assistant", "content": "끝"}, {"role": "assistant", "content": json.dumps(REPORT)}])
    incident = make_incident()
    run(Investigator(llm, FakeObs(), "fake"), incident)
    tool_msgs = [m["content"] for m in llm.requests[1]["messages"] if m["role"] == "tool"]
    assert "알 수 없는 도구" in tool_msgs[0]
    assert "필수 인자 누락" in tool_msgs[1]
    assert incident.report_fallback is False


@pytest.mark.parametrize("text", [
    '{"a": 1}',
    '```json\n{"a": 1}\n```',
    '보고서입니다:\n{"a": 1}\n이상입니다.',
])
def test_parse_json_variants(text):
    assert parse_json(text) == {"a": 1}


def test_log_window_starts_before_alert():
    from datetime import timedelta

    from ops_agent.agent import log_window_minutes

    incident = make_incident()
    assert log_window_minutes(incident) == 4  # 경과 1분 미만 → 올림 1분 + 3분
    incident.started_at -= timedelta(minutes=4, seconds=10)
    assert log_window_minutes(incident) == 8


def test_duplicate_tool_calls_are_not_executed_twice():
    same = tool_call("query_prometheus", {"query": "up"})
    llm = FakeLLM([same, same, {"role": "assistant", "content": "끝"}, {"role": "assistant", "content": json.dumps(REPORT)}])
    obs = FakeObs()
    incident = make_incident()
    run(Investigator(llm, obs, "fake"), incident)
    assert incident.tool_calls == 1
    assert [c for c in obs.calls if c[0] == "promql"] == [("promql", "up")]
    assert "이미 호출" in llm.requests[2]["messages"][-1]["content"]


def test_baseline_includes_runtime_config():
    llm = FakeLLM([{"role": "assistant", "content": "끝"}, {"role": "assistant", "content": json.dumps(REPORT)}])
    incident = make_incident()
    run(Investigator(llm, FakeObs(), "fake"), incident)
    assert incident.evidence["current_runtime_config"]["mode"] == "storage_error"
    assert "storage_error" in llm.requests[0]["messages"][1]["content"]


def test_log_search_is_clamped_to_incident_window():
    llm = FakeLLM([
        tool_call("search_logs", {"query": "{x}", "minutes": 60}),
        {"role": "assistant", "content": "끝"},
        {"role": "assistant", "content": json.dumps(REPORT)},
    ])
    obs = FakeObs()
    run(Investigator(llm, obs, "fake"), make_incident())
    assert ("logs", "{x}", 4) in obs.calls
