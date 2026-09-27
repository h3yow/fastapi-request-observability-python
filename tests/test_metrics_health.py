"""指标口径与探针：维度、错误计数、并发访问不污染、判定原因可区分。"""

import threading

from app.correlation import CORRELATION_ID_HEADER
from app.health import ProbeRegistry, ProbeResult, ProbeStatus, aggregate
from app.metrics import registry
from tests.conftest import log_verdict


def test_metrics_recorded_per_route(client):
    client.get("/")
    client.get("/items/1")
    client.get("/items/2")
    client.get("/items/0")  # 404，不计错误
    client.get("/boom")     # 500，计错误
    routes = client.get("/metrics").json()["routes"]

    root = routes["GET /"]
    assert root["request_count"] == 1
    assert root["error_count"] == 0
    assert root["total_duration_seconds"] >= 0

    items = routes["GET /items/{item_id}"]
    # 路径参数被归一到路径模板维度
    assert items["request_count"] == 3
    assert items["error_count"] == 0  # 4xx 不算服务端错误

    boom = routes["GET /boom"]
    assert boom["request_count"] == 1
    assert boom["error_count"] == 1
    assert boom["avg_duration_seconds"] == boom["total_duration_seconds"]
    log_verdict(
        "-",
        "指标按 METHOD+路径模板 聚合；仅 5xx 计 error_count；平均值=总耗时/请求数",
    )


def test_metrics_snapshot_is_consistent_copy():
    registry.record("GET", "/x", 200, 0.1)
    snap = registry.snapshot()
    registry.record("GET", "/x", 500, 0.2)
    assert snap["GET /x"]["request_count"] == 1
    assert snap["GET /x"]["error_count"] == 0
    log_verdict("-", "快照是拷贝，后续写入不污染已取快照")


def test_concurrent_metric_recording_no_pollution(client):
    """并发请求 + 并发读快照：计数准确、快照不串。"""
    errors: list[Exception] = []

    def hammer(cid: str) -> None:
        try:
            for _ in range(25):
                client.get("/work", headers={CORRELATION_ID_HEADER: cid})
                client.get("/metrics")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(f"m-{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    routes = registry.snapshot()
    assert routes["GET /work"]["request_count"] == 100
    assert routes["GET /metrics"]["request_count"] == 100
    log_verdict("m-0..m-3", "4 线程 x 25 轮并发写指标并读快照，计数精确无丢失")


def test_health_endpoints_ok(client):
    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    body = ready.json()
    assert body["status"] == "ok"
    assert body["probes"]["metrics_registry"]["reason"] == "metrics registry available"
    log_verdict("-", "存活/就绪探针全 ok 时返回 200 且每项探针带判定原因")


def test_readiness_fail_returns_503_with_reason(client):
    from app.health import readiness_probes

    readiness_probes.register(
        "database",
        lambda: ProbeResult(ProbeStatus.FAIL, "connection refused"),
    )
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "fail"
    assert "database" in body["reason"]
    assert body["probes"]["database"]["reason"] == "connection refused"
    # 其他探针不受影响，原因各自可区分
    assert body["probes"]["metrics_registry"]["status"] == "ok"
    log_verdict("-", "单个就绪探针失败 -> 整体 503，失败原因精确到探针名与具体原因")


def test_probe_exception_becomes_fail():
    probes = ProbeRegistry()

    def broken() -> ProbeResult:
        raise RuntimeError("boom")

    probes.register("broken", broken)
    probes.register("fine", lambda: ProbeResult(ProbeStatus.OK, "fine"))
    results = probes.run()
    assert results["broken"].status is ProbeStatus.FAIL
    assert "boom" in results["broken"].reason
    assert results["fine"].status is ProbeStatus.OK
    status, reason = aggregate(results)
    assert status is ProbeStatus.FAIL
    assert "broken" in reason
    log_verdict("-", "探针抛异常被归一为 FAIL 并记录异常原因，不拖垮其他探针")


def test_degraded_distinguishable_from_fail():
    results = {
        "a": ProbeResult(ProbeStatus.DEGRADED, "slow disk"),
        "b": ProbeResult(ProbeStatus.OK, "ok"),
    }
    status, reason = aggregate(results)
    assert status is ProbeStatus.DEGRADED
    assert "a" in reason
    results["c"] = ProbeResult(ProbeStatus.FAIL, "down")
    status, _ = aggregate(results)
    assert status is ProbeStatus.FAIL
    log_verdict("-", "degraded 与 fail 是两种可区分的判定，fail 优先级更高")


def test_concurrent_probe_access_no_pollution():
    """并发注册与并发执行探针：结果完整、互不污染。"""
    probes = ProbeRegistry()
    errors: list[Exception] = []

    def register_batch(base: int) -> None:
        try:
            for i in range(50):
                name = f"p-{base}-{i}"
                probes.register(
                    name, lambda n=name: ProbeResult(ProbeStatus.OK, f"{n} ok")
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    snapshots: list[dict] = []

    def run_repeatedly() -> None:
        try:
            for _ in range(50):
                snapshots.append(probes.run())
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=register_batch, args=(b,)) for b in range(4)]
    threads += [threading.Thread(target=run_repeatedly) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    final = probes.run()
    assert len(final) == 200
    assert all(r.status is ProbeStatus.OK for r in final.values())
    # 每次执行的内部结果自洽：同一轮里名字与原因对应
    for snap in snapshots:
        for name, result in snap.items():
            assert result.reason == f"{name} ok"
    log_verdict("-", "4 线程并发注册 200 个探针 + 2 线程并发执行，结果完整且名称与原因不串")
