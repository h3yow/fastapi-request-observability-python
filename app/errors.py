"""业务错误类型与错误归一化。

对外响应体统一为::

    {"error": {"code": "<机器可读错误码>", "message": "<安全文案>",
               "request_id": "<关联标识>"}}

内部异常的类型与堆栈不会出现在响应中，只进入结构化日志。
"""

from enum import Enum
from typing import Any, Dict, Optional

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .context import get_request_id


class ErrorCode(str, Enum):
    """对外可暴露的归一化错误码。"""

    BAD_REQUEST = "bad_request"
    NOT_FOUND = "not_found"
    UPSTREAM_ERROR = "upstream_error"
    INTERNAL_ERROR = "internal_error"


class AppError(Exception):
    """应用层可归一化错误。

    :param code: 归一化错误码
    :param message: 可安全暴露给客户端的文案
    :param status_code: 对应的 HTTP 状态码
    """

    def __init__(
        self,
        code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        message: str = "internal server error",
        status_code: int = 500,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class NotFoundError(AppError):
    """资源不存在（404）。"""

    def __init__(self, message: str = "resource not found") -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, 404)


class UpstreamError(AppError):
    """下游依赖失败（502）。"""

    def __init__(self, message: str = "upstream service error") -> None:
        super().__init__(ErrorCode.UPSTREAM_ERROR, message, 502)


def error_body(
    code: ErrorCode | str,
    message: str,
    request_id: Optional[str] = None,
) -> Dict[str, Any]:
    """构造归一化错误响应体。"""
    rid = request_id if request_id is not None else get_request_id()
    return {
        "error": {
            "code": str(code.value) if isinstance(code, ErrorCode) else str(code),
            "message": message,
            "request_id": rid,
        }
    }


def normalize_app_error(exc: AppError) -> Dict[str, Any]:
    """:class:`AppError` -> (status_code, body)。"""
    return {"status_code": exc.status_code, "body": error_body(exc.code, exc.message)}


def normalize_http_error(exc: StarletteHTTPException) -> Dict[str, Any]:
    """Starlette/FastAPI 的 HTTPException -> (status_code, body)。"""
    mapping = {404: ErrorCode.NOT_FOUND, 400: ErrorCode.BAD_REQUEST}
    code = mapping.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
    # 4xx 的 detail 通常是框架的安全文案，可以保留；5xx 不暴露 detail。
    if 400 <= exc.status_code < 500:
        message = str(exc.detail)
    else:
        message = "internal server error"
    return {"status_code": exc.status_code, "body": error_body(code, message)}


def normalize_validation_error(exc: RequestValidationError) -> Dict[str, Any]:
    """请求参数校验错误 -> 400 归一化响应，不回显可疑输入内容。"""
    return {
        "status_code": 422,
        "body": error_body(
            ErrorCode.BAD_REQUEST, "request validation failed"
        ),
    }


def normalize_unexpected_error(exc: Exception) -> Dict[str, Any]:
    """未预期异常 -> 500，对外完全隐藏内部细节。"""
    return {
        "status_code": 500,
        "body": error_body(
            ErrorCode.INTERNAL_ERROR, "internal server error"
        ),
    }
