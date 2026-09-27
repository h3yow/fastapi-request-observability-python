"""FastAPI 异常处理器：把各类异常翻译成归一化响应。

内部异常的类型与完整堆栈（含 ``raise ... from ...`` 链）进入结构化
日志；响应体只包含归一化错误码、安全文案与关联标识。
"""

from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .errors import (
    AppError,
    normalize_app_error,
    normalize_http_error,
    normalize_unexpected_error,
    normalize_validation_error,
)
from .logging_config import get_logger

logger = get_logger("app.errors")


def _json_response(result: dict[str, Any]) -> JSONResponse:
    return JSONResponse(
        status_code=result["status_code"], content=result["body"]
    )


async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    # 业务错误属于可预期失败，记录 warning，并保留异常链用于对账。
    logger.warning(
        "application error",
        exc_info=exc,
        extra={"path": request.url.path, "error_code": exc.code.value},
    )
    return _json_response(normalize_app_error(exc))


async def handle_http_error(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    result = normalize_http_error(exc)
    level = logger.warning if exc.status_code < 500 else logger.error
    level(
        "http error",
        extra={"path": request.url.path, "status_code": exc.status_code},
    )
    return _json_response(result)


async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.warning(
        "request validation failed",
        exc_info=exc,
        extra={"path": request.url.path},
    )
    return _json_response(normalize_validation_error(exc))


async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # 未预期异常：完整异常链（含 __cause__ / __context__）必须落日志。
    logger.error(
        "unhandled exception",
        exc_info=exc,
        extra={"path": request.url.path},
    )
    return _json_response(normalize_unexpected_error(exc))


def register_exception_handlers(app: FastAPI) -> None:
    """在 app 上注册全部归一化异常处理器。"""
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
