import json
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="To-Do API")

BASE_DIR = Path(__file__).resolve().parent
DATA_FILE = BASE_DIR / "todo.json"
TEMPLATES_DIR = BASE_DIR / "templates"


# ---------- Pydantic 모델 ----------
class TodoIn(BaseModel):
    """클라이언트가 보내는 입력용 모델 (id 없음)."""
    title: str
    description: str = ""
    done: bool = False
    order: int = 0


class TodoItem(TodoIn):
    """서버가 반환하는 출력용 모델 (id 포함)."""
    id: int


# ---------- JSON 파일 읽기/쓰기 헬퍼 ----------
def load_todos() -> list[dict]:
    """todo.json을 읽어 리스트로 반환. 없으면 빈 배열로 생성한다."""
    if not DATA_FILE.exists():
        DATA_FILE.write_text("[]", encoding="utf-8")
        return []
    try:
        todos = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 파일이 비었거나 손상된 경우 빈 배열로 초기화
        DATA_FILE.write_text("[]", encoding="utf-8")
        return []

    # 기존 데이터에 order 필드가 없으면 등록 순서를 기준으로 채워 넣는다
    backfilled = False
    for i, t in enumerate(todos):
        if "order" not in t:
            t["order"] = i
            backfilled = True
    if backfilled:
        save_todos(todos)
    return todos


def save_todos(todos: list[dict]) -> None:
    DATA_FILE.write_text(
        json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def next_id(todos: list[dict]) -> int:
    """현재 목록에서 가장 큰 id + 1 을 반환."""
    return max((t["id"] for t in todos), default=0) + 1


def next_order(todos: list[dict]) -> int:
    """현재 목록에서 가장 큰 order + 1 을 반환."""
    return max((t.get("order", 0) for t in todos), default=-1) + 1


# 서버 시작 시 todo.json이 없으면 빈 배열로 생성
load_todos()


# ---------- 엔드포인트 ----------
@app.get("/")
def index():
    """기본 프론트엔드 뷰(HTML) 서빙."""
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.get("/todos", response_model=list[TodoItem])
def read_todos():
    """전체 To-Do 항목 조회 (Read). 우선순위(order) 순으로 정렬한다."""
    return sorted(load_todos(), key=lambda t: t.get("order", 0))


@app.post("/todos", response_model=TodoItem, status_code=201)
def create_todo(todo: TodoIn):
    """새 To-Do 항목 추가 (Create). 서버가 id와 순서(order)를 부여한다."""
    todos = load_todos()
    item = TodoItem(
        id=next_id(todos),
        order=next_order(todos),
        **todo.model_dump(exclude={"order"}),
    )
    todos.append(item.model_dump())
    save_todos(todos)
    return item


@app.put("/todos/{todo_id}", response_model=TodoItem)
def update_todo(todo_id: int, todo: TodoIn):
    """기존 To-Do 항목 수정 (Update)."""
    todos = load_todos()
    for i, t in enumerate(todos):
        if t["id"] == todo_id:
            updated = TodoItem(id=todo_id, **todo.model_dump())
            todos[i] = updated.model_dump()
            save_todos(todos)
            return updated
    raise HTTPException(status_code=404, detail="해당 id의 항목을 찾을 수 없습니다.")


@app.put("/todos/{todo_id}/move", response_model=list[TodoItem])
def move_todo(todo_id: int, direction: Literal["up", "down"]):
    """항목의 우선순위(order)를 이웃 항목과 맞바꾼다. 파일을 한 번만 읽고 써서
    동시 요청으로 인한 경쟁 상태(race condition)를 방지한다."""
    todos = sorted(load_todos(), key=lambda t: t.get("order", 0))
    idx = next((i for i, t in enumerate(todos) if t["id"] == todo_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="해당 id의 항목을 찾을 수 없습니다.")

    target_idx = idx - 1 if direction == "up" else idx + 1
    if 0 <= target_idx < len(todos):
        todos[idx]["order"], todos[target_idx]["order"] = (
            todos[target_idx]["order"],
            todos[idx]["order"],
        )
        todos.sort(key=lambda t: t.get("order", 0))
        save_todos(todos)

    return todos


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    """To-Do 항목 삭제 (Delete)."""
    todos = load_todos()
    new_todos = [t for t in todos if t["id"] != todo_id]
    if len(new_todos) == len(todos):
        raise HTTPException(status_code=404, detail="해당 id의 항목을 찾을 수 없습니다.")
    save_todos(new_todos)
    return None