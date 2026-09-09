import json
from pathlib import Path

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
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # 파일이 비었거나 손상된 경우 빈 배열로 초기화
        DATA_FILE.write_text("[]", encoding="utf-8")
        return []


def save_todos(todos: list[dict]) -> None:
    DATA_FILE.write_text(
        json.dumps(todos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def next_id(todos: list[dict]) -> int:
    """현재 목록에서 가장 큰 id + 1 을 반환."""
    return max((t["id"] for t in todos), default=0) + 1


# 서버 시작 시 todo.json이 없으면 빈 배열로 생성
load_todos()


# ---------- 엔드포인트 ----------
@app.get("/")
def index():
    """기본 프론트엔드 뷰(HTML) 서빙."""
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.get("/todos", response_model=list[TodoItem])
def read_todos():
    """전체 To-Do 항목 조회 (Read)."""
    return load_todos()


@app.post("/todos", response_model=TodoItem, status_code=201)
def create_todo(todo: TodoIn):
    """새 To-Do 항목 추가 (Create). 서버가 id를 부여한다."""
    todos = load_todos()
    item = TodoItem(id=next_id(todos), **todo.model_dump())
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


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    """To-Do 항목 삭제 (Delete)."""
    todos = load_todos()
    new_todos = [t for t in todos if t["id"] != todo_id]
    if len(new_todos) == len(todos):
        raise HTTPException(status_code=404, detail="해당 id의 항목을 찾을 수 없습니다.")
    save_todos(new_todos)
    return None