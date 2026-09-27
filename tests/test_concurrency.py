"""并发交织：正常/异常/伪造标识请求混合执行，链路与指标可对账。"""

import asyncio
import re
from collections import Counter

import httpx

from app.correlation import CORRELATION_ID_HEADER
from app.metrics import registry
from tests.conftest import log_verdict

UUID4_HEX = re.compile(r"^[0-9a-f]{32}$")


def test_mixed_workload_interleaved(app):
    """正常、业务错误、未捕获异常、伪造标识四类请求并发交织。"""

    async def run():
        # ServerErrorMiddleware 发出 500 响应后会重新抛出异常，交给传输层决定；
        # 这里关闭传输层重抛，模拟真实客户端只拿到 500 响应。
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:
            tasks = []
            for i in range(10):
                tasks.append(ac.get("/", headers={CORRELATION_ID_HEADER: f"ok-{i}"}))
                tasks.append(
                    ac.get("/items/0", headers={CORRELATION_ID_HEADER: f"nf-{i}"})
                )
                tasks.append(
                    ac.get("/boom", headers={CORRELATION_ID_HEADER: f"boom-{i}"})
                )
                tasks.append(
                    ac.get("/work", headers={CORRELATION_ID_HEADER: "bad id!"})
                )
            return await asyncio.gather(*tasks)

    responses = asyncio.run(run())
    assert len(responses) == 40

    for i in range(10):
        ok, nf, boom, forged = responses[i * 4 : i * 4 + 4]

        assert ok.status_code == 200
        assert ok.headers[CORRELATION_ID_HEADER] == f"ok-{i}"

        assert nf.status_code == 404
        assert nf.json()["error"]["code"] == "NOT_FOUND"
        assert nf.json()["error"]["correlation_id"] == f"nf-{i}"

        assert boom.status_code == 500
        assert boom.json()["error"]["code"] == "INTERNAL_ERROR"
        assert boom.json()["error"]["correlation_id"] == f"boom-{i}"
        assert boom.headers[CORRELATION_ID_HEADER] == f"boom-{i}"

        # 伪造标识被替换为服务端生成的 UUID4，且分支内不串号
        assert forged.status_code == 200
        body = forged.json()
        cid = body["correlation_id"]
        assert UUID4_HEX.fullmatch(cid)
        assert set(body["branch_correlation_ids"]) == {cid}

    # 指标对账：各类请求计数与错误数精确
    routes = registry.snapshot()
    assert routes["GET /"]["request_count"] == 10
    assert routes["GET /items/{item_id}"]["request_count"] == 10
    assert routes["GET /items/{item_id}"]["error_count"] == 0
    assert routes["GET /boom"]["request_count"] == 10
    assert routes["GET /boom"]["error_count"] == 10
    assert routes["GET /work"]["request_count"] == 10
    assert routes["GET /work"]["error_count"] == 0

    # 伪造标识请求生成的 UUID 互不相同（无串号、无碰撞）
    forged_cids = [
        responses[i * 4 + 3].json()["correlation_id"] for i in range(10)
    ]
    assert all(v == 1 for v in Counter(forged_cids).values())
    log_verdict(
        "ok-*/nf-*/boom-*/uuid",
        "40 个混合请求并发交织：响应标识各自正确，指标计数与错误数精确对账",
    )
