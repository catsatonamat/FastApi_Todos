import asyncio
import json

from conftest import REPORT, FakeExecutor, FakeObs, RecordingNotifier
from ops_agent.actions import ActionExecutor
from ops_agent.models import Report
from ops_agent.service import OpsService


class FakeInvestigator:
    async def investigate(self, incident, on_step):
        await on_step("🔧 fake step")
        incident.report = Report.model_validate(REPORT)


def alert_payload(status="firing", group_key="{}:{alertname=\"TodoHighErrorRate\"}"):
    return {
        "status": status,
        "groupKey": group_key,
        "commonLabels": {"alertname": "TodoHighErrorRate", "severity": "critical", "service": "todo-api"},
        "commonAnnotations": {"summary": "Todo API 5xx 에러율 42%"},
        "alerts": [{
            "status": status,
            "labels": {"alertname": "TodoHighErrorRate"},
            "annotations": {},
            "startsAt": "2026-10-26T03:00:00.123456789Z",
        }],
    }


def make_service(settings, obs=None, executor=None):
    notifier = RecordingNotifier()
    service = OpsService(settings, obs or FakeObs(), FakeInvestigator(), executor or FakeExecutor(), notifier)
    return service, notifier


async def drain(service):
    while service._tasks:
        await asyncio.gather(*list(service._tasks))


def test_firing_alert_opens_incident_and_reports(settings):
    async def scenario():
        service, notifier = make_service(settings)
        created = await service.handle_alertmanager(alert_payload())
        await drain(service)
        return service, notifier, created

    service, notifier, created = asyncio.run(scenario())
    incident = service.incidents[created[0]]
    assert incident.alertname == "TodoHighErrorRate"
    assert incident.severity == "critical"
    assert incident.status == "reported"
    assert incident.started_at.year == 2026
    assert [e[0] for e in notifier.events] == ["opened", "step", "report"]


def test_repeated_firing_is_deduplicated(settings):
    async def scenario():
        service, _ = make_service(settings)
        first = await service.handle_alertmanager(alert_payload())
        second = await service.handle_alertmanager(alert_payload())
        await drain(service)
        return service, first, second

    service, first, second = asyncio.run(scenario())
    assert len(first) == 1 and second == []
    assert len(service.incidents) == 1


def test_resolved_alert_closes_incident_and_new_firing_opens_new(settings):
    async def scenario():
        service, notifier = make_service(settings)
        [iid] = await service.handle_alertmanager(alert_payload())
        await drain(service)
        await service.handle_alertmanager(alert_payload("resolved"))
        await drain(service)
        again = await service.handle_alertmanager(alert_payload())
        await drain(service)
        return service, notifier, iid, again

    service, notifier, iid, again = asyncio.run(scenario())
    assert service.incidents[iid].status == "resolved"
    assert service.incidents[iid].time_to_resolve is not None
    assert ("resolved", iid) in notifier.events
    assert len(again) == 1 and again[0] != iid


def test_action_runs_once_and_triggers_verification(settings):
    async def scenario():
        executor = FakeExecutor()
        service, notifier = make_service(settings, executor=executor)
        [iid] = await service.handle_alertmanager(alert_payload())
        await drain(service)
        first = await service.run_action(iid, "disable_fault", "alice")
        second = await service.run_action(iid, "disable_fault", "bob")
        await drain(service)
        return executor, notifier, first, second

    executor, notifier, first, second = asyncio.run(scenario())
    assert first.ok and not second.ok
    assert len(executor.runs) == 1
    assert ("action", "disable_fault", "alice", True) in notifier.events
    notes = [e[1] for e in notifier.events if e[0] == "note"]
    assert any(n.startswith("✅ 복구 확인") for n in notes)


def test_verification_reports_still_unhealthy(settings):
    async def scenario():
        service, notifier = make_service(settings, obs=FakeObs(healthy_after_fix=False))
        [iid] = await service.handle_alertmanager(alert_payload())
        await drain(service)
        await service.run_action(iid, "restart_app", "alice")
        await drain(service)
        return notifier

    notes = [e[1] for e in asyncio.run(scenario()).events if e[0] == "note"]
    assert any(n.startswith("⚠️ 아직 비정상") for n in notes)


