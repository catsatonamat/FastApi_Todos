"""Discord 봇: 인시던트 보고(Embed) · 조사 과정(스레드) · 승인 버튼 · 슬래시 명령."""
import logging

import discord
from discord import app_commands

from .actions import ACTIONS, ActionResult
from .evaluation import EXPECTED_ACTION
from .config import Settings
from .models import Incident
from .service import OpsService

log = logging.getLogger("ops_agent.discord")

COLORS = {"critical": 0xE03E3E, "high": 0xF2711C, "medium": 0xF2C94C, "low": 0x4C9AFF, "warning": 0xF2C94C}
RESOLVED_COLOR = 0x2EB67D
SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵", "warning": "🟡"}
CONFIDENCE = {"high": "높음", "medium": "중간", "low": "낮음"}


# ---------- 승인 버튼 ----------
class ActionButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"aiops:(?P<incident>[0-9a-f]+):(?P<action>[a-z_]+)",
):
    """custom_id에 인시던트·조치 ID를 담아, 봇이 재시작돼도 버튼이 다시 연결된다."""

    def __init__(self, incident_id: str, action_id: str, *, style=discord.ButtonStyle.secondary, disabled=False):
        if action_id == "dismiss":
            label, emoji = "조치 안 함", "🙅"
        else:
            action = ACTIONS.get(action_id)
            label, emoji = (action.label, action.emoji) if action else (action_id, None)
        super().__init__(discord.ui.Button(
            label=label, emoji=emoji, style=style, disabled=disabled,
            custom_id=f"aiops:{incident_id}:{action_id}",
        ))
        self.incident_id = incident_id
        self.action_id = action_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["incident"], match["action"])

    async def callback(self, interaction: discord.Interaction) -> None:
        bot: OpsBot = interaction.client  # type: ignore[assignment]
        if not bot.is_approver(interaction.user):
            await interaction.response.send_message("⛔ 조치를 승인할 권한이 없습니다.", ephemeral=True)
            return
        actor = interaction.user.display_name
        # Discord는 3초 안에 응답을 요구하므로, 메시지 수정 등 느린 작업보다 응답을 먼저 보낸다
        if self.action_id == "dismiss":
            await interaction.response.send_message("확인했습니다 — 조치 없이 종료합니다.", ephemeral=True)
            if not await bot.service.dismiss(self.incident_id, actor):
                await interaction.followup.send("❌ 인시던트를 찾을 수 없습니다.", ephemeral=True)
            return
        await interaction.response.send_message(f"**{self.item.label}** 승인 — 실행합니다.", ephemeral=True)
        result = await bot.service.run_action(self.incident_id, self.action_id, actor)
        # 결과 전문은 인시던트 스레드에 남고, 누른 사람에게는 요약을 바로 보여준다
        summary = result.detail.split("\n", 1)[0]
        incident = bot.service.incidents.get(self.incident_id)
        where = f" — 자세한 내용은 <#{incident.thread_id}>" if incident and incident.thread_id else ""
        link = f"\n🔗 {result.link}" if result.link else ""
        await interaction.followup.send(f"{'✅' if result.ok else '❌'} {summary}{where}{link}", ephemeral=True)


def build_view(incident: Incident) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    closed = incident.status == "resolved" or incident.dismissed_by is not None
    recommended = set(incident.report.recommended_actions) if incident.report else set()
    for action_id in ACTIONS:
        done = action_id in incident.actions_done
        style = discord.ButtonStyle.success if action_id in recommended else discord.ButtonStyle.secondary
        view.add_item(ActionButton(incident.id, action_id, style=style, disabled=done or closed))
    view.add_item(ActionButton(incident.id, "dismiss", disabled=closed))
    return view


# ---------- Embed ----------
def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _metrics_line(metrics: dict) -> str:
    er, p95, rps = metrics.get("error_rate"), metrics.get("p95_seconds"), metrics.get("rps")
    return " · ".join([
        f"에러율 **{er:.1%}**" if isinstance(er, float) else "에러율 **0%**",
        f"p95 **{p95 * 1000:.0f}ms**" if isinstance(p95, float) else "p95 N/A",
        f"처리량 **{rps:.1f} req/s**" if isinstance(rps, float) else "처리량 N/A",
        f"up **{metrics.get('up'):g}**" if isinstance(metrics.get("up"), float) else f"up **{metrics.get('up')}**",
    ])


def _secs(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v / 60:.1f}분" if v >= 120 else f"{v:.0f}초"


