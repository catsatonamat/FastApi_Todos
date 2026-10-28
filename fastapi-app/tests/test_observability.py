import json
import logging

import pytest
from fastapi.testclient import TestClient

import chaos
import main
from main import app, save_todos

client = TestClient(app)
TOKEN = "test-token"
ADMIN = {"X-Admin-Token": TOKEN}


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "todo.json")
    monkeypatch.setattr(main, "CHAOS_ADMIN_TOKEN", TOKEN)
    chaos.reset()
    save_todos([])
    yield
    chaos.reset()


def metric_value(name, **labels):
    """/metrics 응답에서 이름·라벨이 일치하는 샘플 값을 찾는다 (없으면 0)."""
    for line in client.get("/metrics").text.splitlines():
        if line.startswith(name + "{") and all(f'{k}="{v}"' in line for k, v in labels.items()):
            return float(line.rsplit(" ", 1)[1])
    return 0.0


# ---------- 메트릭 ----------

def test_metrics_endpoint_exposes_prometheus_format():
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "http_requests_total" in response.text
    assert "http_request_duration_seconds_bucket" in response.text


def test_request_counter_uses_route_template():
    save_todos([{"id": 7, "title": "A", "order": 0}])
    before = metric_value("http_requests_total", method="PUT", route="/todos/{todo_id}", status="200")
    client.put("/todos/7", json={"title": "B"})
    after = metric_value("http_requests_total", method="PUT", route="/todos/{todo_id}", status="200")
    assert after == before + 1


def test_metrics_endpoint_is_not_counted():
    client.get("/metrics")
    assert metric_value("http_requests_total", route="/metrics") == 0


# ---------- 구조화 로그 ----------

def test_access_log_has_request_fields(caplog):
    client.get("/todos")
    record = next(r for r in caplog.records if getattr(r, "route", None) == "/todos")
    assert record.levelname == "INFO"
    assert record.status == 200
    assert record.duration_ms >= 0


def test_json_formatter_includes_extra_fields():
    record = logging.makeLogRecord({"msg": "hello", "levelname": "ERROR", "name": "todo", "error_type": "X"})
    entry = json.loads(main.log.handlers[0].formatter.format(record))
    assert entry["msg"] == "hello"
    assert entry["error_type"] == "X"


# ---------- 헬스체크 ----------

def test_health_ok():
    assert client.get("/health").json() == {"status": "ok"}


def test_health_fails_when_storage_unavailable():
    chaos.configure(chaos.ChaosConfig(mode="storage_error", error_rate=1.0))
    assert client.get("/health").status_code == 503


# ---------- 장애 주입 ----------

def test_chaos_api_disabled_without_token(monkeypatch):
    monkeypatch.setattr(main, "CHAOS_ADMIN_TOKEN", "")
    assert client.get("/admin/chaos", headers=ADMIN).status_code == 404


def test_chaos_api_rejects_wrong_token():
    assert client.put("/admin/chaos", json={"mode": "off"}, headers={"X-Admin-Token": "nope"}).status_code == 401
    assert client.get("/admin/chaos").status_code == 401


def test_chaos_api_roundtrip():
    response = client.put("/admin/chaos", json={"mode": "storage_error", "error_rate": 0.3}, headers=ADMIN)
    assert response.status_code == 200
    state = client.get("/admin/chaos", headers=ADMIN).json()
    assert state["mode"] == "storage_error"
    assert state["error_rate"] == 0.3
    assert state["since"] is not None


def test_chaos_api_validates_input():
    response = client.put("/admin/chaos", json={"mode": "storage_error", "error_rate": 2}, headers=ADMIN)
    assert response.status_code == 422


def test_storage_error_returns_503_and_counts():
    client.put("/admin/chaos", json={"mode": "storage_error", "error_rate": 1.0}, headers=ADMIN)
    before = metric_value("todo_storage_errors_total", op="reading")
    response = client.get("/todos")
    assert response.status_code == 503
    assert metric_value("todo_storage_errors_total", op="reading") == before + 1
    assert metric_value("http_requests_total", route="/todos", status="503") >= 1


def test_storage_error_is_logged(caplog):
    chaos.configure(chaos.ChaosConfig(mode="storage_error", error_rate=1.0))
    client.get("/todos")
    errors = [r for r in caplog.records if getattr(r, "error_type", None) == "StorageUnavailableError"]
    assert errors and errors[0].levelname == "ERROR"


def test_slow_storage_sleeps(monkeypatch):
    slept = []
    monkeypatch.setattr(chaos.time, "sleep", slept.append)
    chaos.configure(chaos.ChaosConfig(mode="slow_storage", latency_ms=1200))
    assert client.get("/todos").status_code == 200
    assert slept == [1.2]


def test_chaos_off_restores_normal_behavior():
    client.put("/admin/chaos", json={"mode": "storage_error", "error_rate": 1.0}, headers=ADMIN)
    client.put("/admin/chaos", json={"mode": "off"}, headers=ADMIN)
    assert client.get("/todos").status_code == 200
    assert chaos.state.since is None