def test_unknown_action_and_incident_rejected(settings):
    async def scenario():
        executor = FakeExecutor()
        service, _ = make_service(settings, executor=executor)
        [iid] = await service.handle_alertmanager(alert_payload())
        await drain(service)
        return executor, await service.run_action(iid, "drop_database", "x"), await service.run_action("nope", "restart_app", "x")

    executor, bad_action, bad_incident = asyncio.run(scenario())
    assert not bad_action.ok and not bad_incident.ok
    assert executor.runs == []


def test_issue_draft_without_github_token_is_audited(settings):
    async def scenario():
        import aiohttp
        async with aiohttp.ClientSession() as http:
            service, _ = make_service(settings, executor=ActionExecutor(settings, http))
            [iid] = await service.handle_alertmanager(alert_payload())
            await drain(service)
            return await service.run_action(iid, "create_issue", "alice")

    result = asyncio.run(scenario())
    assert result.ok and "초안" in result.detail
    assert "장애 주입 플래그 활성화" in result.detail
    audit = [json.loads(line) for line in open(settings.audit_log, encoding="utf-8")]
    assert audit[-1]["action"] == "create_issue" and audit[-1]["actor"] == "alice"


def test_issue_retries_without_label_on_422(settings):
    from dataclasses import replace

    from ops_agent.models import Incident, utcnow

    s = replace(settings, github_token="t", github_repo="o/r")
    executor = ActionExecutor(s, http=None)
    sent = []

    async def fake_post(payload, path=""):
        sent.append(payload)
        return (422, {"message": "label"}) if "labels" in payload else (201, {"number": 7, "html_url": "https://x/7"})

    executor._post_issue = fake_post
    incident = Incident(group_key="g", alertname="A", severity="critical", summary="s", started_at=utcnow(), labels={})
    result = asyncio.run(executor._create_issue(incident))
    assert result.ok and result.link == "https://x/7" and incident.issue_number == 7
    assert "labels" in sent[0] and "labels" not in sent[1]


def test_issue_body_is_built_from_report_not_progress_messages():
    from ops_agent.actions import issue_body
    from ops_agent.models import Incident, Step, utcnow

    incident = Incident(group_key="g", alertname="A", severity="critical", summary="s", started_at=utcnow(), labels={})
    incident.report = Report.model_validate({**REPORT, "hypotheses": REPORT["hypotheses"] + [
        {"cause": "네트워크 문제", "confidence": "low", "evidence": ["근거 없음"]}]})
    incident.steps = [Step("🧠 보고서 작성 중…"), Step("💬 중간 결론 " * 50)]
    incident.tool_log = [{"name": "search_logs", "args": {"query": '{a="b"} |= "x|y"'}}]
    body = issue_body(incident)

    assert "보고서 작성 중" not in body and "중간 결론" not in body
    assert "<summary>기타 가능성</summary>" in body and "- 네트워크 문제 (신뢰도 낮음)" in body
    assert "KST" in body
    assert "x\\|y" in body  # 표 안의 | 는 이스케이프


def test_progress_after_issue_is_commented(settings):
    async def scenario():
        executor = FakeExecutor()
        service, _ = make_service(settings, executor=executor)
        [iid] = await service.handle_alertmanager(alert_payload())
        await drain(service)
        await service.run_action(iid, "disable_fault", "alice")
        await drain(service)
        await service.handle_alertmanager(alert_payload("resolved"))
        await drain(service)
        return executor.comments

    comments = asyncio.run(scenario())
    assert comments[0].startswith("🧯 **장애 플래그 해제** — 승인자: alice")
    assert any(c.startswith("✅ 복구 확인") for c in comments)
    assert comments[-1].startswith("✅ **RESOLVED**")


def test_comment_issue_skipped_without_issue(settings):
    from dataclasses import replace

    from ops_agent.models import Incident, utcnow

    executor = ActionExecutor(replace(settings, github_token="t", github_repo="o/r"), http=None)
    calls = []

    async def fake_post(payload, path=""):
        calls.append(path)
        return 201, {}

    executor._post_issue = fake_post
    incident = Incident(group_key="g", alertname="A", severity="critical", summary="s", started_at=utcnow(), labels={})
    asyncio.run(executor.comment_issue(incident, "x"))
    incident.issue_number = 12
    asyncio.run(executor.comment_issue(incident, "x"))
    assert calls == ["/12/comments"]
