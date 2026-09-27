"""关联标识与指标采集中间件。

装配顺序（后添加的在更外层）：
``CorrelationIdMiddleware``（最外） -> ``MetricsMiddleware`` -> 路由，
保证指标与请求日志都能拿到关联标识。

关联标识同时写入 ``request.scope``：未捕获异常会冒泡到最外层的
ServerErrorMiddleware 处理，此时 contextvar 已被复位，异常处理器
需从 scope 取回标识（见 :mod:`app.errors`）。
"""

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.correlation import (
    CORRELATION_ID_HEADER,
    reset_correlation_id,
    resolve_correlation_id,
    set_correlation_id,
)
from app.logging_config import get_logger
from app.metrics import registry

logger = get_logger("app.request")

#: request.scope 中存放关联标识的键。
SCOPE_CORRELATION_ID_KEY = "correlation_id"


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """为每个请求解析/生成关联标识，写入上下文、scope 与响应头。"""

    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = resolve_correlation_id(
            request.headers.get(CORRELATION_ID_HEADER)
        )
        request.scope[SCOPE_CORRELATION_ID_KEY] = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(token)
        response.headers.setdefault(CORRELATION_ID_HEADER, correlation_id)
        return response


class MetricsMiddleware(BaseHTTPMiddleware):
    """按接口维度记录请求量、耗时与错误数，并输出一条请求完成日志。

    即使下游抛出未捕获异常（由外层归一化为 500），也要记账，
    保证异常链路的请求量与错误数不丢失。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        status_code = 500  # 下游未捕获异常时按 500 记账
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = time.perf_counter() - start
            # 路由匹配后 scope 里有 route，用路径模板做维度，避免路径参数打爆基数。
            route = request.scope.get("route")
            path_template = getattr(route, "path", request.url.path)
            registry.record(request.method, path_template, status_code, duration)
            logger.info(
                "request completed",
                extra={
                    "context": {
                        "method": request.method,
                        "path": path_template,
                        "status_code": status_code,
                        "duration_seconds": round(duration, 6),
                    }
                },
            )
