"""관측성(Observability) 모듈: Prometheus 메트릭 + JSON 구조화 로그.

- 메트릭은 GET /metrics 로 노출되고 Prometheus가 수집한다.
- 로그는 stdout에 한 줄짜리 JSON으로 찍히고 Alloy가 수집해 Loki로 보낸다.
"""
import json
import logging
import sys
import time
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

REQUESTS = Counter(
    "http_requests_total", "HTTP 요청 수", ["method", "route", "status"]
)
LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP 요청 처리 시간(초)",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
)
STORAGE_ERRORS = Counter(
    "todo_storage_errors_total", "저장소(todo.json) 접근 실패 수", ["op"]
)

# 메트릭·접근 로그에서 제외할 경로 (수집 자체가 노이즈가 되지 않도록)
SKIP_PATHS = {"/metrics"}

# LogRecord 기본 속성 — 이 외의 속성(extra=...)만 JSON 필드로 내보낸다
_RESERVED = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        entry.update({k: v for k, v in vars(record).items() if k not in _RESERVED})
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("todo")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


log = setup_logging()


def _route_of(request: Request) -> str:
    """라벨 카디널리티를 막기 위해 실제 경로(/todos/3) 대신 라우트 템플릿(/todos/{todo_id})을 쓴다."""
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


def instrument(app: FastAPI) -> None:
    @app.middleware("http")
    async def metrics_and_access_log(request: Request, call_next):
        if request.url.path in SKIP_PATHS:
            return await call_next(request)

        start = time.perf_counter()
        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            log.exception(
                "unhandled exception",
                extra={"method": request.method, "path": request.url.path, "error_type": "UnhandledException"},
            )
            response = JSONResponse({"detail": "Internal Server Error"}, status_code=500)

        elapsed = time.perf_counter() - start
        route = _route_of(request)
        REQUESTS.labels(request.method, route, str(status)).inc()
        LATENCY.labels(request.method, route).observe(elapsed)

        level = logging.ERROR if status >= 500 else logging.WARNING if elapsed > 0.5 else logging.INFO
        log.log(
            level,
            f"{request.method} {request.url.path} {status}",
            extra={
                "method": request.method,
                "route": route,
                "status": status,
                "duration_ms": round(elapsed * 1000, 1),
            },
        )
        return response

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
