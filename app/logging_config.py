"""结构化日志配置。

* :class:`JsonFormatter` 将日志渲染为单行 JSON，字段稳定：
  ``timestamp / level / logger / message / request_id`` 以及业务
  通过 ``extra`` 传入的上下文字段；
* :class:`RequestIdFilter` 自动为每条日志补上当前上下文的关联标识；
* :func:`configure_logging` 只配置一次，幂等，避免 uvicorn / pytest
  重复添加 handler 导致输出翻倍。
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from .context import get_request_id

_CONFIGURED = False

# 允许在 extra 中透传的上下文字段白名单，避免把 LogRecord 内部属性泄漏出去。
_RESERVED_ATTRS = set(
    logging.makeLogRecord(
        {
            "name": "",
            "msg": "",
            "args": (),
            "levelname": "",
            "levelno": 0,
            "pathname": "",
            "filename": "",
            "module": "",
            "exc_info": None,
            "exc_text": None,
            "stack_info": None,
            "lineno": 0,
            "funcName": "",
            "created": 0,
            "msecs": 0,
            "relativeCreated": 0,
            "thread": 0,
            "threadName": "",
            "processName": "",
            "process": 0,
            "taskName": "",
        }
    ).__dict__.keys()
) | {"request_id"}


class RequestIdFilter(logging.Filter):
    """为每条日志补充当前上下文的 ``request_id``（无则为 ``null``）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JsonFormatter(logging.Formatter):
    """把日志记录渲染成单行 JSON。

    异常链路（``exc_info``）会被序列化为 ``exc_type / exc_message /
    traceback`` 三个字段，确保内部异常链不被吞掉。
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }

        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and not key.startswith("_"):
                payload[key] = value

        if record.exc_info:
            exc_type, exc_value, _ = record.exc_info
            payload["exc_type"] = exc_type.__name__ if exc_type else None
            payload["exc_message"] = str(exc_value) if exc_value else None
            payload["traceback"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """配置根日志：JSON 输出 + 关联标识过滤器。幂等。"""
    global _CONFIGURED
    if _CONFIGURED:
        return

    handler = logging.StreamHandler()
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """获取 logger；未显式配置时也保证根日志可用。"""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
