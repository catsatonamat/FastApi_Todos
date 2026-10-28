"""데모용 트래픽 생성기. Todo API에 읽기 위주의 요청을 꾸준히 보낸다.

표준 라이브러리만 사용하므로 python 이미지에 이 파일만 마운트해 실행할 수 있다.
"""
import json
import os
import random
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("TARGET_URL", "http://fastapi-app:8000")
RPS = float(os.environ.get("RPS", "5"))
MAX_OWN_TODOS = 20  # loadgen이 만든 항목이 이 수를 넘으면 지운다
PREFIX = "[loadgen]"


def call(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            raw = res.read()
            return res.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        return 0, None


def own_todos() -> list[dict]:
    status, todos = call("GET", "/todos")
    if status != 200:
        return []
    return [t for t in todos if t["title"].startswith(PREFIX)]


def step() -> int:
    roll = random.random()
    if roll < 0.70:
        return call("GET", "/todos")[0]
    mine = own_todos()
    if roll < 0.85 and len(mine) < MAX_OWN_TODOS:
        return call("POST", "/todos", {"title": f"{PREFIX} task {random.randint(1, 9999)}"})[0]
    if not mine:
        return call("GET", "/health")[0]
    target = random.choice(mine)
    # 상한을 넘으면 생성 대신 항상 삭제해 개수가 MAX_OWN_TODOS 근처로 돌아오게 한다
    if roll < 0.95 and len(mine) <= MAX_OWN_TODOS:
        return call("PUT", f"/todos/{target['id']}", {"title": target["title"], "done": not target["done"]})[0]
    return call("DELETE", f"/todos/{target['id']}")[0]


def main() -> None:
    print(f"loadgen → {BASE_URL} @ {RPS} rps", flush=True)
    stats: dict[int, int] = {}
    last_report = time.monotonic()
    while True:
        status = step()
        stats[status] = stats.get(status, 0) + 1
        if time.monotonic() - last_report > 30:
            print(f"last 30s status counts: {dict(sorted(stats.items()))}", flush=True)
            stats.clear()
            last_report = time.monotonic()
        time.sleep(random.expovariate(RPS))


if __name__ == "__main__":
    main()
