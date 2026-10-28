"""JSON 한 줄 로그 (Loki에서 앱 로그와 같은 방식으로 검색할 수 있도록)."""
import json
import logging
import sys
from datetime import datetime, timezone

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


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
    for noisy in ("aiohttp.access", "discord.gateway"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # 음성 기능을 쓰지 않으므로 "PyNaCl/davey is not installed" 경고는 숨긴다
    logging.getLogger("discord.client").setLevel(logging.ERROR)
