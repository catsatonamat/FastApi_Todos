"""인시던트 수명주기 오케스트레이션.

알림 수신 → 인시던트 생성 → 조사 → 보고 → (승인) 조치 → 복구 검증 → resolved
"""
import asyncio
import logging
from datetime import datetime
from typing import Protocol

from .actions import ACTIONS, ActionExecutor, ActionResult
from .agent import Investigator
from .config import Settings
from .evaluation import IncidentStore, judge
from .models import Incident, Step, utcnow
from .observability import Observability, is_healthy

log = logging.getLogger("ops_agent.service")

# 이 조치가 성공하면 복구 여부를 다시 확인한다
REMEDIATIONS = {"disable_fault", "restart_app"}


class Notifier(Protocol):
    async def incident_opened(self, incident: Incident) -> None: ...
    async def step(self, incident: Incident, text: str) -> None: ...
    async def report_ready(self, incident: Incident) -> None: ...
    async def action_done(self, incident: Incident, action_id: str, actor: str, result: ActionResult) -> None: ...
    async def note(self, incident: Incident, text: str) -> None: ...
    async def resolved(self, incident: Incident) -> None: ...


class ConsoleNotifier:
    """Discord 없이 실행할 때: 모든 이벤트를 로그로 남긴다."""

    async def incident_opened(self, incident: Incident) -> None:
        log.warning(f"[{incident.id}] 🚨 {incident.alertname}: {incident.summary}")

    async def step(self, incident: Incident, text: str) -> None:
        log.info(f"[{incident.id}] {text}")

    async def report_ready(self, incident: Incident) -> None:
        log.warning(f"[{incident.id}] 📋 보고서", extra={"report": incident.report.model_dump()})

    async def action_done(self, incident: Incident, action_id: str, actor: str, result: ActionResult) -> None:
        log.warning(f"[{incident.id}] {action_id} by {actor}: ok={result.ok} {result.detail}")

    async def note(self, incident: Incident, text: str) -> None:
        log.info(f"[{incident.id}] {text}")

    async def resolved(self, incident: Incident) -> None:
        log.warning(f"[{incident.id}] ✅ resolved (MTTR {incident.time_to_resolve:.0f}s)")


