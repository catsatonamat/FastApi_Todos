"""운영 지표(MTTD·조사 시간·MTTR)와 RCA 적중률.

- 정답(ground truth)은 조사 시점에 수집한 기본 증거에서 뽑는다: 런타임 설정의 장애 주입 모드·시각, up 지표.
  LLM의 판단과 무관하게 결정적으로 정해지므로 LLM 평가 기준으로 쓸 수 있다.
- 인시던트 기록은 JSONL로 남겨 에이전트가 재시작돼도 통계가 유지된다 (reset-demo.sh로 초기화).
"""
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from prometheus_client.core import GaugeMetricFamily

from .models import Incident

log = logging.getLogger("ops_agent.evaluation")

# 장애 유형별 정답 조치 (가장 덜 침습적인 조치를 정답으로 본다)
EXPECTED_ACTION = {
    "storage_error": "disable_fault",
    "slow_storage": "disable_fault",
    "down": "restart_app",
}


def judge(incident: Incident) -> None:
    """기본 증거에서 정답을 뽑고, 보고서의 첫 번째 권장 조치가 정답인지 판정한다."""
    config = incident.evidence.get("current_runtime_config") or {}
    metrics = incident.evidence.get("metrics") or {}
    if config.get("mode") in ("storage_error", "slow_storage"):
        incident.fault_mode = config["mode"]
        if config.get("since"):
            incident.fault_started_at = datetime.fromisoformat(config["since"])
    elif metrics.get("up") == 0:
        incident.fault_mode = "down"   # 중지 시각은 알 수 없어 MTTD는 계산하지 않는다

    expected = EXPECTED_ACTION.get(incident.fault_mode or "")
    remediations = [a for a in (incident.report.recommended_actions if incident.report else []) if a != "create_issue"]
    incident.rca_hit = (remediations[:1] == [expected]) if expected else None


def to_record(incident: Incident) -> dict:
    return {
        "id": incident.id,
        "alertname": incident.alertname,
        "llm": incident.llm_label,
        "fault_mode": incident.fault_mode,
        "rca_hit": incident.rca_hit,
        "fallback": incident.report_fallback,
        "tool_calls": incident.tool_calls,
        "recommended": incident.report.recommended_actions if incident.report else [],
        "status": incident.status,
        "mttd": incident.mttd,
        "investigation": incident.investigation_seconds,
        "mttr": incident.time_to_resolve,
    }


class IncidentStore:
    """인시던트 기록 (id별 최신 기록만 유지, JSONL에 누적 저장)."""

    def __init__(self, path: str):
        self.path = Path(path)
        self.records: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                    self.records[record["id"]] = record
                except (json.JSONDecodeError, KeyError):
                    continue

    def save(self, incident: Incident) -> None:
        record = to_record(incident)
        self.records[incident.id] = record
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            log.warning("인시던트 기록 저장 실패", extra={"error": str(e)})

    def summary(self) -> dict[str, dict]:
        """LLM별 + 전체('all') 요약."""
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in self.records.values():
            groups[r["llm"]].append(r)
            groups["all"].append(r)
        return {llm: _summarize(rs) for llm, rs in groups.items()}


def _avg(values: list) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return sum(nums) / len(nums) if nums else None


def _summarize(records: list[dict]) -> dict:
    judged = [r for r in records if r["rca_hit"] is not None]
    hits = sum(1 for r in judged if r["rca_hit"])
    return {
        "incidents": len(records),
        "mttd": _avg([r["mttd"] for r in records]),
        "investigation": _avg([r["investigation"] for r in records]),
        "mttr": _avg([r["mttr"] for r in records]),
        "judged": len(judged),
        "hits": hits,
        "hit_ratio": hits / len(judged) if judged else None,
    }


class KpiCollector:
    """GET /metrics 를 긁을 때마다 기록에서 KPI를 계산해 Prometheus 게이지로 내보낸다."""

    def __init__(self, store: IncidentStore):
        self.store = store

    def collect(self):
        specs = {
            "incidents": ("aiops_incidents", "처리한 인시던트 수"),
            "mttd": ("aiops_mttd_seconds", "평균 MTTD: 장애 시작 → 알림 수신(초)"),
            "investigation": ("aiops_investigation_seconds", "평균 조사 시간: 알림 수신 → 보고(초)"),
            "mttr": ("aiops_mttr_seconds", "평균 MTTR: 장애 시작 → 복구(초)"),
            "hit_ratio": ("aiops_rca_hit_ratio", "RCA 적중률: 첫 권장 조치가 정답인 비율"),
        }
        families = {k: GaugeMetricFamily(name, doc, labels=["llm"]) for k, (name, doc) in specs.items()}
        for llm, s in self.store.summary().items():
            for key, family in families.items():
                if s[key] is not None:
                    family.add_metric([llm], s[key])
        yield from families.values()