def _timings(incident: Incident) -> str:
    parts = []
    if incident.mttd is not None:
        parts.append(f"MTTD **{_secs(incident.mttd)}**")
    if incident.investigation_seconds is not None:
        parts.append(f"조사 **{_secs(incident.investigation_seconds)}**")
    if incident.time_to_resolve is not None:
        parts.append(f"MTTR **{_secs(incident.time_to_resolve)}**")
    return " · ".join(parts)


def stats_embed(summary: dict[str, dict]) -> discord.Embed:
    embed = discord.Embed(title="📏 AIOps 운영 지표", color=0x4C9AFF)
    if not summary:
        embed.description = "아직 기록된 인시던트가 없습니다."
        return embed
    for llm in sorted(summary, key=lambda k: (k != "all", k)):
        s = summary[llm]
        ratio = f"{s['hit_ratio']:.0%} ({s['hits']}/{s['judged']})" if s["hit_ratio"] is not None else "-"
        embed.add_field(
            name="전체" if llm == "all" else llm,
            value=(f"인시던트 **{s['incidents']}건**\n"
                   f"평균 MTTD **{_secs(s['mttd'])}** · 조사 **{_secs(s['investigation'])}** · MTTR **{_secs(s['mttr'])}**\n"
                   f"RCA 적중률 **{ratio}**"),
            inline=False,
        )
    embed.set_footer(text="MTTD: 장애 주입→알림 수신 · 조사: 알림→보고서 · MTTR: 장애 주입→복구 · 적중: 첫 권장 조치가 정답")
    return embed


def opening_embed(incident: Incident) -> discord.Embed:
    embed = discord.Embed(
        title=f"{SEVERITY_EMOJI.get(incident.severity, '⚪')} {incident.alertname}",
        description=f"{incident.summary}\n\n🔎 AI 에이전트가 지표·로그를 조사하고 있습니다. 진행 상황은 스레드에서 확인하세요.",
        color=COLORS.get(incident.severity, 0x999999),
        timestamp=incident.started_at,
    )
    embed.set_footer(text=f"Incident {incident.id}")
    return embed


def report_embed(incident: Incident) -> discord.Embed:
    r = incident.report
    resolved = incident.status == "resolved"
    title = f"{SEVERITY_EMOJI.get(r.severity, '⚪')} [{r.severity.upper()}] {incident.alertname}"
    embed = discord.Embed(
        title=_clip(("✅ RESOLVED · " if resolved else "") + title, 256),
        description=_clip(f"{r.summary}\n\n**영향**\n{r.impact}", 4096),
        color=RESOLVED_COLOR if resolved else COLORS.get(r.severity, 0x999999),
        timestamp=incident.started_at,
    )
    if metrics := incident.evidence.get("metrics"):
        embed.add_field(name="📊 알림 시점 지표", value=_metrics_line(metrics), inline=False)
    for i, h in enumerate(r.hypotheses[:3], 1):
        evidence = "\n".join(f"• {e}" for e in h.evidence) or "-"
        embed.add_field(
            name=_clip(f"원인 후보 {i}: {h.cause}", 256),
            value=_clip(f"신뢰도: **{CONFIDENCE[h.confidence]}**\n{evidence}", 1024),
            inline=False,
        )
    if r.recommended_actions:
        lines = [f"{ACTIONS[a].emoji} **{ACTIONS[a].label}** — {ACTIONS[a].description}" for a in r.recommended_actions]
        embed.add_field(name="🛠 권장 조치 (승인 필요)", value=_clip("\n".join(lines), 1024), inline=False)
    if r.next_steps:
        embed.add_field(name="👀 추가 확인", value=_clip("\n".join(f"• {s}" for s in r.next_steps), 1024), inline=False)
    if timings := _timings(incident):
        embed.add_field(name="⏱ 운영 지표", value=timings, inline=False)
    if resolved and incident.rca_hit is not None:
        expected = EXPECTED_ACTION[incident.fault_mode]
        verdict = "🎯 적중" if incident.rca_hit else "❌ 빗나감"
        embed.add_field(
            name="RCA 판정 (데모 평가)",
            value=f"{verdict} — 주입된 장애 `{incident.fault_mode}`, 정답 조치 {ACTIONS[expected].label}",
            inline=False,
        )
    if incident.actions_done:
        embed.add_field(name="실행된 조치", value=", ".join(ACTIONS[a].label for a in incident.actions_done), inline=True)

    footer = [f"Incident {incident.id}", incident.llm_label, f"도구 호출 {incident.tool_calls}회"]
    if incident.time_to_report is not None:
        footer.append(f"알림→보고 {incident.time_to_report:.0f}초")
    if incident.report_fallback:
        footer.append("⚠️ LLM 실패: 기본 증거만")
    embed.set_footer(text=" · ".join(footer))
    return embed


