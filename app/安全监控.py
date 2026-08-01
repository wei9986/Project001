"""安全门：执行动作前检查限位、距离和抓取信号。调试时查看 fault 与传感器快照。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.虚拟设备 import VirtualSensors


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    fault: Optional[str] = None
    reason: Optional[str] = None


class SafetyMonitor:
    """执行动作前统一检查安全条件。"""

    def __init__(self, minimum_tof_mm: float = 80.0) -> None:
        if minimum_tof_mm <= 0:
            raise ValueError("minimum_tof_mm 必须为正数")
        self.minimum_tof_mm = minimum_tof_mm

    def check(self, action_name: str, sensors: VirtualSensors) -> SafetyDecision:
        """检查一个待执行动作，返回允许或故障原因。"""
        if sensors.limit_triggered:
            return SafetyDecision(False, "limit_triggered", "限位信号已触发")
        if sensors.tof_distance_mm is not None and sensors.tof_distance_mm < self.minimum_tof_mm:
            return SafetyDecision(False, "tof_too_close", "ToF 距离低于安全阈值")
        if action_name == "assemble_material" and not sensors.grasp_confirmed:
            return SafetyDecision(False, "grasp_not_confirmed", "抓取确认信号未满足")
        return SafetyDecision(True)
