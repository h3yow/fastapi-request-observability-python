"""关联标识：沿用、生成、伪造替换与异步分支传播。"""

import asyncio
import re

import httpx

from app.correlation import (
    CORRELATION_ID_HEADER,
    is_valid_correlation_id,
    resolve_correlation_id,
)
from tests.conftest import log_verdict

UUID4_HEX = re.compile(r"^[0-9a-f]{32}$")


def test_missing_header_generates_uuid(client):
    resp = client.get("/")
    cid = resp.headers[CORRELATION_ID_HEADER]
    assert UUID4_HEX.fullmatch(cid)
    log_verdict(cid, "未带请求头时服务端生成 UUID4 关联标识并写入响应头")


def test_valid_incoming_header_is_reused(client):
    resp = client.get("/", headers={CORRELATION_ID_HEADER: "order-2026.09_28"})
    assert resp.headers[CORRELATION_ID_HEADER] == "order-2026.09_28"
    log_verdict("order-2026.09_28", "合法标识被原样沿用并回写到响应头")


def test_forged_header_is_replaced(client):
    forged = 'bad id with spaces\r\nX-Injected: 1'
    resp = client.get("/", headers={CORRELATION_ID_HEADER: forged})
    cid = resp.headers[CORRELATION_ID_HEADER]
    assert cid != forged
    assert UUID4_HEX.fullmatch(cid)
    log_verdict(cid, f"伪造标识 {forged!r} 含非法字符，被替换为服务端生成的 UUID4")


def test_too_long_header_is_replaced(client):
    resp = client.get("/", headers={CORRELATION_ID_HEADER: "a" * 200})
    cid = resp.headers[CORRELATION_ID_HEADER]
    assert UUID4_HEX.fullmatch(cid)
    log_verdict(cid, "超长标识（>128 字符）不合法，被替换为新标识")


def test_resolve_correlation_id_unit():
    assert resolve_correlation_id(None) != resolve_correlation_id(None)
    assert resolve_correlation_id("ok-1") == "ok-1"
    assert resolve_correlation_id("  ok-1  ") == "ok-1"
    assert is_valid_correlation_id("a" * 128)
    assert not is_valid_correlation_id("a" * 129)
    assert not is_valid_correlation_id("")


def test_async_branches_share_correlation_id(client):
    resp = client.get("/work", headers={CORRELATION_ID_HEADER: "work-1"})
    body = resp.json()
    assert body["correlation_id"] == "work-1"
    assert body["branch_correlation_ids"] == ["work-1"] * 3
    log_verdict("work-1", "asyncio.gather 派生的 3 个异步分支均拿到同一关联标识")


def test_concurrent_requests_do_not_cross_correlation_ids(app):
    """并发交织：每个请求只看到自己的关联标识。"""
    ids = [f"req-{i}" for i in range(20)]

    async def run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as ac:

            async def one(cid: str):
                resp = await ac.get("/work", headers={CORRELATION_ID_HEADER: cid})
                return cid, resp

            return await asyncio.gather(*(one(cid) for cid in ids))

    results = asyncio.run(run())
    for cid, resp in results:
        body = resp.json()
        assert body["correlation_id"] == cid
        assert set(body["branch_correlation_ids"]) == {cid}
        assert resp.headers[CORRELATION_ID_HEADER] == cid
    log_verdict(
        f"{ids[0]}..{ids[-1]}",
        "20 个并发请求交织执行，主协程与异步分支的标识均未串号",
    )