# ---------- Notifier ----------
class DiscordNotifier:
    def __init__(self, bot: "OpsBot"):
        self.bot = bot

    async def _channel(self) -> discord.abc.Messageable:
        cid = self.bot.settings.discord_channel_id
        return self.bot.get_channel(cid) or await self.bot.fetch_channel(cid)

    async def _thread(self, incident: Incident) -> discord.abc.Messageable:
        if incident.thread_id:
            return self.bot.get_channel(incident.thread_id) or await self.bot.fetch_channel(incident.thread_id)
        return await self._channel()

    async def _send(self, incident: Incident, text: str) -> None:
        target = await self._thread(incident)
        for i in range(0, len(text), 1900):
            await target.send(text[i : i + 1900])

    async def _edit_main(self, incident: Incident, **kwargs) -> None:
        if not incident.message_id:
            return
        channel = await self._channel()
        await channel.get_partial_message(incident.message_id).edit(**kwargs)

    async def _refresh(self, incident: Incident, **kwargs) -> None:
        """채널 원본 메시지와 스레드 안 버튼 메시지의 버튼 상태를 함께 맞춘다."""
        await self._edit_main(incident, embed=report_embed(incident), view=build_view(incident), **kwargs)
        if incident.controls_message_id and incident.thread_id:
            thread = await self._thread(incident)
            await thread.get_partial_message(incident.controls_message_id).edit(view=build_view(incident))

    async def incident_opened(self, incident: Incident) -> None:
        await self.bot.wait_until_ready()
        channel = await self._channel()
        message = await channel.send(
            content=f"🚨 **{incident.alertname}** 알림 수신 — AI 에이전트가 조사를 시작합니다.",
            embed=opening_embed(incident),
        )
        incident.message_id = message.id
        try:
            thread = await message.create_thread(
                name=_clip(f"🔎 {incident.alertname} · {incident.id}", 100), auto_archive_duration=1440
            )
            incident.thread_id = thread.id
        except discord.HTTPException as e:
            log.warning("스레드를 만들 수 없어 채널에 진행 상황을 남깁니다", extra={"error": str(e)})

    async def step(self, incident: Incident, text: str) -> None:
        await self._send(incident, text)

    async def report_ready(self, incident: Incident) -> None:
        mention = f"<@&{role.id}> " if (role := self.bot.approver_role()) else ""
        await self._edit_main(
            incident,
            content=f"{mention}🚨 **{incident.alertname}** — 분석 완료. 조치를 승인해 주세요.",
            embed=report_embed(incident),
            view=build_view(incident),
        )
        took = f" (알림→보고 {incident.time_to_report:.0f}초)" if incident.time_to_report is not None else ""
        # 스레드 상단의 원본 미리보기에서는 버튼이 동작하지 않으므로 스레드 안에도 버튼을 둔다
        thread = await self._thread(incident)
        controls = await thread.send(
            f"📋 보고서를 작성했습니다{took}. 아래 버튼(또는 채널의 원본 메시지)으로 조치를 승인하세요.",
            view=build_view(incident),
        )
        if incident.thread_id:
            incident.controls_message_id = controls.id

    async def action_done(self, incident: Incident, action_id: str, actor: str, result: ActionResult) -> None:
        action = ACTIONS[action_id]
        status = "✅" if result.ok else "❌"
        text = f"{action.emoji} **{action.label}** — 승인자: **{actor}**\n{status} {result.detail}"
        if result.link:
            text += f"\n🔗 {result.link}"
        await self._send(incident, text)
        await self._refresh(incident)

    async def note(self, incident: Incident, text: str) -> None:
        await self._send(incident, text)
        if incident.dismissed_by and incident.report:
            await self._refresh(incident)

    async def resolved(self, incident: Incident) -> None:
        mttr = f" — MTTR **{incident.time_to_resolve:.0f}초**" if incident.time_to_resolve is not None else ""
        await self._send(incident, f"✅ **RESOLVED**: Alertmanager가 `{incident.alertname}` 해제를 알렸습니다{mttr}")
        if incident.report:
            await self._refresh(incident, content=f"✅ **{incident.alertname}** 복구됨")


