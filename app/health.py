"""健康检查与就绪检查。

* ``GET /health/live``：存活探针，只要进程能响应即成功，固定返回
  各检查项的判定依据，不依赖任何外部状态；
* ``GET /health/ready``：就绪探针，聚合若干检查项；任一检查项失败
  则整体 ``not_ready`` 并返回 503，响应中逐项给出 ``status / reason``，
  区分失败原因，而不是笼统的成功或失败。

探针路径不进入业务指标统计（见 :mod:`app.metrics`）。
"""

import threading
from typing import Any, Dict, List

from fastapi import APIRouter, Response

router = APIRouter()


class ReadinessState:
    """就绪状态。

    内置一个 ``application`` 检查项；测试或真实的启动/停机钩子可以调用
    :meth:`set_ready` 改变其判定与原因。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = True
        self._reasons: List[str] = []

    def set_ready(self, ready: bool, reasons: List[str] | None = None) -> None:
        """设置就绪状态与未就绪原因列表。"""
        with self._lock:
            self._ready = bool(ready)
            self._reasons = list(reasons) if reasons else []

    def check(self) -> Dict[str, Any]:
        """执行就绪检查，返回整体状态与逐项判定依据。"""
        with self._lock:
            ready = self._ready
            reasons = list(self._reasons)

        checks: Dict[str, Any] = {
            "application": {
                "status": "up" if ready else "down",
                "reason": "application accepting traffic"
                if ready
                else "; ".join(reasons) or "application not ready",
            }
        }
        overall = "ready" if all(c["status"] == "up" for c in checks.values()) else "not_ready"
        return {"status": overall, "checks": checks}


readiness = ReadinessState()


@router.get("/health/live")
async def liveness() -> Dict[str, Any]:
    """存活探针：进程可响应即存活。"""
    return {
        "status": "alive",
        "checks": {
            "process": {
                "status": "up",
                "reason": "process is running and responding to http",
            }
        },
    }


@router.get("/health/ready")
async def readiness_probe(response: Response) -> Dict[str, Any]:
    """就绪探针：任一检查项失败则返回 503 并列出原因。"""
    result = readiness.check()
    if result["status"] != "ready":
        response.status_code = 503
    return result
