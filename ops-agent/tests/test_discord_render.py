import asyncio
from datetime import datetime, timezone

import discord

from conftest import REPORT
from ops_agent.config import Settings
from ops_agent.discord_bot import OpsBot, build_view, report_embed
from ops_agent.models import Incident, Report


def reported_incident(**kw) -> Incident:
    incident = Incident(
        group_key="g", alertname="TodoHighErrorRate", severity="critical",
        summary="s", started_at=datetime.now(timezone.utc), labels={}, **kw,
    )
    incident.report = Report.model_validate(REPORT)
    incident.evidence = {"metrics": {"error_rate": 0.42, "p95_seconds": 0.02, "rps": 5.0, "up": 1}}
    incident.reported_at = datetime.now(timezone.utc)
    return incident


def test_report_embed_has_core_fields():
    embed = report_embed(reported_incident())
    names = [f.name for f in embed.fields]
    assert embed.title.startswith("🔴 [CRITICAL]")
    assert any(n.startswith("원인 후보 1") for n in names)
    assert "🛠 권장 조치 (승인 필요)" in names
    assert "42.0%" in embed.fields[0].value
    assert "알림→보고" in embed.footer.text


def test_resolved_embed_shows_mttr():
    incident = reported_incident()
    incident.status = "resolved"
    incident.resolved_at = datetime.now(timezone.utc)
    embed = report_embed(incident)
    assert embed.title.startswith("✅ RESOLVED")
    timings = next(f.value for f in embed.fields if f.name == "⏱ 운영 지표")
    assert "MTTR" in timings and "조사" in timings


def test_view_highlights_recommended_and_disables_done():
    async def build():
        incident = reported_incident()
        incident.actions_done.append("restart_app")
        return {b.custom_id: b.item for b in build_view(incident).children}

    buttons = asyncio.run(build())
    iid = next(iter(buttons)).split(":")[1]
    assert buttons[f"aiops:{iid}:disable_fault"].style == discord.ButtonStyle.success
    assert buttons[f"aiops:{iid}:restart_app"].disabled is True
    assert buttons[f"aiops:{iid}:create_issue"].style == discord.ButtonStyle.secondary
    assert f"aiops:{iid}:dismiss" in buttons


def test_approver_rules():
    class User:
        def __init__(self, id, roles=()):
            self.id = id
            self.roles = [type("R", (), {"name": r}) for r in roles]

    async def check():
        open_bot = OpsBot(Settings())
        strict = OpsBot(Settings(discord_approver_ids=frozenset({1}), discord_approver_role="oncall"))
        return (open_bot.is_approver(User(9)), strict.is_approver(User(1)),
                strict.is_approver(User(2, ["oncall"])), strict.is_approver(User(3, ["dev"])))

    assert asyncio.run(check()) == (True, True, True, False)
