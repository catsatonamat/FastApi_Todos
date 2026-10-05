import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from main import app, save_todos, load_todos, TodoIn, TodoItem

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_data_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_FILE", tmp_path / "todo.json")
    save_todos([])
    yield


def make_todo(id, title="Test", description="", done=False, order=0):
    return {"id": id, "title": title, "description": description, "done": done, "order": order}


def seed(*titles):
    save_todos([make_todo(i, t, order=i - 1) for i, t in enumerate(titles, start=1)])


def sorted_titles():
    return [t["title"] for t in sorted(load_todos(), key=lambda t: t["order"])]


# ---------- 데이터 모델링 ----------

def test_todo_in_defaults():
    todo = TodoIn(title="Test")
    assert todo.description == ""
    assert todo.done is False
    assert todo.order == 0


def test_todo_in_requires_title():
    with pytest.raises(ValidationError):
        TodoIn(description="no title")


def test_todo_item_requires_id():
    with pytest.raises(ValidationError):
        TodoItem(title="Test")


def test_todo_item_keeps_all_fields():
    item = TodoItem(id=5, title="T", description="D", done=True, order=3, due_date="2026-01-02")
    assert item.model_dump() == {"id": 5, "title": "T", "description": "D", "done": True, "order": 3, "due_date": date(2026, 1, 2)}


# ---------- 상태관리 (JSON 파일 저장/로드) ----------

def test_save_and_load_roundtrip():
    todos = [make_todo(1, "A"), make_todo(2, "B", order=1)]
    save_todos(todos)
    assert load_todos() == todos


def test_load_todos_creates_file_when_missing():
    main.DATA_FILE.unlink()
    assert load_todos() == []
    assert main.DATA_FILE.exists()


def test_load_todos_recovers_from_corrupt_file():
    main.DATA_FILE.write_text("{not json", encoding="utf-8")
    assert load_todos() == []
    assert json.loads(main.DATA_FILE.read_text(encoding="utf-8")) == []


def test_load_todos_backfills_missing_order_and_persists():
    legacy = [{"id": 1, "title": "old1", "description": "", "done": False},
              {"id": 2, "title": "old2", "description": "", "done": True}]
    main.DATA_FILE.write_text(json.dumps(legacy), encoding="utf-8")

    todos = load_todos()

    assert [t["order"] for t in todos] == [0, 1]
    saved = json.loads(main.DATA_FILE.read_text(encoding="utf-8"))
    assert [t["order"] for t in saved] == [0, 1]


def test_created_item_is_persisted_and_readable():
    client.post("/todos", json={"title": "Persist"})
    assert client.get("/todos").json()[0]["title"] == "Persist"
    assert load_todos()[0]["title"] == "Persist"


# ---------- CRUD ----------

def test_get_todos_empty():
    response = client.get("/todos")
    assert response.status_code == 200
    assert response.json() == []


def test_get_todos_with_items():
    save_todos([make_todo(1, "Test")])
    response = client.get("/todos")
    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["title"] == "Test"


def test_get_todos_sorted_by_order():
    save_todos([make_todo(1, "C", order=2), make_todo(2, "A", order=0), make_todo(3, "B", order=1)])
    titles = [t["title"] for t in client.get("/todos").json()]
    assert titles == ["A", "B", "C"]


def test_create_todo():
    response = client.post("/todos", json={"title": "Test", "description": "Test description"})
    assert response.status_code == 201
    body = response.json()
    assert body["title"] == "Test"
    assert body["id"] == 1
    assert body["order"] == 0
    assert body["done"] is False
    assert len(load_todos()) == 1


def test_create_todo_assigns_sequential_ids_and_orders():
    client.post("/todos", json={"title": "first"})
    client.post("/todos", json={"title": "second"})
    items = load_todos()
    assert [t["id"] for t in items] == [1, 2]
    assert [t["order"] for t in items] == [0, 1]


def test_create_todo_ignores_client_supplied_order():
    response = client.post("/todos", json={"title": "Test", "order": 99})
    assert response.status_code == 201
    assert response.json()["order"] == 0


def test_update_todo():
    save_todos([make_todo(1, "Test", "Test description")])
    response = client.put("/todos/1", json={"title": "Updated", "description": "Updated description", "done": True, "order": 0})
    assert response.status_code == 200
    assert response.json()["title"] == "Updated"
    assert response.json()["done"] is True
    stored = load_todos()[0]
    assert stored["title"] == "Updated"
    assert stored["done"] is True


def test_update_todo_only_changes_target():
    save_todos([make_todo(1, "A"), make_todo(2, "B", order=1)])
    client.put("/todos/1", json={"title": "A2", "order": 0})
    assert [t["title"] for t in load_todos()] == ["A2", "B"]


def test_update_todo_not_found():
    response = client.put("/todos/1", json={"title": "Updated"})
    assert response.status_code == 404


def test_delete_todo():
    save_todos([make_todo(1, "Test")])
    response = client.delete("/todos/1")
    assert response.status_code == 204
    assert load_todos() == []


def test_delete_todo_keeps_other_items():
    seed("A", "B")
    client.delete("/todos/1")
    assert [t["title"] for t in load_todos()] == ["B"]


def test_delete_todo_not_found():
    response = client.delete("/todos/1")
    assert response.status_code == 404


# ---------- 유효성 검사 ----------

