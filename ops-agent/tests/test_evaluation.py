import asyncio
from datetime import timedelta

from conftest import REPORT
from ops_agent.discord_bot import stats_embed
from ops_agent.evaluation import IncidentStore, KpiCollector, judge
from ops_agent.models import Incident, Report, utcnow


def incident(mode=None, since=None, up=1.0, actions=("disable_fault",), llm="ollama/qwen3:8b") -> Incident:
    now = utcnow()
    inc = Incident(group_key="g", alertname="A", severity="critical", summary="s",
                   started_at=now - timedelta(seconds=20), labels={})
    inc.llm_label = llm
    inc.opened_at = now
    inc.reported_at = now + timedelta(seconds=60)
    inc.evidence = {"metrics": {"up": up},
                    "current_runtime_config": {"mode": mode, "since": since} if mode else {"error": "연결 실패"}}
    inc.report = Report.model_validate({**REPORT, "recommended_actions": list(actions)})
    return inc


def test_judge_hit_for_injected_fault():
    since = (utcnow() - timedelta(seconds=45)).isoformat()
    inc = incident("storage_error", since)
    judge(inc)
    assert inc.fault_mode == "storage_error" and inc.rca_hit is True
    assert 44 <= inc.mttd <= 46
    assert inc.investigation_seconds == 60


def test_judge_miss_when_first_remediation_is_wrong():
    inc = incident("slow_storage", utcnow().isoformat(), actions=("create_issue", "restart_app", "disable_fault"))
    judge(inc)
    assert inc.rca_hit is False  # create_issue는 건너뛰고 첫 실제 조치(restart_app)로 판정


def test_judge_down_and_unknown():
    down = incident(up=0.0, actions=("restart_app",))
    judge(down)
    assert down.fault_mode == "down" and down.rca_hit is True and down.mttd is None

    unknown = incident()
    judge(unknown)
    assert unknown.fault_mode is None and unknown.rca_hit is None


def test_mttr_measured_from_fault_start():
    since = utcnow() - timedelta(seconds=100)
    inc = incident("storage_error", since.isoformat())
    judge(inc)
    inc.resolved_at = since + timedelta(seconds=300)
    assert inc.time_to_resolve == 300


def test_store_summary_by_llm_and_persistence(tmp_path):
    path = tmp_path / "incidents.jsonl"
    store = IncidentStore(str(path))
    for llm, hit_mode in [("ollama/qwen3:8b", "storage_error"), ("ollama/qwen3:8b", "slow_storage"), ("gemini/x", "storage_error")]:
        inc = incident(hit_mode, utcnow().isoformat(), llm=llm,
                       actions=("restart_app",) if hit_mode == "slow_storage" else ("disable_fault",))
        judge(inc)
        store.save(inc)
        store.save(inc)  # 같은 인시던트를 여러 번 저장해도 한 건으로 센다

    summary = IncidentStore(str(path)).summary()  # 파일에서 다시 읽어도 같다
    assert summary["all"]["incidents"] == 3
    assert summary["ollama/qwen3:8b"]["hit_ratio"] == 0.5
    assert summary["gemini/x"]["hits"] == 1


def test_kpi_collector_exports_gauges(tmp_path):
    store = IncidentStore(str(tmp_path / "i.jsonl"))
    inc = incident("storage_error", utcnow().isoformat())
    judge(inc)
    store.save(inc)
    families = {f.name: f for f in KpiCollector(store).collect()}
    ratio = families["aiops_rca_hit_ratio"].samples
    assert {s.labels["llm"]: s.value for s in ratio} == {"ollama/qwen3:8b": 1.0, "all": 1.0}
    assert families["aiops_mttr_seconds"].samples == []  # 아직 복구 전


def test_stats_embed_lists_all_first():
    store_summary = {"gemini/x": {"incidents": 1, "mttd": 40.0, "investigation": 70.0, "mttr": 200.0,
                                  "judged": 1, "hits": 1, "hit_ratio": 1.0},
                     "all": {"incidents": 1, "mttd": 40.0, "investigation": 70.0, "mttr": 200.0,
                             "judged": 1, "hits": 1, "hit_ratio": 1.0}}
    embed = stats_embed(store_summary)
    assert embed.fields[0].name == "전체"
    assert "100% (1/1)" in embed.fields[0].value and "3.3분" in embed.fields[0].value
