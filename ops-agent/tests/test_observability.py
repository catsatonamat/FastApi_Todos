import json

from ops_agent.observability import _compact_log, _log_kind, is_healthy


def test_log_kind_groups_errors_and_slow_requests():
    assert _log_kind({"level": "ERROR", "error_type": "StorageUnavailableError"}) == "ERROR StorageUnavailableError"
    assert _log_kind({"level": "WARNING", "route": "/todos", "duration_ms": 812.0}) == "WARNING slow request /todos (>500ms)"


def test_compact_log_keeps_only_useful_fields():
    line = json.dumps({"ts": "t", "level": "ERROR", "msg": "m", "logger": "todo", "method": "GET", "error_type": "X"})
    assert _compact_log(line) == {"ts": "t", "level": "ERROR", "msg": "m", "error_type": "X"}
    assert _compact_log("plain text") == {"msg": "plain text"}


def test_is_healthy_thresholds():
    assert is_healthy({"up": 1.0, "error_rate": 0.0, "p95_seconds": 0.01}) == (True, [])
    assert is_healthy({"up": 1.0, "error_rate": None, "p95_seconds": None})[0] is True  # 5xx 시리즈가 아직 없음
    ok, problems = is_healthy({"up": 0.0, "error_rate": 0.3, "p95_seconds": 0.9})
    assert not ok and len(problems) == 3
