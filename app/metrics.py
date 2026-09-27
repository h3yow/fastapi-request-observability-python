"""按接口维度统计请求量、耗时与错误数的内存指标注册表。

口径：
- 维度为 ``METHOD path_template``（如 ``GET /items/{item_id}``），避免路径参数打爆基数；
- ``error`` 定义为响应状态码 >= 500（服务端错误），4xx 不计入错误数；
- 所有读写都走同一把锁，并发请求与并发读取快照互不污染。
"""

import threading
from dataclasses import dataclass


@dataclass
class RouteMetrics:
    """单个接口的累计指标。"""

    request_count: int = 0
    error_count: int = 0
    total_duration_seconds: float = 0.0

    @property
    def avg_duration_seconds(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.total_duration_seconds / self.request_count

    def snapshot(self) -> dict:
        return {
            "request_count": self.request_count,
            "error_count": self.error_count,
            "total_duration_seconds": round(self.total_duration_seconds, 6),
            "avg_duration_seconds": round(self.avg_duration_seconds, 6),
        }


class MetricsRegistry:
    """线程安全的指标注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._routes: dict[str, RouteMetrics] = {}

    @staticmethod
    def _key(method: str, path: str) -> str:
        return f"{method.upper()} {path}"

    def record(
        self, method: str, path: str, status_code: int, duration_seconds: float
    ) -> None:
        """记录一次请求；status_code >= 500 计入错误数。"""
        with self._lock:
            metrics = self._routes.setdefault(self._key(method, path), RouteMetrics())
            metrics.request_count += 1
            if status_code >= 500:
                metrics.error_count += 1
            metrics.total_duration_seconds += duration_seconds

    def snapshot(self) -> dict[str, dict]:
        """返回全部接口指标的一致性快照（拷贝，不受后续写入影响）。"""
        with self._lock:
            return {key: m.snapshot() for key, m in self._routes.items()}

    def reset(self) -> None:
        """清空指标（测试用）。"""
        with self._lock:
            self._routes.clear()


registry = MetricsRegistry()
