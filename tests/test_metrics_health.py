"""指标统计与健康/就绪探针测试，含并发交织场景。"""

import concurrent.futures

from fastapi.testclient import TestClient


def test_metrics_count_per_endpoint(client: TestClient) -> None:
    client.get("/", headers={"X-Request-ID": "m-1"})
    client.get("/", headers={"X-Request-ID": "m-2"})
    client.get("/items/3", headers={"X-Request-ID": "m-3"})
    client.get("/items/1", headers={"X-Request-ID": "m-4"})  # 404
    client.get("/items/0", headers={"X-Request-ID": "m-5"})  # 500

    resp = client.get("/metrics")
    assert resp.status_code == 200
    endpoints = resp.json()["endpoints"]

    root = endpoints["/"]
    assert root["requests"] == 2
    assert root["errors"] == 0
    assert root["status_codes"]["200"] == 2
    assert root["avg_duration_ms"] >= 0.0

    items = endpoints["/items/{item_id}"]
    assert items["requests"] == 3
    assert items["errors"] == 2
    assert items["status_codes"] == {"200": 1, "404": 1, "500": 1}
    assert items["max_duration_ms"] >= items["min_duration_ms"]


def test_probes_and_metrics_are_excluded_from_metrics(client: TestClient) -> None:
    client.get("/health/live")
    client.get("/health/ready")
    client.get("/metrics")
    endpoints = client.get("/metrics").json()["endpoints"]
    assert "/health/live" not in endpoints
    assert "/health/ready" not in endpoints
    assert "/metrics" not in endpoints


def test_liveness_probe(client: TestClient) -> None:
    resp = client.get("/health/live")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "alive"
    # 判定依据明确，而不是笼统成功
    assert body["checks"]["process"]["status"] == "up"
    assert "running" in body["checks"]["process"]["reason"]


def test_readiness_probe_reasons_are_distinguished(client: TestClient) -> None:
    from app.health import readiness

    resp_ok = client.get("/health/ready")
    assert resp_ok.status_code == 200
    assert resp_ok.json()["status"] == "ready"
    assert (
        resp_ok.json()["checks"]["application"]["reason"]
        == "application accepting traffic"
    )

    readiness.set_ready(False, ["database connection refused"])
    resp_bad = client.get("/health/ready")
    assert resp_bad.status_code == 503
    body = resp_bad.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["application"]["status"] == "down"
    assert body["checks"]["application"]["reason"] == "database connection refused"

    readiness.set_ready(False, ["redis unreachable", "queue draining"])
    resp_multi = client.get("/health/ready")
    assert resp_multi.status_code == 503
    reason = resp_multi.json()["checks"]["application"]["reason"]
    assert "redis unreachable" in reason and "queue draining" in reason


def test_concurrent_probes_and_metrics_do_not_pollute(client: TestClient) -> None:
    """并发交织：业务请求 + 探针 + 抓指标，统计结果必须准确且探针不计入。"""
    from app.health import readiness

    def hit(path: str) -> int:
        return client.get(path).status_code

    def worker(idx: int) -> None:
        hit("/")
        hit("/async-demo")
        hit("/items/1")  # 404
        hit("/items/0")  # 500
        hit("/health/live")
        # 就绪状态在 ready/not_ready 间翻转，探针必须稳定可判定
        readiness.set_ready(idx % 2 == 0, ["synthetic flap"])
        hit("/health/ready")
        hit("/metrics")  # 与业务并发抓取，不应抛错或串数据

    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(worker, range(40)))

    # 恢复就绪后快照对账
    readiness.set_ready(True)
    endpoints = client.get("/metrics").json()["endpoints"]
    assert endpoints["/"]["requests"] == 40
    assert endpoints["/async-demo"]["requests"] == 40
    assert endpoints["/items/{item_id}"]["requests"] == 80
    assert endpoints["/items/{item_id}"]["errors"] == 80
    assert endpoints["/items/{item_id}"]["status_codes"]["404"] == 40
    assert endpoints["/items/{item_id}"]["status_codes"]["500"] == 40
    # 探针与指标自身在高并发下依然没有被计入
    assert all(
        p not in endpoints
        for p in ("/health/live", "/health/ready", "/metrics")
    )