class OpsService:
    def __init__(
        self,
        settings: Settings,
        obs: Observability,
        investigator: Investigator,
        executor: ActionExecutor,
        notifier: Notifier | None = None,
        store: IncidentStore | None = None,
    ):
        self.s = settings
        self.store = store or IncidentStore(settings.incident_log)
        self.obs = obs
        self.investigator = investigator
        self.executor = executor
        self.notifier: Notifier = notifier or ConsoleNotifier()
        self.incidents: dict[str, Incident] = {}
        self._active: dict[str, str] = {}      # group_key → incident id (firing 중인 것만)
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    # ---------- 알림 수신 ----------
    async def handle_alertmanager(self, payload: dict) -> list[str]:
        """Alertmanager 웹훅 페이로드를 처리하고 새로 만든 인시던트 id를 반환한다."""
        group_key = payload.get("groupKey", "")
        alerts = payload.get("alerts", [])
        firing = [a for a in alerts if a.get("status") == "firing"]

        async with self._lock:
            existing = self.incidents.get(self._active.get(group_key, ""))
            if payload.get("status") == "resolved" or not firing:
                if existing:
                    self._active.pop(group_key, None)
                    self._spawn(self._resolve(existing))
                return []
            if existing:
                return []  # 같은 그룹의 반복 알림 → 이미 조사 중

            common = payload.get("commonLabels", {})
            annotations = payload.get("commonAnnotations") or firing[0].get("annotations", {})
            incident = Incident(
                group_key=group_key,
                alertname=common.get("alertname") or firing[0]["labels"].get("alertname", "unknown"),
                severity=common.get("severity", "warning"),
                summary=annotations.get("summary", ""),
                started_at=min(_parse_time(a.get("startsAt")) for a in firing),
                labels=common,
            )
            self.incidents[incident.id] = incident
            self._active[group_key] = incident.id

        log.warning("incident opened", extra={"incident": incident.id, "alertname": incident.alertname})
        self._spawn(self._investigate(incident))
        return [incident.id]

    async def _investigate(self, incident: Incident) -> None:
        await self.notifier.incident_opened(incident)

        async def on_step(text: str) -> None:
            incident.steps.append(Step(text))
            await self.notifier.step(incident, text)

        await self.investigator.investigate(incident, on_step)
        incident.status = "reported" if incident.status != "resolved" else incident.status
        incident.reported_at = utcnow()
        judge(incident)
        self.store.save(incident)
        log.info("incident reported", extra={
            "incident": incident.id, "llm": incident.llm_label, "fault_mode": incident.fault_mode,
            "rca_hit": incident.rca_hit, "mttd_seconds": incident.mttd,
            "investigation_seconds": incident.investigation_seconds,
            "recommended": incident.report.recommended_actions if incident.report else [],
        })
        await self.notifier.report_ready(incident)

    async def _resolve(self, incident: Incident) -> None:
        incident.status = "resolved"
        incident.resolved_at = utcnow()
        self.store.save(incident)
        log.warning("incident resolved", extra={"incident": incident.id, "mttr_seconds": incident.time_to_resolve})
        await self.notifier.resolved(incident)
        mttr = f" (MTTR {incident.time_to_resolve:.0f}초)" if incident.time_to_resolve is not None else ""
        await self.executor.comment_issue(
            incident, f"✅ **RESOLVED** — Alertmanager가 `{incident.alertname}` 해제를 알렸습니다{mttr}. "
                      "근본 원인 후속 조치가 끝나면 이슈를 닫아 주세요.")

    # ---------- 승인형 조치 ----------
    async def run_action(self, incident_id: str, action_id: str, actor: str) -> ActionResult:
        incident = self.incidents.get(incident_id)
        if incident is None:
            return ActionResult(False, "인시던트를 찾을 수 없습니다 (에이전트가 재시작되었을 수 있음).")
        if action_id not in ACTIONS:
            log.warning("rejected action", extra={"incident": incident_id, "action": action_id, "actor": actor})
            return ActionResult(False, f"허용되지 않은 조치: {action_id}")
        async with self._lock:
            if action_id in incident.actions_done:
                return ActionResult(False, "이미 실행된 조치입니다.")
            result = await self.executor.run(action_id, incident, actor)
            if result.ok:
                incident.actions_done.append(action_id)

        await self.notifier.action_done(incident, action_id, actor, result)
        if action_id != "create_issue":
            mark = "✅" if result.ok else "❌"
            await self.executor.comment_issue(
                incident, f"{ACTIONS[action_id].emoji} **{ACTIONS[action_id].label}** — 승인자: {actor}\n\n{mark} {result.detail}")
        if result.ok and action_id in REMEDIATIONS:
            self._spawn(self._verify(incident, action_id))
        return result

    async def dismiss(self, incident_id: str, actor: str) -> bool:
        incident = self.incidents.get(incident_id)
        if incident is None:
            return False
        incident.dismissed_by = actor
        self.executor.audit(incident, "dismiss", actor, ActionResult(True, "조치 없이 종료"))
        await self.notifier.note(incident, f"🙅 {actor} 님이 조치하지 않기로 했습니다.")
        return True

    async def _verify(self, incident: Incident, action_id: str) -> None:
        """조치 후 일정 시간 기다렸다가 지표를 다시 조회해 복구 여부를 판단한다 (폐루프)."""
        await self.notifier.note(incident, f"⏳ {int(self.s.verify_delay)}초 후 복구 여부를 검증합니다…")
        await asyncio.sleep(self.s.verify_delay)
        snap = await self.obs.snapshot(window="30s")
        healthy, problems = is_healthy(snap)
        er = snap.get("error_rate")
        p95 = snap.get("p95_seconds")
        metrics = (
            f"에러율 {er:.1%}" if isinstance(er, float) else "에러율 0%",
            f"p95 {p95 * 1000:.0f}ms" if isinstance(p95, float) else "p95 N/A",
            f"up={snap.get('up'):g}" if isinstance(snap.get("up"), float) else f"up={snap.get('up')}",
        )
        if healthy:
            text = f"✅ 복구 확인 ({ACTIONS[action_id].label} 후): " + ", ".join(metrics)
        else:
            text = f"⚠️ 아직 비정상 ({', '.join(problems)}) — 다른 조치를 검토하세요."
        log.info("verification", extra={"incident": incident.id, "healthy": healthy, "snapshot": snap})
        await self.notifier.note(incident, text)
        await self.executor.comment_issue(incident, text)

    # ---------- 유틸 ----------
    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_done)

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("background task failed", exc_info=task.exception())

    def recent(self, limit: int = 10) -> list[Incident]:
        return sorted(self.incidents.values(), key=lambda i: i.opened_at, reverse=True)[:limit]


def _parse_time(value: str | None) -> datetime:
    if not value:
        return utcnow()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
