"""结构化 JSON 日志配置。

所有日志统一输出为单行 JSON，固定字段：
``timestamp`` / ``level`` / ``logger`` / ``message`` / ``correlation_id``；
异常日志额外带 ``exception``（完整 traceback，保证内部异常链不被吞掉）。
"""

import json
import logging
import logging.config
from datetime import datetime, timezone

from app.correlation import get_correlation_id


class JsonFormatter(logging.Formatter):
    """把日志记录序列化为单行 JSON，并注入当前上下文的关联标识。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(
                record, "correlation_id", None
            )
            or get_correlation_id(),
        }
        # 透传业务侧通过 extra= 附加的上下文字段（如 path、status_code）。
        extras = getattr(record, "context", None)
        if isinstance(extras, dict):
            payload.update(extras)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """配置 root logger 输出结构化 JSON 日志（幂等，重复调用不叠加 handler）。"""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """获取命名 logger。"""
    return logging.getLogger(name)
