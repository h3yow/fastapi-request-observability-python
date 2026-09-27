"""FastAPI 应用装配：中间件、异常处理器、探针与演示路由。"""

import asyncio

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.correlation import CORRELATION_ID_HEADER, get_correlation_id
from app.errors import NotFoundError, register_exception_handlers
from app.health import (
    ProbeResult,
    ProbeStatus,
    aggregate,
    liveness_probes,
    readiness_probes,
)
from app.logging_config import configure_logging, get_logger
from app.metrics import registry
from app.middleware import CorrelationIdMiddleware, MetricsMiddleware

logger = get_logger("app.main")


def _register_builtin_probes() -> None:
    """内置探针：存活探针恒 ok；就绪探针检查指标注册表可用。"""
    liveness_probes.register(
        "process", lambda: ProbeResult(ProbeStatus.OK, "process alive")
    )

    def metrics_available() -> ProbeResult:
        registry.snapshot()
        return ProbeResult(ProbeStatus.OK, "metrics registry available")

    readiness_probes.register("metrics_registry", metrics_available)


def create_app() -> FastAPI:
    configure_logging()
    _register_builtin_probes()

    app = FastAPI(title="request-observability-demo")
    register_exception_handlers(app)
    # 后添加的中间件在更外层：先指标、后关联标识，保证指标日志能拿到关联标识。
    app.add_middleware(MetricsMiddleware)
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/")
    def read_root():
        return {"Hello": "World"}

    @app.get("/work")
    async def work():
        """演示异步分支内的关联标识传播：子任务与主协程拿到同一标识。"""
        main_cid = get_correlation_id()

        async def branch() -> str:
            await asyncio.sleep(0)
            return get_correlation_id()

        branch_cids = await asyncio.gather(*(branch() for _ in range(3)))
        return {
            "correlation_id": main_cid,
            "branch_correlation_ids": list(branch_cids),
        }

    @app.get("/items/{item_id}")
    async def get_item(item_id: int):
        if item_id == 0:
            raise NotFoundError("item 0 does not exist")
        return {"item_id": item_id}

    @app.get("/boom")
    async def boom():
        """演示异常链归一化：内部链完整进日志，对外只返回 INTERNAL_ERROR。"""
        try:
            raise ValueError("low-level disk failure")
        except ValueError as exc:
            raise RuntimeError("repository layer failed") from exc

    @app.get("/metrics")
    async def metrics():
        return {"routes": registry.snapshot()}

    @app.get("/health/live")
    async def health_live():
        results = liveness_probes.run()
        status, reason = aggregate(results)
        return {
            "status": status.value,
            "reason": reason,
            "probes": {n: r.as_dict() for n, r in results.items()},
        }

    @app.get("/health/ready")
    async def health_ready():
        results = readiness_probes.run()
        status, reason = aggregate(results)
        body = {
            "status": status.value,
            "reason": reason,
            "probes": {n: r.as_dict() for n, r in results.items()},
        }
        if status is ProbeStatus.FAIL:
            return JSONResponse(status_code=503, content=body)
        return body

    return app


app = create_app()
