"""错误归一化：内部异常链完整保留在日志里，对外只返回归一化、可区分的错误。

对外错误体固定为::

    {"error": {"code": "...", "message": "...", "correlation_id": "..."}}

- ``AppError`` 及其子类：使用自带的 ``code`` / ``status_code``，可安全对外；
- 请求参数校验失败：归一化为 ``VALIDATION_ERROR``（400），不暴露内部字段细节；
- 未捕获异常：归一化为 ``INTERNAL_ERROR``（500），内部 traceback 只进日志。
"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.correlation import CORRELATION_ID_HEADER, get_correlation_id
from app.logging_config import get_logger

logger = get_logger("app.errors")


class AppError(Exception):
    """业务异常基类：携带对外的错误码与 HTTP 状态码。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    def __init__(self, message: str = "resource not found") -> None:
        super().__init__("NOT_FOUND", message, status_code=404)


class UpstreamError(AppError):
    def __init__(self, message: str = "upstream call failed") -> None:
        super().__init__("UPSTREAM_ERROR", message, status_code=502)


def resolve_request_correlation_id(request: Request) -> str:
    """优先从 request.scope 取关联标识（未捕获异常冒泡到最外层时
    contextvar 已被复位），取不到再回退到 contextvar。"""
    return request.scope.get("correlation_id") or get_correlation_id()


def error_body(code: str, message: str, correlation_id: str) -> dict:
    """构造归一化错误响应体。"""
    return {
        "error": {
            "code": code,
            "message": message,
            "correlation_id": correlation_id,
        }
    }


def register_exception_handlers(app: FastAPI) -> None:
    """注册异常处理器：AppError / RequestValidationError / 未捕获异常。"""

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        cid = resolve_request_correlation_id(request)
        logger.warning(
            "request failed with app error",
            extra={
                "correlation_id": cid,
                "context": {
                    "path": request.url.path,
                    "error_code": exc.code,
                    "status_code": exc.status_code,
                }
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message, cid),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        cid = resolve_request_correlation_id(request)
        logger.warning(
            "request validation failed",
            extra={
                "correlation_id": cid,
                "context": {
                    "path": request.url.path,
                    "error_code": "VALIDATION_ERROR",
                    "status_code": 400,
                }
            },
        )
        return JSONResponse(
            status_code=400,
            content=error_body("VALIDATION_ERROR", "request validation failed", cid),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        cid = resolve_request_correlation_id(request)
        # logger.exception 记录完整异常链（含 __cause__/__context__），不吞内部细节。
        logger.exception(
            "unhandled exception",
            extra={
                "correlation_id": cid,
                "context": {
                    "path": request.url.path,
                    "error_code": "INTERNAL_ERROR",
                    "status_code": 500,
                }
            },
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "internal server error", cid),
            headers={CORRELATION_ID_HEADER: cid},
        )
