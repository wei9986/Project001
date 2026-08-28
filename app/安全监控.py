"""安全门：执行动作前检查限位、距离和抓取信号。调试时查看 fault 与传感器快照。

传感器对象按鸭子类型约定（不强制继承），只需提供以下成员：
  limit_triggered: bool          -- 限位信号是否触发
  tof_distance_mm: float|None    -- ToF 距离，None 表示该传感器未接入，跳过该项检查
  grasp_confirmed: bool|None     -- 抓取确认；None 表示未接入（跳过装配检查），
                                    False 表示已接入未确认（拦截装配）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


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

    def check(self, action_name: str, sensors: Any) -> SafetyDecision:
        """检查一个待执行动作，返回允许或故障原因。sensors 提供限位/ToF/抓取确认。"""
        if getattr(sensors, "limit_triggered", False):
            return SafetyDecision(False, "limit_triggered", "限位信号已触发")
        tof = getattr(sensors, "tof_distance_mm", None)
        if tof is not None and tof < self.minimum_tof_mm:
            return SafetyDecision(False, "tof_too_close", "ToF 距离低于安全阈值")
        # 仅当抓取传感器已接入且未确认（False）时拦截装配；
        # None（未接入）不得被当成未确认，避免实机装配被一票否决（P0-2）。
        if action_name == "assemble_material" and getattr(sensors, "grasp_confirmed", None) is False:
            return SafetyDecision(False, "grasp_not_confirmed", "抓取确认信号未满足")
        return SafetyDecision(True)