# ---------- Bot ----------
class OpsBot(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.service: OpsService  # __main__에서 주입
        register_commands(self)

    async def setup_hook(self) -> None:
        self.add_dynamic_items(ActionButton)
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()  # 전역 등록은 반영까지 시간이 걸릴 수 있다

    async def on_ready(self) -> None:
        log.info(f"Discord 로그인: {self.user}", extra={"channel": self.settings.discord_channel_id})

    def approver_role(self) -> discord.Role | None:
        if not self.settings.discord_approver_role:
            return None
        channel = self.get_channel(self.settings.discord_channel_id)
        guild = getattr(channel, "guild", None)
        return discord.utils.get(guild.roles, name=self.settings.discord_approver_role) if guild else None

    def is_approver(self, user: discord.abc.User) -> bool:
        s = self.settings
        if not s.discord_approver_ids and not s.discord_approver_role:
            return True  # 제한을 설정하지 않으면 채널의 누구나 승인 가능 (데모 기본값)
        if user.id in s.discord_approver_ids:
            return True
        roles = getattr(user, "roles", [])
        return any(r.name == s.discord_approver_role for r in roles)


def register_commands(bot: OpsBot) -> None:
    @bot.tree.command(name="status", description="Todo API 핵심 지표를 지금 조회합니다")
    async def status(interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        snap = await bot.service.obs.snapshot()
        health = await bot.service.obs.app_health()
        alerts = snap.get("firing_alerts") or []
        embed = discord.Embed(
            title="📊 Todo API 상태",
            description=_metrics_line(snap),
            color=COLORS["critical"] if alerts else RESOLVED_COLOR,
        )
        embed.add_field(name="/health", value=f"HTTP {health.get('status_code')} ({health.get('latency_ms', '-')}ms)")
        embed.add_field(name="발생 중인 알림", value=", ".join(alerts) or "없음")
        await interaction.followup.send(embed=embed)

    @bot.tree.command(name="stats", description="MTTD · 조사 시간 · MTTR · RCA 적중률 (LLM별)")
    async def stats(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=stats_embed(bot.service.store.summary()))

    @bot.tree.command(name="incidents", description="최근 인시던트 목록")
    async def incidents(interaction: discord.Interaction) -> None:
        recent = bot.service.recent(5)
        if not recent:
            await interaction.response.send_message("기록된 인시던트가 없습니다.", ephemeral=True)
            return
        lines = []
        for i in recent:
            mttr = f" · MTTR {i.time_to_resolve:.0f}s" if i.time_to_resolve is not None else ""
            actions = f" · 조치: {', '.join(i.actions_done)}" if i.actions_done else ""
            lines.append(f"`{i.id}` **{i.alertname}** — {i.status}{mttr}{actions}")
        await interaction.response.send_message("\n".join(lines))

    chaos = app_commands.Group(name="chaos", description="(데모) Todo API에 장애를 주입하거나 해제합니다")

    async def apply(interaction: discord.Interaction, config: dict, label: str) -> None:
        if not bot.is_approver(interaction.user):
            await interaction.response.send_message("⛔ 권한이 없습니다.", ephemeral=True)
            return
        await interaction.response.defer()  # 앱 API 호출이 3초를 넘을 수 있으므로 응답을 미뤄 둔다
        result = await bot.service.executor.set_chaos(config, interaction.user.display_name)
        mark = "🧪" if result.ok else "❌"
        await interaction.followup.send(f"{mark} {label} — {result.detail}")

    @chaos.command(name="error", description="저장소 I/O 오류 주입 (요청 일부가 503)")
    @app_commands.describe(percent="실패 확률 (%)")
    async def chaos_error(interaction: discord.Interaction, percent: app_commands.Range[int, 1, 100]) -> None:
        await apply(interaction, {"mode": "storage_error", "error_rate": percent / 100}, f"저장소 오류 **{percent}%** 주입")

    @chaos.command(name="slow", description="저장소 접근 지연 주입")
    @app_commands.describe(ms="지연 시간 (ms)")
    async def chaos_slow(interaction: discord.Interaction, ms: app_commands.Range[int, 100, 10000]) -> None:
        await apply(interaction, {"mode": "slow_storage", "latency_ms": ms}, f"저장소 지연 **{ms}ms** 주입")

    @chaos.command(name="off", description="장애 주입 해제")
    async def chaos_off(interaction: discord.Interaction) -> None:
        await apply(interaction, {"mode": "off"}, "장애 주입 **해제**")

    bot.tree.add_command(chaos)
