"""인시던트·보고서 데이터 모델."""
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ActionId = Literal["disable_fault", "restart_app", "create_issue"]


class Hypothesis(BaseModel):
    cause: str = Field(description="원인 후보 (한 문장)")
    confidence: Literal["high", "medium", "low"]
    evidence: list[str] = Field(description="이 가설을 뒷받침하는 구체적 근거 (지표 값, 로그 메시지 등)")


class Report(BaseModel):
    summary: str = Field(description="장애 상황 요약 (1~2문장, 수치 포함)")
    severity: Literal["critical", "high", "medium", "low"]
    impact: str = Field(description="사용자·서비스 영향")
    hypotheses: list[Hypothesis] = Field(description="가능성 높은 순 원인 후보 1~3개")
    recommended_actions: list[ActionId] = Field(description="권장 조치 ID (사람 승인 후 실행됨)")
    next_steps: list[str] = Field(description="사람이 추가로 확인할 사항")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Step:
    """에이전트 조사 과정의 한 단계 (도구 호출 등). Discord 스레드에 그대로 표시된다."""
    text: str
    at: datetime = field(default_factory=utcnow)


@dataclass
class Incident:
    group_key: str
    alertname: str
    severity: str
    summary: str
    started_at: datetime
    labels: dict
    id: str = field(default_factory=lambda: secrets.token_hex(3))
    status: Literal["investigating", "reported", "resolved"] = "investigating"
    opened_at: datetime = field(default_factory=utcnow)
    reported_at: datetime | None = None
    resolved_at: datetime | None = None
    evidence: dict = field(default_factory=dict)
    report: Report | None = None
    report_fallback: bool = False      # LLM 실패로 기본 증거만 보고했는지
    llm_label: str = ""
    tool_calls: int = 0
    tool_log: list[dict] = field(default_factory=list)   # 실행한 도구 호출 [{"name", "args"}] (이슈 기록용)
    steps: list[Step] = field(default_factory=list)
    actions_done: list[str] = field(default_factory=list)
    dismissed_by: str | None = None
    # Discord 메시지 위치 (DiscordNotifier가 채운다)
    message_id: int | None = None
    thread_id: int | None = None
    controls_message_id: int | None = None   # 스레드 안의 승인 버튼 메시지
    # 생성된 GitHub 이슈 (이후 진행 상황을 댓글로 남긴다)
    issue_number: int | None = None
    # 평가용 정답 (주입된 장애). evaluation.judge()가 채운다
    fault_mode: str | None = None            # storage_error | slow_storage | down
    fault_started_at: datetime | None = None
    rca_hit: bool | None = None              # 권장 조치가 정답과 맞는지 (판단 불가면 None)

    @property
    def time_to_report(self) -> float | None:
        """알림 발생(startsAt) → 보고서 전달까지 걸린 시간(초)."""
        return (self.reported_at - self.started_at).total_seconds() if self.reported_at else None

    @property
    def mttd(self) -> float | None:
        """MTTD: 장애 시작(장애 주입 시각) → 에이전트가 알림을 받은 시각(초)."""
        return (self.opened_at - self.fault_started_at).total_seconds() if self.fault_started_at else None

    @property
    def investigation_seconds(self) -> float | None:
        """조사 시간: 알림 수신 → 보고서 완성(초)."""
        return (self.reported_at - self.opened_at).total_seconds() if self.reported_at else None

    @property
    def time_to_resolve(self) -> float | None:
        """MTTR: 장애 시작 → 복구(초). 장애 시작 시각을 모르면 알림 발생 시각부터 잰다."""
        if not self.resolved_at:
            return None
        return (self.resolved_at - (self.fault_started_at or self.started_at)).total_seconds()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "alertname": self.alertname,
            "severity": self.severity,
            "status": self.status,
            "summary": self.summary,
            "started_at": self.started_at.isoformat(),
            "time_to_report": self.time_to_report,
            "time_to_resolve": self.time_to_resolve,
            "tool_calls": self.tool_calls,
            "actions_done": self.actions_done,
            "report": self.report.model_dump() if self.report else None,
            "report_fallback": self.report_fallback,
            "steps": [s.text for s in self.steps],
        }
