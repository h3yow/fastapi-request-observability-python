"""健康与就绪探针，判定原因可区分。

- 每个探针是一个 ``name -> check()`` 注册项，check 返回 :class:`ProbeResult`；
- 探针抛异常时归为 ``FAIL`` 并记录异常原因，不会拖垮整个探针接口；
- 注册与执行都走锁，并发访问探针接口不会互相污染。
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum


class ProbeStatus(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    FAIL = "fail"


@dataclass(frozen=True)
class ProbeResult:
    status: ProbeStatus
    reason: str

    def as_dict(self) -> dict:
        return {"status": self.status.value, "reason": self.reason}


class ProbeRegistry:
    """线程安全的探针注册表。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checks: dict[str, Callable[[], ProbeResult]] = {}

    def register(self, name: str, check: Callable[[], ProbeResult]) -> None:
        with self._lock:
            self._checks[name] = check

    def run(self) -> dict[str, ProbeResult]:
        """执行全部探针；单个探针异常被归一化为 FAIL，不影响其他探针。"""
        with self._lock:
            checks = dict(self._checks)
        results: dict[str, ProbeResult] = {}
        for name, check in checks.items():
            try:
                results[name] = check()
            except Exception as exc:  # noqa: BLE001 - 探针自身异常也要可归因
                results[name] = ProbeResult(
                    ProbeStatus.FAIL, f"probe raised {type(exc).__name__}: {exc}"
                )
        return results


def aggregate(results: dict[str, ProbeResult]) -> tuple[ProbeStatus, str]:
    """汇总探针结果：任一 FAIL 则整体 FAIL，任一 DEGRADED 则整体 DEGRADED。"""
    if not results:
        return ProbeStatus.OK, "no probes registered"
    failed = [n for n, r in results.items() if r.status is ProbeStatus.FAIL]
    if failed:
        return ProbeStatus.FAIL, f"failed probes: {', '.join(sorted(failed))}"
    degraded = [n for n, r in results.items() if r.status is ProbeStatus.DEGRADED]
    if degraded:
        return ProbeStatus.DEGRADED, f"degraded probes: {', '.join(sorted(degraded))}"
    return ProbeStatus.OK, "all probes ok"


liveness_probes = ProbeRegistry()
readiness_probes = ProbeRegistry()
