"""正常链路与关联标识传递测试。"""

from fastapi.testclient import TestClient

from tests.conftest import JsonLogCapture


def test_root_response_shape_unchanged(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    # 既有接口对外返回结构不能变
    assert resp.json() == {"Hello": "World"}


def test_generated_request_id_when_absent(
    client: TestClient, log_capture: JsonLogCapture
) -> None:
    resp = client.get("/")
    rid = resp.headers["x-request-id"]
    assert len(rid) == 32  # uuid4 hex
    body = resp.json()
    # 关联标识通过响应头回传，不污染原有 body
    assert body == {"Hello": "World"}

    # 该请求的所有日志都带同一个关联标识
    rid_logs = log_capture.for_request(rid)
    assert rid_logs, "expected at least one log tagged with request id"
    assert all(r.request_id == rid for r in rid_logs)


def test_client_request_id_is_reused(
    client: TestClient, log_capture: JsonLogCapture
) -> None:
    rid = "client-correlation-123"
    resp = client.get("/", headers={"X-Request-ID": rid})
    assert resp.headers["x-request-id"] == rid
    assert all(r.request_id == rid for r in log_capture.for_request(rid))


def test_forged_request_id_is_replaced(
    client: TestClient, log_capture: JsonLogCapture
) -> None:
    for forged in ["bad id with space", "<script>", "id/../slash", "x" * 200]:
        resp = client.get("/", headers={"X-Request-ID": forged})
        new_rid = resp.headers["x-request-id"]
        assert new_rid != forged
        assert new_rid.isalnum() and len(new_rid) == 32

    warnings = [
        r for r in log_capture.records
        if r.message == "invalid client request id rejected; generated a new one"
    ]
    assert len(warnings) == 4
    assert all(r.level == "WARNING" for r in warnings)


def test_async_branches_share_same_request_id(
    client: TestClient, log_capture: JsonLogCapture
) -> None:
    rid = "async-rid-xyz"
    resp = client.get("/async-demo", headers={"X-Request-ID": rid})
    assert resp.status_code == 200
    data = resp.json()
    assert data["request_id"] == rid
    assert {b["branch"] for b in data["branches"]} == {"a", "b"}
    # 两个异步分支的“下游调用”拿到的都是同一个标识
    assert {b["request_id"] for b in data["branches"]} == {rid}

    downstream_logs = [
        r for r in log_capture.for_request(rid)
        if r.message == "downstream call in async branch"
    ]
    assert len(downstream_logs) == 2
    assert {r.extra["branch"] for r in downstream_logs} == {"a", "b"}


def test_concurrent_requests_do_not_cross_talk(client: TestClient) -> None:
    """高并发交织下，每个请求的标识不能串号。"""
    import concurrent.futures

    def fire(idx: int) -> tuple[int, str, str]:
        rid = f"concurrent-rid-{idx:03d}"
        resp = client.get("/async-demo", headers={"X-Request-ID": rid})
        return resp.status_code, resp.headers["x-request-id"], resp.json()["request_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(fire, range(80)))

    for idx, (status, header_rid, body_rid) in enumerate(results):
        expected = f"concurrent-rid-{idx:03d}"
        assert status == 200
        assert header_rid == expected == body_rid
