"""请求关联标识、结构化访问日志与指标中间件（纯 ASGI 实现）。

采用纯 ASGI 中间件而非 ``BaseHTTPMiddleware``：每个请求在同一任务链中
执行，配合 :mod:`app.context` 中的 ``ContextVar``，保证关联标识在
路由、依赖、异常处理器以及 ``asyncio`` 分支中都指向同一个值，且不同
请求之间互不串号。

职责：
1. 从 ``X-Request-ID`` 读取客户端关联标识：合法则沿用，缺失或伪造
   （含非法字符 / 超长）则补新值并记录 warning；
2. 把生效的关联标识写入响应头 ``X-N``；
3. 请求结束时输出一条稳定可断言的结构化访问日志（<400 INFO、
   4xx WARNING、5xx ERROR），并按接口维度记录指标（探针路径除外）；
4. 作为最后一道防线捕获未预期异常：保留完整异常链到日志，对外只
   返回归一化 500，不泄漏内部细节。
"""

import re
import time
from typing import Any, Dict, List, Tuple

from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .context import generate_request_id, get_request_id, reset_request_id, set_request_id
from .errors import ErrorCode, error_body
from .logging_config import get_logger
from .metrics import get_registry, is_excluded

REQUEST_ID_HEADER = "x-request-id"
MAX_REQUEST_ID_LENGTH = 128
_REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")

logger = get_logger("app.access")


def resolve_request_id(headers: Headers) -> Tuple[str, str]:
    """根据请求头确定生效关联标识。

    返回 ``(request_id, source)``，source 取值：
    ``client``（沿用）/ ``generated``（缺失补齐）/ ``invalid``（伪造补齐）。
    """
    raw = headers.get(REQUEST_ID_HEADER)
    if raw is None:
        return generate_request_id(), "generated"
    candidate = raw.strip()
    if _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate, "client"
    return generate_request_id(), "invalid"


class ObservabilityMiddleware:
    """关联标识 + 访问日志 + 指标 + 兜底错误归一化。"""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "")
        path = scope.get("path", "")
        request_id, source = resolve_request_id(Headers(scope=scope))
        token = set_request_id(request_id)

        if source == "invalid":
            logger.warning(
                "invalid client request id rejected; generated a new one",
                extra={
                    "method": method,
                    "path": path,
                    "request_id_source": source,
                },
            )

        start = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_wrapper(message: Dict[str, Any]) -> None:
            nonlocal status_code, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status_code = message.get("status", 500)
                message["headers"] = self._with_request_id_header(
                    message.get("headers", [])
                )
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:  # noqa: BLE001 - 最后一道防线，需兜底归一化
            duration_ms = (time.perf_counter() - start) * 1000
            logger.error(
                "unhandled exception during request",
                exc_info=exc,
                extra={
                    "method": method,
                    "path": path,
                    "status_code": 500,
                    "duration_ms": round(duration_ms, 3),
                    "request_id_source": source,
                },
            )
            if not response_started:
                response = JSONResponse(
                    status_code=500,
                    content=error_body(
                        ErrorCode.INTERNAL_ERROR, "internal server error"
                    ),
                    headers={REQUEST_ID_HEADER: request_id},
                )
                status_code = 500
                await response(scope, receive, send)
            self._record(scope, path, duration_ms, status_code)
            # 异常已被归一化处理，不再向上抛；不调用 reset 也无妨，
            # 请求任务结束后上下文随之销毁。
            return
        else:
            duration_ms = (time.perf_counter() - start) * 1000

        self._record(scope, path, duration_ms, status_code)

        level = logger.info if status_code < 400 else (
            logger.warning if status_code < 500 else logger.error
        )
        level(
            "request completed" if status_code < 500 else "request failed",
            extra={
                "method": method,
                "path": path,
                "endpoint": self._endpoint_label(scope),
                "status_code": status_code,
                "duration_ms": round(duration_ms, 3),
                "request_id_source": source,
            },
        )
        reset_request_id(token)

    @staticmethod
    def _with_request_id_header(headers: List[Tuple[bytes, bytes]]) -> List[Tuple[bytes, bytes]]:
        """在响应头中加入生效关联标识（若下游已写则不重复追加）。"""
        if not any(name.lower() == REQUEST_ID_HEADER.encode() for name, _ in headers):
            headers.append((REQUEST_ID_HEADER.encode(), get_request_id().encode()))  # type: ignore[union-attr]
        return headers

    @staticmethod
    def _endpoint_label(scope: Dict[str, Any]) -> str:
        """优先使用路由模板作为接口维度标签，未匹配则标记 unmatched。"""
        route = scope.get("route")
        return getattr(route, "path", None) or "unmatched"

    def _record(
        self, scope: Dict[str, Any], path: str, duration_ms: float, status_code: int
    ) -> None:
        if is_excluded(path):
            return
        endpoint = self._endpoint_label(scope)
        get_registry().record(
            endpoint=endpoint,
            duration_ms=duration_ms,
            status_code=status_code,
            is_error=status_code >= 400,
        )
