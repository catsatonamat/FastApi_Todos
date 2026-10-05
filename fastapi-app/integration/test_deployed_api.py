import os
import time

import httpx2
import pytest

BASE_URL = os.environ.get("BASE_URL")

pytestmark = pytest.mark.skipif(not BASE_URL, reason="BASE_URL 환경변수가 없어 건너뜁니다")


@pytest.fixture(scope="module")
def client():
    with httpx2.Client(base_url=BASE_URL, timeout=10) as c:
        deadline = time.monotonic() + 30
        while True:
            try:
                if c.get("/todos").status_code == 200:
                    break
            except httpx2.TransportError:
                pass
            if time.monotonic() > deadline:
                pytest.fail(f"{BASE_URL} 에 30초 안에 연결되지 않았습니다")
            time.sleep(1)
        yield c


def test_crud_flow(client):
    created = client.post("/todos", json={"title": "integration", "description": "auto"})
    assert created.status_code == 201
    todo_id = created.json()["id"]

    listed = client.get("/todos")
    assert listed.status_code == 200
    assert any(t["id"] == todo_id for t in listed.json())

    updated = client.put(
        f"/todos/{todo_id}",
        json={"title": "integration-updated", "description": "auto", "done": True},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "integration-updated"
    assert updated.json()["done"] is True

    deleted = client.delete(f"/todos/{todo_id}")
    assert deleted.status_code == 204

    after = client.get("/todos").json()
    assert all(t["id"] != todo_id for t in after)


def test_create_without_title_returns_422(client):
    response = client.post("/todos", json={"description": "title 없음"})
    assert response.status_code == 422


def test_delete_nonexistent_id_returns_404(client):
    ids = [t["id"] for t in client.get("/todos").json()]
    missing_id = max(ids, default=0) + 1000
    response = client.delete(f"/todos/{missing_id}")
    assert response.status_code == 404
