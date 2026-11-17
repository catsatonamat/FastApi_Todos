import json
import logging
import os
import sqlite3
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field, StringConstraints

app = FastAPI(title="To-Do API")

# Prometheus 메트릭 엔드포인트(/metrics). 요청 수·응답 시간 등을 자동으로 수집 (#7)
Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# 액세스 로그를 표준 출력(stdout)으로 기록
# Docker가 stdout을 컨테이너 로그로 저장하고, Alloy가 그 로그를 읽어 Loki로 전송
custom_logger = logging.getLogger("custom.access")
custom_logger.setLevel(logging.INFO)
custom_logger.addHandler(logging.StreamHandler(sys.stdout))
custom_logger.propagate = False                  # 상위 로거로 넘겨 두 번 찍히는 것을 방지

# ← 추가: uvicorn 기본 액세스 로그 끄기 (아래 미들웨어가 대신 기록)
#   켜 두면 요청 1번에 로그가 2줄씩 남아 대시보드 카운트가 2배가 됨
logging.getLogger("uvicorn.access").disabled = True


@app.middleware("http")                          # 모든 요청을 한 줄씩 기록
async def log_requests(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start
    client = request.client.host if request.client else "-"
    # 형식: IP - "METHOD PATH HTTP/1.1" 상태코드 응답시간 → 7번 LogQL 쿼리가 이 형식으로 파싱
    custom_logger.info(
        f'{client} - "{request.method} {request.url.path} HTTP/1.1" '
        f'{response.status_code} {duration:.3f}s'
    )
    return response

BASE_DIR = Path(__file__).resolve().parent
# 저장 위치는 TODO_DB_FILE 환경변수로 바꿀 수 있다 (컨테이너에서는 쓰기 가능한 /app/data를 가리킨다)
DB_FILE = Path(os.environ.get("TODO_DB_FILE", BASE_DIR / "todo.db"))
TEMPLATES_DIR = BASE_DIR / "templates"

# 입력 제한 (디스크·메모리 소진 방지)
MAX_TITLE_LENGTH = 200
MAX_DESCRIPTION_LENGTH = 2000
MAX_ORDER = 100_000
MAX_TODOS = 1000


# ---------- Pydantic 모델 ----------
class TodoIn(BaseModel):
    """클라이언트가 보내는 입력용 모델 (id 없음). 길이·범위 제한을 건다."""
    # 앞뒤 공백을 제거한 뒤 검사하므로 공백만 있는 제목도 거부된다
    title: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TITLE_LENGTH),
    ]
    description: str = Field(default="", max_length=MAX_DESCRIPTION_LENGTH)
    done: bool = False
    order: int = Field(default=0, ge=0, le=MAX_ORDER)
    due_date: date | None = None


class TodoItem(BaseModel):
    """서버가 반환하는 출력용 모델 (id 포함).
    TodoIn을 상속하지 않아, 제한을 두기 전에 저장된 항목도 그대로 조회된다."""
    id: int
    title: str
    description: str = ""
    done: bool = False
    order: int = 0
    due_date: date | None = None


# ---------- SQLite 저장소 ----------
# 요청마다 연결을 새로 열고 닫는다 (요청은 스레드풀의 여러 스레드에서 동시에 처리되므로
# 연결을 스레드끼리 공유하지 않는다).
# 읽고-고치고-쓰는 엔드포인트는 transaction() 안에서 BEGIN IMMEDIATE로 쓰기 잠금을 먼저 잡는다.
# 그래서 동시 요청이 와도 같은 id·order가 두 번 발급되거나 다른 요청의 변경이 사라지지 않는다.
# 잠금은 DB 파일에 걸리므로 uvicorn 워커를 여러 개 띄워도 그대로 동작한다.
# ("order"는 SQL 예약어라 컬럼 이름은 sort_order로 두고, 밖으로는 order로 내보낸다)
SCHEMA = """
CREATE TABLE IF NOT EXISTS todos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    done        INTEGER NOT NULL DEFAULT 0,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    due_date    TEXT
)
"""
COLUMNS = "id, title, description, done, sort_order, due_date"
BUSY_TIMEOUT_SECONDS = 5   # 다른 요청이 쓰기 잠금을 잡고 있으면 최대 이만큼 기다린다


