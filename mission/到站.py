"""第 6 项：到站只看里程计和速度。看见标签不能单独允许伸臂。"""

from __future__ import annotations

from typing import Optional, Tuple

Point = Tuple[float, float]


def _angle_delta(left: float, right: float) -> float:
    delta = abs(float(left) - float(right)) % 360.0
    return min(delta, 360.0 - delta)


def arrived(
    position_mm: Optional[Point],
    dock_mm: Optional[Point],
    threshold_mm: Optional[float],
    speed_mm_s: Optional[float],
    still_speed_mm_s: Optional[float],
    yaw_deg: Optional[float] = None,
    dock_yaw_deg: Optional[float] = None,
    heading_tolerance_deg: Optional[float] = None,
    require_heading: bool = False,
) -> bool:
    """位置门槛和静止门槛都要有实测值。比赛到站还要比航向。距离用平方比较。"""
    if position_mm is None or dock_mm is None or threshold_mm is None or speed_mm_s is None or still_speed_mm_s is None:
        return False
    if threshold_mm <= 0 or still_speed_mm_s <= 0:
        return False
    dx = float(position_mm[0]) - float(dock_mm[0])
    dy = float(position_mm[1]) - float(dock_mm[1])
    if dx * dx + dy * dy > float(threshold_mm) ** 2 or abs(float(speed_mm_s)) > float(still_speed_mm_s):
        return False
    if not require_heading:
        return True
    if yaw_deg is None or dock_yaw_deg is None or heading_tolerance_deg is None or heading_tolerance_deg <= 0:
        return False
    return _angle_delta(yaw_deg, dock_yaw_deg) <= float(heading_tolerance_deg)


def ring_aligned(point_mm: Optional[Point], center_mm: Optional[Point], tolerance_mm: Optional[float]) -> bool:
    """环心或半径为空时不算对准。距离同样用平方比较。"""
    if point_mm is None or center_mm is None or tolerance_mm is None or tolerance_mm <= 0:
        return False
    dx = float(point_mm[0]) - float(center_mm[0])
    dy = float(point_mm[1]) - float(center_mm[1])
    return dx * dx + dy * dy <= float(tolerance_mm) ** 2


def arm_allowed(odom_arrived: bool, tag_station: Optional[str], odom_station: Optional[str]) -> tuple[bool, Optional[str]]:
    """标签与里程计不一致时不伸臂。只有标签、没有里程计到站，也不伸臂。"""
    if not odom_arrived:
        return False, None
    if tag_station is not None and odom_station is not None and tag_station != odom_station:
        return False, "localization_conflict"
    return True, None
