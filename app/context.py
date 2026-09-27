"""请求关联标识的上下文存取。

使用 :class:`contextvars.ContextVar` 保存当前请求的关联标识：

* 每个请求在自己的上下文中设置标识，高并发下互不串号；
* 在请求处理过程中派生的 ``asyncio`` 任务会复制当前上下文，
  因此异步分支里拿到的仍是同一个标识；
* 日志过滤器、异常处理器、下游调用均可通过 :func:`get_request_id` 取值。
"""

from contextvars import ContextVar, Token
from typing import Optional
from uuid import uuid4

_request_id_ctx: ContextVar[Optional[str]] = ContextVar(
    "request_id", default=None
)


def generate_request_id() -> str:
    """生成一个新的关联标识（32 位小写十六进制 UUID）。"""
    return uuid4().hex


def get_request_id() -> Optional[str]:
    """获取当前上下文的关联标识，未设置时返回 ``None``。"""
    return _request_id_ctx.get()


def set_request_id(request_id: str) -> Token[Optional[str]]:
    """在当前上下文设置关联标识，返回用于恢复的 token。"""
    return _request_id_ctx.set(request_id)


def reset_request_id(token: Token[Optional[str]]) -> None:
    """根据 token 把上下文恢复到设置前的状态。"""
    _request_id_ctx.reset(token)
