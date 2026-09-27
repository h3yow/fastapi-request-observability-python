"""结构化日志输出的稳定性测试（直接捕获 stdout 中的 JSON 行）。"""

import json
import logging
from io import StringIO

from app.logging_config import JsonFormatter, RequestIdFilter
from app.context import reset_request_id, set_request_id


def _make_stream_logger(stream: StringIO) -> logging.Logger:
    handler = logging.StreamHandler(stream)
    handler.addFilter(RequestIdFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.json.output")
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return logger


def test_every_log_line_is_json_with_stable_fields() -> None:
    stream = StringIO()
    logger = _make_stream_logger(stream)

    token = set_request_id("json-rid")
    logger.info("plain event", extra={"endpoint": "/", "status_code": 200})
    try:
        raise ValueError("boom")
    except ValueError:
        logger.error("error event", exc_info=True)
    reset_request_id(token)

    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        payload = json.loads(line)  # 必须是合法 JSON
        assert set(payload) >= {
            "timestamp", "level", "logger", "message", "request_id"
        }
        assert payload["request_id"] == "json-rid"

    first = json.loads(lines[0])
    assert first["level"] == "INFO"
    assert first["endpoint"] == "/" and first["status_code"] == 200

    second = json.loads(lines[1])
    assert second["level"] == "ERROR"
    assert second["exc_type"] == "ValueError"
    assert second["exc_message"] == "boom"
    assert "Traceback" in second["traceback"]


def test_logs_without_context_have_null_request_id() -> None:
    stream = StringIO()
    logger = _make_stream_logger(stream)
    logger.warning("orphan log")
    payload = json.loads(stream.getvalue().strip())
    assert payload["request_id"] is None
    assert payload["level"] == "WARNING"
