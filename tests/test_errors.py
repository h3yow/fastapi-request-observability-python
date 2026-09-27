"""异常链路与错误归一化测试。"""

from fastapi.testclient import TestClient

from tests.conftest import JsonLogCapture


def test_not_found_is_normalized(client: TestClient) -> None:
    rid = "err-404-rid"
    resp = client.get("/items/1", headers={"X-Request-ID": rid})
    assert resp.status_code == 404
    assert resp.json() == {
        "error": {
            "code": "not_found",
            "message": "item 1 not found",
            "request_id": rid,
        }
    }
    assert resp.headers["x-request-id"] == rid


def test_upstream_error_is_normalized(client: TestClient) -> None:
    resp = client.get("/items/2")
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"]["code"] == "upstream_error"
    assert body["error"]["request_id"] == resp.headers["x-request-id"]


def test_unexpected_error_hides_internal_details(
    client: TestClient, log_capture: JsonLogCapture
) -> None:
    rid = "err-500-rid"
    resp = client.get("/items/0", headers={"X-Request-ID": rid})
    assert resp.status_code == 500
    body = resp.json()
    # 对外只能看到归一化结果
    assert body == {
        "error": {
            "code": "internal_error",
            "message": "internal server error",
            "request_id": rid,
        }
    }
    raw = resp.text
    assert "secret" not in raw
    assert "RuntimeError" not in raw
    assert "Traceback" not in raw

    # 内部异常链路必须完整落日志，且带对关联标识，不能错配
    error_logs = [
        r for r in log_capture.for_request(rid) if r.level == "ERROR"
    ]
    assert error_logs
    target = [
        r for r in error_logs
        if r.message == "unhandled exception during request"
    ]
    assert len(target) == 1
    record = target[0].extra["_record"]
    assert record.exc_info is not None
    assert record.exc_info[0].__name__ == "RuntimeError"
    assert "secret" in str(record.exc_info[1])  # 细节只进日志
    assert "app/middleware.py" in target[0].as_json()


def test_validation_error_is_normalized_without_input_echo(
    client: TestClient,
) -> None:
    resp = client.get("/items/abc")
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["message"] == "request validation failed"
    assert "abc" not in resp.text  # 不回显可疑输入
    assert body["error"]["request_id"] == resp.headers["x-request-id"]


def test_error_log_levels_are_stable(
    client: TestClient, log_capture: JsonLogCapture
) -> None:
    """正常 INFO / 4xx WARNING / 5xx ERROR，且访问日志带判定字段。"""
    client.get("/", headers={"X-Request-ID": "level-ok"})
    client.get("/items/1", headers={"X-Request-ID": "level-404"})
    client.get("/items/0", headers={"X-Request-ID": "level-500"})

    def access_logs(rid: str):
        return [
            r for r in log_capture.for_request(rid)
            if r.message
            in {"request completed", "request failed", "unhandled exception during request"}
        ]

    ok = access_logs("level-ok")
    not_found = access_logs("level-404")
    crashed = access_logs("level-500")
    assert len(ok) == len(not_found) == len(crashed) == 1
    assert ok[0].level == "INFO" and ok[0].extra["status_code"] == 200
    assert not_found[0].level == "WARNING" and not_found[0].extra["status_code"] == 404
    assert crashed[0].level == "ERROR" and crashed[0].extra["status_code"] == 500
    assert crashed[0].message == "unhandled exception during request"
    # 判定字段：耗时与接口标签稳定存在
    assert isinstance(crashed[0].extra["duration_ms"], float)
    assert crashed[0].extra["path"] == "/items/0"
