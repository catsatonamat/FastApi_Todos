"""장애 주입(Chaos) 모듈.

AIOps 데모용으로 저장소(todo.json) 접근에 장애를 일으킨다.
상태는 프로세스 메모리에만 있으므로 컨테이너를 재시작하면 정상으로 돌아온다.
"""
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

ChaosMode = Literal["off", "storage_error", "slow_storage"]


class StorageUnavailableError(Exception):
    """저장소 I/O 장애. 앱은 이 예외를 503으로 응답한다."""

    def __init__(self, op: str, path: str):
        self.op = op
        super().__init__(f"[Errno 5] Input/output error while {op} '{path}'")


class ChaosConfig(BaseModel):
    """PUT /admin/chaos 요청 본문."""
    mode: ChaosMode
    error_rate: float = Field(default=0.5, ge=0.0, le=1.0)   # storage_error: 실패 확률
    latency_ms: int = Field(default=800, ge=0, le=10_000)    # slow_storage: 지연 시간


@dataclass
class ChaosState:
    mode: ChaosMode = "off"
    error_rate: float = 0.5
    latency_ms: int = 800
    since: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


state = ChaosState()


def configure(config: ChaosConfig) -> ChaosState:
    state.mode = config.mode
    state.error_rate = config.error_rate
    state.latency_ms = config.latency_ms
    state.since = None if config.mode == "off" else datetime.now(timezone.utc).isoformat()
    return state


def reset() -> ChaosState:
    return configure(ChaosConfig(mode="off"))


def before_storage(op: str, path: str) -> None:
    """저장소에 접근하기 직전에 호출된다. 설정된 장애 모드에 따라 지연시키거나 예외를 던진다."""
    if state.mode == "slow_storage":
        time.sleep(state.latency_ms / 1000)
    elif state.mode == "storage_error" and random.random() < state.error_rate:
        raise StorageUnavailableError(op, path)
