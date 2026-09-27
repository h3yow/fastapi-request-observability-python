"""错误归一化：对外归一、对内保留完整异常链。"""

from app.correlation import CORRELATION_ID_HEADER
from tests.conftest import log_verdict


def test_app_error_normalized(client, log_capture):
    resp = client.get("/items/0", headers={CORRELATION_ID_HEADER: "err-1"})
    assert resp.status_code == 404
    body = resp.json()
    assert body == {
        "error": {
            "code": "NOT_FOUND",
            "message": "item 0 does not exist",
            "correlation_id": "err-1",
        }
    }
    error_logs = log_capture.by_logger("app.errors")
    assert len(error_logs) == 1
    record = error_logs[0]
    assert record["level"] == "WARNING"
    assert record["correlation_id"] == "err-1"
    assert record["error_code"] == "NOT_FOUND"
    log_verdict("err-1", "AppError 归一化为 NOT_FOUND/404，日志级别 WARNING 且带同一关联标识")


def test_validation_error_normalized(client, log_capture):
    resp = client.get("/items/not-an-int", headers={CORRELATION_ID_HEADER: "err-2"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["correlation_id"] == "err-2"
    # 不暴露 pydantic 内部字段细节
    assert "loc" not in str(body) and "ctx" not in str(body)
    record = log_capture.by_logger("app.errors")[0]
    assert record["level"] == "WARNING"
    assert record["correlation_id"] == "err-2"
    log_verdict("err-2", "参数校验失败归一化为 VALIDATION_ERROR/400，不泄露内部校验细节")


def test_unexpected_error_normalized_and_chain_preserved(client, log_capture):
    resp = client.get("/boom", headers={CORRELATION_ID_HEADER: "err-3"})
    assert resp.status_code == 500
    body = resp.json()
    # 对外：归一化、不泄露内部细节
    assert body == {
        "error": {
            "code": "INTERNAL_ERROR",
            "message": "internal server error",
            "correlation_id": "err-3",
        }
    }
    assert "disk failure" not in str(body)
    assert "repository layer" not in str(body)
    # 响应头也带关联标识（异常响应由最外层中间件直接发出）
    assert resp.headers[CORRELATION_ID_HEADER] == "err-3"
    # 对内：ERROR 日志带完整异常链与同一关联标识
    record = log_capture.by_logger("app.errors")[0]
    assert record["level"] == "ERROR"
    assert record["correlation_id"] == "err-3"
    assert "ValueError: low-level disk failure" in record["exception"]
    assert "RuntimeError: repository layer failed" in record["exception"]
    assert "direct cause" in record["exception"]
    log_verdict("err-3", "未捕获异常对外归一化为 INTERNAL_ERROR/500，日志保留 ValueError->RuntimeError 完整异常链")


def test_request_log_levels_stable(client, log_capture):
    client.get("/", headers={CORRELATION_ID_HEADER: "ok-1"})
    client.get("/items/0", headers={CORRELATION_ID_HEADER: "err-4"})
    client.get("/boom", headers={CORRELATION_ID_HEADER: "err-5"})
    request_logs = {
        r["correlation_id"]: r for r in log_capture.by_logger("app.request")
    }
    assert request_logs["ok-1"]["level"] == "INFO"
    assert request_logs["ok-1"]["status_code"] == 200
    assert request_logs["err-4"]["status_code"] == 404
    assert request_logs["err-5"]["status_code"] == 500
    error_logs = {r["correlation_id"]: r for r in log_capture.by_logger("app.errors")}
    assert error_logs["err-4"]["level"] == "WARNING"
    assert error_logs["err-5"]["level"] == "ERROR"
    log_verdict("ok-1/err-4/err-5", "正常=INFO、业务错误=WARNING、未捕获异常=ERROR，级别稳定可断言")


def test_error_responses_distinguishable(client):
    r404 = client.get("/items/0").json()
    r400 = client.get("/items/abc").json()
    r500 = client.get("/boom").json()
    codes = {r404["error"]["code"], r400["error"]["code"], r500["error"]["code"]}
    assert codes == {"NOT_FOUND", "VALIDATION_ERROR", "INTERNAL_ERROR"}
    log_verdict(
        r500["error"]["correlation_id"],
        "三类错误的归一化 code 两两可区分",
    )
