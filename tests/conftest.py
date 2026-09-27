"""测试公共夹具：独立应用实例、指标复位、结构化日志捕获。"""

import io
import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.health import liveness_probes, readiness_probes
from app.logging_config import JsonFormatter
from app.main import create_app
from app.metrics import registry


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后复位指标与探针注册表，避免用例间互相污染。"""
    saved_live = dict(liveness_probes._checks)
    saved_ready = dict(readiness_probes._checks)
    registry.reset()
    yield
    registry.reset()
    liveness_probes._checks.clear()
    liveness_probes._checks.update(saved_live)
    readiness_probes._checks.clear()
    readiness_probes._checks.update(saved_ready)


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app):
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


class LogCapture:
    """捕获 root logger 输出并按行解析为结构化字典。"""

    def __init__(self) -> None:
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(JsonFormatter())

    @property
    def records(self) -> list[dict]:
        return [
            json.loads(line)
            for line in self.stream.getvalue().splitlines()
            if line.strip()
        ]

    def by_logger(self, name: str) -> list[dict]:
        return [r for r in self.records if r["logger"] == name]


@pytest.fixture()
def log_capture():
    """把 JSON 格式化日志接到内存流上，用例结束后还原。"""
    capture = LogCapture()
    root = logging.getLogger()
    root.addHandler(capture.handler)
    yield capture
    root.removeHandler(capture.handler)


def log_verdict(correlation_id: str, rationale: str) -> None:
    """在单测日志中打印关联标识与判定依据（pytest -s 可见）。"""
    print(f"[verdict] correlation_id={correlation_id} | 判定依据: {rationale}")
