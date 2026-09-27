"""按接口维度的指标采集。

每个 endpoint（路由模板，如 ``/items/{item_id}``）维护：

* ``requests``：请求总数
* ``errors``：错误请求数（HTTP 状态码 >= 400）
* ``total_duration_ms`` / ``avg_duration_ms``：累计与平均耗时
* ``min_duration_ms`` / ``max_duration_ms``：耗时极值
* ``status_codes``：按状态码分桶的计数

所有读写都在同一把锁下进行，``snapshot`` 返回深拷贝，
并发记录与并发抓取指标互不污染。
"""

import threading
from copy import deepcopy
from typing import Any, Dict, Optional

# 探针与指标自身的路径不计入业务接口指标。
EXCLUDED_PATHS = frozenset({"/metrics", "/health/live", "/health/ready"})


def is_excluded(path: str) -> bool:
    """该路径是否应排除在业务指标之外。"""
    return path in EXCLUDED_PATHS


def _new_entry() -> Dict[str, Any]:
    return {
        "requests": 0,
        "errors": 0,
        "total_duration_ms": 0.0,
        "min_duration_ms": None,
        "max_duration_ms": None,
        "status_codes": {},
    }


class MetricsRegistry:
    """线程安全的请求指标注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._endpoints: Dict[str, Dict[str, Any]] = {}

    def record(
        self,
        endpoint: str,
        duration_ms: float,
        status_code: int,
        is_error: bool,
    ) -> None:
        """记录一次请求的指标。"""
        with self._lock:
            entry = self._endpoints.setdefault(endpoint, _new_entry())
            entry["requests"] += 1
            if is_error:
                entry["errors"] += 1
            entry["total_duration_ms"] += float(duration_ms)
            if entry["min_duration_ms"] is None or duration_ms < entry["min_duration_ms"]:
                entry["min_duration_ms"] = float(duration_ms)
            if entry["max_duration_ms"] is None or duration_ms > entry["max_duration_ms"]:
                entry["max_duration_ms"] = float(duration_ms)
            bucket = entry["status_codes"]
            key = str(status_code)
            bucket[key] = bucket.get(key, 0) + 1

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        """返回当前指标的深拷贝快照，并附带动口字段。"""
        with self._lock:
            data = deepcopy(self._endpoints)
        for entry in data.values():
            total = entry["total_duration_ms"]
            count = entry["requests"]
            entry["avg_duration_ms"] = (total / count) if count else 0.0
        return data

    def reset(self) -> None:
        """清空所有指标（主要给测试用）。"""
        with self._lock:
            self._endpoints = {}


# 进程级单例。
registry: Optional[MetricsRegistry] = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    """获取全局指标注册表。"""
    assert registry is not None
    return registry
