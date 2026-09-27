"""pytest 公共夹具。

* 每个用例拿到全新组装的 app 与 TestClient；
* 指标注册表与就绪状态在用例间重置，避免并发/顺序污染；
* :class:`JsonLogCapture` 捕获 ``app`` 命名空间下的结构化日志，
  供用例断言级别、关联标识与判定依据，同时打印到测试输出中。
"""

import logging
from dataclasses import dataclass
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

from app import create_app
from app.health import readiness
from app.logging_config import JsonFormatter, RequestIdFilter
from app.metrics import get_registry


@dataclass
class CapturedLog:
    level: str
    message: str
    request_id: Any
    extra: Dict[str, Any]

    def as_json(self) -> str:
        return JsonFormatter().format(self.extra["_record"])


class JsonLogCapture(logging.Handler):
    """捕获 app.* 日志并保留解析后的结构化字段。"""

    def __init__(self) -> None:
        super().__init__()
        self.addFilter(RequestIdFilter())
        self.records: List[CapturedLog] = []

    def emit(self, record: logging.LogRecord) -> None:
        rid = getattr(record, "request_id", None)
        extra = {
            k: v
            for k, v in record.__dict__.items()
            if k
            not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "taskName",
                "request_id",
            }
        }
        extra["_record"] = record
        entry = CapturedLog(
            level=record.levelname,
            message=record.getMessage(),
            request_id=rid,
            extra=extra,
        )
        self.records.append(entry)
        # 单测日志中打印关联标识与判定依据
        print(
            f"[TEST-LOG] level={entry.level} request_id={rid} "
            f"message={entry.message} context={ {k: v for k, v in extra.items() if k != '_record'} }"
        )

    def messages(self, level: str | None = None) -> List[str]:
        return [r.message for r in self.records if level is None or r.level == level]

    def for_request(self, request_id: str) -> List[CapturedLog]:
        return [r for r in self.records if r.request_id == request_id]


@pytest.fixture
def log_capture() -> JsonLogCapture:
    handler = JsonLogCapture()
    logger = logging.getLogger("app")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield handler
    finally:
        logger.removeHandler(handler)


@pytest.fixture
def client(log_capture: JsonLogCapture) -> TestClient:
    get_registry().reset()
    readiness.set_ready(True)
    app = create_app()
    with TestClient(app) as c:
        yield c
