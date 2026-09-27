"""关联标识（correlation id）的生成、校验与上下文传播。

传播规则：
- 客户端通过 ``X-Correlation-ID`` 请求头传入的标识，若格式合法则原样沿用；
- 缺失或格式非法（伪造/超长/含非法字符）时，服务端生成新的 UUID4 标识；
- 标识存放在 :mod:`contextvars` 中，异步分支（await / asyncio.Task）内自动继承，
  不同请求之间互不串号。
"""

import contextvars
import re
import uuid

CORRELATION_ID_HEADER = "X-Correlation-ID"

#: 允许的标识格式：1~128 位，仅字母数字与 ``-``、``_``、``.``，便于日志检索且防注入。
_VALID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

#: 无请求上下文（如启动日志）时的占位值。
NO_CORRELATION_ID = "-"

_correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "correlation_id", default=NO_CORRELATION_ID
)


def is_valid_correlation_id(value: str) -> bool:
    """判断客户端传入的关联标识是否合法可沿用。"""
    return bool(_VALID_PATTERN.fullmatch(value))


def new_correlation_id() -> str:
    """生成一个新的关联标识（UUID4）。"""
    return uuid.uuid4().hex


def resolve_correlation_id(incoming: str | None) -> str:
    """根据请求头决定最终使用的关联标识：合法则沿用，否则生成。"""
    if incoming is not None:
        candidate = incoming.strip()
        if is_valid_correlation_id(candidate):
            return candidate
    return new_correlation_id()


def get_correlation_id() -> str:
    """获取当前上下文的关联标识；无上下文时返回 ``-``。"""
    return _correlation_id.get()


def set_correlation_id(value: str) -> contextvars.Token[str]:
    """设置当前上下文的关联标识，返回 token 以便复位。"""
    return _correlation_id.set(value)


def reset_correlation_id(token: contextvars.Token[str]) -> None:
    """复位关联标识上下文到设置前的值。"""
    _correlation_id.reset(token)