def _connect() -> sqlite3.Connection:
    # isolation_level=None: 트랜잭션을 BEGIN/COMMIT으로 직접 관리한다
    conn = sqlite3.connect(DB_FILE, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


@contextmanager
def transaction() -> Generator[sqlite3.Connection]:
    """쓰기 잠금을 잡고 시작해, 블록이 끝나면 커밋하고 예외가 나면 되돌린다."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "done": bool(row["done"]),
        "order": row["sort_order"],
        "due_date": row["due_date"],
    }


def _to_row(t: dict) -> tuple:
    """dict(또는 model_dump(mode="json") 결과)를 INSERT용 값 묶음으로 바꾼다."""
    return (
        t["id"],
        t["title"],
        t.get("description", ""),
        int(bool(t.get("done", False))),
        t.get("order", 0),
        t.get("due_date"),
    )


def _fetch_sorted(conn: sqlite3.Connection) -> list[dict]:
    """우선순위(order) 순으로 전체 항목을 읽는다. order가 같으면 먼저 등록된(id가 작은) 것이 앞."""
    rows = conn.execute(f"SELECT {COLUMNS} FROM todos ORDER BY sort_order, id")
    return [_row_to_dict(r) for r in rows]


def load_todos() -> list[dict]:
    """전체 항목을 id 순(등록 순)으로 반환한다."""
    conn = _connect()
    try:
        rows = conn.execute(f"SELECT {COLUMNS} FROM todos ORDER BY id")
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def save_todos(todos: list[dict]) -> None:
    """저장된 항목 전체를 주어진 목록으로 바꾼다 (테스트 데이터 준비·JSON 이전에 사용)."""
    with transaction() as conn:
        conn.execute("DELETE FROM todos")
        conn.executemany(
            f"INSERT INTO todos ({COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            [_to_row(t) for t in todos],
        )


def init_db() -> None:
    """DB를 만들고, DB가 처음 생길 때 같은 폴더의 todo.json이 있으면 그 데이터를 옮겨 온다.
    todo.json은 지우지 않고 그대로 둔다 (DB가 이미 있으면 다시 옮기지 않는다)."""
    is_new = not DB_FILE.exists()
    _connect().close()
    legacy_file = DB_FILE.with_name("todo.json")
    if not (is_new and legacy_file.exists()):
        return
    try:
        legacy = json.loads(legacy_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        logging.getLogger(__name__).warning("%s를 읽을 수 없어 옮기지 않았습니다.", legacy_file)
        return
    # 예전 데이터에 order가 없으면 등록 순서를 기준으로 채워 넣는다
    save_todos([{**t, "order": t.get("order", i)} for i, t in enumerate(legacy)])


# 서버 시작 시 DB(테이블)가 없으면 만든다
init_db()


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="해당 id의 항목을 찾을 수 없습니다.")


# ---------- 엔드포인트 ----------
@app.get("/")
def index():
    """기본 프론트엔드 뷰(HTML) 서빙."""
    return FileResponse(TEMPLATES_DIR / "index.html")


@app.get("/todos", response_model=list[TodoItem])
def read_todos():
    """전체 To-Do 항목 조회 (Read). 우선순위(order) 순으로 정렬한다."""
    conn = _connect()
    try:
        return _fetch_sorted(conn)
    finally:
        conn.close()


@app.post(
    "/todos",
    response_model=TodoItem,
    status_code=201,
    responses={409: {"description": "항목 수가 상한(MAX_TODOS)에 도달해 더 추가할 수 없음"}},
)
def create_todo(todo: TodoIn):
    """새 To-Do 항목 추가 (Create). 서버가 id와 순서(order)를 부여한다."""
    with transaction() as conn:
        count, max_order = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(sort_order), -1) FROM todos"
        ).fetchone()
        if count >= MAX_TODOS:
            raise HTTPException(
                status_code=409,
                detail=f"할 일은 최대 {MAX_TODOS}개까지 저장할 수 있습니다.",
            )
        data = todo.model_dump(mode="json", exclude={"order"})
        cur = conn.execute(
            "INSERT INTO todos (title, description, done, sort_order, due_date) VALUES (?, ?, ?, ?, ?)",
            (data["title"], data["description"], int(data["done"]), max_order + 1, data["due_date"]),
        )
        return TodoItem(id=cur.lastrowid, order=max_order + 1, **todo.model_dump(exclude={"order"}))


@app.put("/todos/{todo_id}", response_model=TodoItem)
def update_todo(todo_id: int, todo: TodoIn):
    """기존 To-Do 항목 수정 (Update)."""
    data = todo.model_dump(mode="json")
    with transaction() as conn:
        cur = conn.execute(
            "UPDATE todos SET title = ?, description = ?, done = ?, sort_order = ?, due_date = ? WHERE id = ?",
            (data["title"], data["description"], int(data["done"]), data["order"], data["due_date"], todo_id),
        )
        if cur.rowcount == 0:
            raise _not_found()
    return TodoItem(id=todo_id, **todo.model_dump())


@app.put("/todos/{todo_id}/move", response_model=list[TodoItem])
def move_todo(todo_id: int, direction: Literal["up", "down"]):
    """항목의 우선순위(order)를 이웃 항목과 맞바꾼다. 읽기·수정·쓰기를 한 트랜잭션 안에서
    처리해 동시 요청으로 인한 경쟁 상태(race condition)를 방지한다."""
    with transaction() as conn:
        todos = _fetch_sorted(conn)
        idx = next((i for i, t in enumerate(todos) if t["id"] == todo_id), None)
        if idx is None:
            raise _not_found()

        target_idx = idx - 1 if direction == "up" else idx + 1
        if 0 <= target_idx < len(todos):
            a, b = todos[idx], todos[target_idx]
            a["order"], b["order"] = b["order"], a["order"]
            conn.executemany(
                "UPDATE todos SET sort_order = ? WHERE id = ?",
                [(a["order"], a["id"]), (b["order"], b["id"])],
            )
            todos.sort(key=lambda t: t["order"])

        return todos


@app.put(
    "/todos/{todo_id}/reorder",
    response_model=list[TodoItem],
    responses={404: {"description": "해당 id의 항목이 없음"}},
)
def reorder_todo(todo_id: int, position: Annotated[int, Query(ge=0)]):
    """항목을 목록의 position번째(0부터) 자리로 옮기고 order를 0부터 다시 매긴다.
    position이 목록 길이를 넘으면 맨 끝으로 옮긴다."""
    with transaction() as conn:
        todos = _fetch_sorted(conn)
        idx = next((i for i, t in enumerate(todos) if t["id"] == todo_id), None)
        if idx is None:
            raise _not_found()

        item = todos.pop(idx)
        todos.insert(min(position, len(todos)), item)
        for i, t in enumerate(todos):
            t["order"] = i
        conn.executemany(
            "UPDATE todos SET sort_order = ? WHERE id = ?",
            [(t["order"], t["id"]) for t in todos],
        )

        return todos


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: int):
    """To-Do 항목 삭제 (Delete)."""
    with transaction() as conn:
        cur = conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
        if cur.rowcount == 0:
            raise _not_found()
    return None
