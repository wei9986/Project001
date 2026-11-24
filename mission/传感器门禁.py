"""传感器只从 STATUS 和 SPEED 进入。没有新帧时不把旧读数改成 0。"""

from __future__ import annotations

from typing import Optional


def sources_ready(sensor_source: Optional[str], speed_source: Optional[str]) -> bool:
    """none 只允许调试。gpio 读载位时，速度仍必须来自 SPEED 帧。"""
    if sensor_source not in ("status_frame", "gpio"):
        return False
    if sensor_source == "gpio":
        return speed_source == "status_frame"
    return speed_source == "status_frame"


def reports_stale(now_ms: float, seen_ms: Optional[float], armed_ms: float, offline_timeout_ms: float) -> bool:
    """间隔短于离线时间不算停车。100 ms 的上报节拍本身不停车。"""
    baseline = armed_ms if seen_ms is None else seen_ms
    return now_ms - baseline > offline_timeout_ms
