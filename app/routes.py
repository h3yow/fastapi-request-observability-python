"""业务路由与指标查询接口。

路由刻意覆盖几条链路，便于本地对账：

* ``GET /``            正常链路，保持既有返回结构；
* ``GET /async-demo``  正常链路，内部派生成 asyncio 任务，
                       用于验证关联标识在异步分支中保持一致；
* ``GET /items/{id}``  404 / 502 / 500 归一化错误链路；
* ``GET /metrics``     按接口维度的指标快照（自身不参与统计）。
"""

import asyncio
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .context import get_request_id
from .errors import NotFoundError, UpstreamError
from .logging_config import get_logger
from .metrics import get_registry

router = APIRouter()
logger = get_logger("app.routes")

# 模拟在异步分支中被下游调用的“服务”：必须拿到同一个关联标识。
_seen_request_ids: list[str] = []


async def _downstream_call(tag: str) -> Dict[str, str]:
    rid = get_request_id()
    assert rid is not None, "downstream call lost request id"
    logger.info(
        "downstream call in async branch",
        extra={"branch": tag, "downstream": "demo-service"},
    )
    _seen_request_ids.append(rid)
    await asyncio.sleep(0)
    return {"branch": tag, "request_id": rid}


@router.get("/")
async def read_root() -> Dict[str, Any]:
    """根接口，保持既有返回结构 ``{"Hello": "World"}``。"""
    logger.info("read_root handled")
    return {"Hello": "World"}


@router.get("/async-demo")
async def async_demo() -> Dict[str, Any]:
    """派生并发异步分支，验证关联标识在所有分支中一致。"""
    rid = get_request_id()
    results = await asyncio.gather(
        _downstream_call("a"),
        _downstream_call("b"),
    )
    return {
        "request_id": rid,
        "branches": results,
    }


@router.get("/items/{item_id}")
async def get_item(item_id: int) -> Dict[str, Any]:
    """按 id 触发不同错误链路的演示接口。

    * 0：触发未预期异常（对外归一化为 500 internal_error）；
    * 1：资源不存在（404 not_found）；
    * 2：下游失败（502 upstream_error）；
    * 其它：正常返回。
    """
    logger.info("get_item start", extra={"item_id": item_id})
    if item_id == 0:
        # 演示内部异常链：对外不能暴露 "db password..." 等细节。
        raise RuntimeError("db password=secret exploded")
    if item_id == 1:
        raise NotFoundError(f"item {item_id} not found")
    if item_id == 2:
        raise UpstreamError()
    return {"item_id": item_id, "request_id": get_request_id()}


@router.get("/metrics")
async def get_metrics() -> JSONResponse:
    """返回当前指标快照。"""
    return JSONResponse(
        {
            "status": "ok",
            "endpoints": get_registry().snapshot(),
        }
    )