def test_create_todo_missing_title():
    response = client.post("/todos", json={"description": "Test description"})
    assert response.status_code == 422


def test_create_todo_invalid_done_type():
    response = client.post("/todos", json={"title": "Test", "done": "not-a-bool"})
    assert response.status_code == 422


def test_create_todo_invalid_order_type():
    response = client.post("/todos", json={"title": "Test", "order": "first"})
    assert response.status_code == 422


def test_update_todo_missing_title():
    save_todos([make_todo(1, "Test")])
    response = client.put("/todos/1", json={"description": "no title"})
    assert response.status_code == 422


def test_move_invalid_direction():
    seed("A", "B")
    response = client.put("/todos/1/move", params={"direction": "left"})
    assert response.status_code == 422


# ---------- 우선순위 이동 (/todos/{id}/move) ----------

def test_move_up_swaps_with_previous():
    seed("A", "B", "C")
    response = client.put("/todos/3/move", params={"direction": "up"})
    assert response.status_code == 200
    assert [t["title"] for t in response.json()] == ["A", "C", "B"]
    assert sorted_titles() == ["A", "C", "B"]


def test_move_down_swaps_with_next():
    seed("A", "B", "C")
    response = client.put("/todos/1/move", params={"direction": "down"})
    assert response.status_code == 200
    assert [t["title"] for t in response.json()] == ["B", "A", "C"]
    assert sorted_titles() == ["B", "A", "C"]


def test_move_top_item_up_is_noop():
    seed("A", "B")
    response = client.put("/todos/1/move", params={"direction": "up"})
    assert response.status_code == 200
    assert sorted_titles() == ["A", "B"]


def test_move_bottom_item_down_is_noop():
    seed("A", "B")
    response = client.put("/todos/2/move", params={"direction": "down"})
    assert response.status_code == 200
    assert sorted_titles() == ["A", "B"]


def test_move_persists_order_to_json_file():
    seed("A", "B", "C")
    client.put("/todos/3/move", params={"direction": "up"})
    saved = json.loads(main.DATA_FILE.read_text(encoding="utf-8"))
    order_by_id = {t["id"]: t["order"] for t in saved}
    assert order_by_id == {1: 0, 3: 1, 2: 2}


def test_move_keeps_item_fields_intact():
    save_todos([make_todo(1, "A", "desc-a", done=True, order=0), make_todo(2, "B", "desc-b", order=1)])
    client.put("/todos/2/move", params={"direction": "up"})
    moved = next(t for t in load_todos() if t["id"] == 2)
    assert moved == {"id": 2, "title": "B", "description": "desc-b", "done": False, "order": 0}


def test_move_not_found():
    seed("A")
    response = client.put("/todos/99/move", params={"direction": "up"})
    assert response.status_code == 404


def test_repeated_moves_keep_orders_unique():
    seed("A", "B", "C", "D")
    for _ in range(3):
        client.put("/todos/4/move", params={"direction": "up"})
        client.put("/todos/1/move", params={"direction": "down"})
    orders = [t["order"] for t in load_todos()]
    assert sorted(orders) == [0, 1, 2, 3]


# ---------- 마감일 (due_date) ----------

def test_todo_in_due_date_defaults_to_none():
    assert TodoIn(title="Test").due_date is None


def test_todo_in_rejects_invalid_due_date():
    with pytest.raises(ValidationError):
        TodoIn(title="Test", due_date="2026-13-45")


def test_create_todo_with_due_date_stores_iso_string():
    response = client.post("/todos", json={"title": "Test", "due_date": "2026-10-10"})
    assert response.status_code == 201
    assert response.json()["due_date"] == "2026-10-10"
    saved = json.loads(main.DATA_FILE.read_text(encoding="utf-8"))
    assert saved[0]["due_date"] == "2026-10-10"


def test_create_todo_without_due_date_is_null():
    response = client.post("/todos", json={"title": "Test"})
    assert response.json()["due_date"] is None


def test_create_todo_invalid_due_date_returns_422():
    response = client.post("/todos", json={"title": "Test", "due_date": "not-a-date"})
    assert response.status_code == 422


def test_update_todo_sets_due_date_and_persists():
    save_todos([make_todo(1, "Test")])
    response = client.put("/todos/1", json={"title": "Test", "due_date": "2027-01-01"})
    assert response.status_code == 200
    assert response.json()["due_date"] == "2027-01-01"
    assert load_todos()[0]["due_date"] == "2027-01-01"


def test_update_todo_clears_due_date_with_null():
    save_todos([{**make_todo(1, "Test"), "due_date": "2027-01-01"}])
    response = client.put("/todos/1", json={"title": "Test", "due_date": None})
    assert response.json()["due_date"] is None
    assert load_todos()[0]["due_date"] is None


def test_legacy_item_without_due_date_reads_as_null():
    save_todos([make_todo(1, "Old")])
    response = client.get("/todos")
    assert response.json()[0]["due_date"] is None


def test_move_keeps_due_date():
    save_todos([
        {**make_todo(1, "A", order=0), "due_date": "2027-02-02"},
        make_todo(2, "B", order=1),
    ])
    client.put("/todos/2/move", params={"direction": "up"})
    moved_a = next(t for t in load_todos() if t["id"] == 1)
    assert moved_a["due_date"] == "2027-02-02"
