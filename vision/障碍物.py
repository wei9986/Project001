"""第 6 项：车道里的黑色圆柱。看不清就保持上一帧，不因此停车。

这里不写颜色阈值。物料掩膜不能拿来认障碍。像素尺寸未测量时，调用方不要编造毫米宽度。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import cv2
import numpy as np

# 命题写明障碍物是 φ50 mm 的圆柱。这是规则尺寸，不是摄像头标定值。
CYLINDER_DIAMETER_MM = 50.0
# 灰度低于这个值才当作暗块。这不是场地 HSV，也不读取颜色追踪配置。
DARK_GRAY_MAX = 40


@dataclass(frozen=True)
class ObstacleSight:
    confidence: float
    in_lane_roi: bool
    width_mm: Optional[float]
    blocks_path: bool = False
    center_mm: Optional[tuple] = None


def assess(
    sights: Sequence[ObstacleSight],
    min_confidence: Optional[float],
    width_tolerance_mm: Optional[float],
    previous: Sequence[ObstacleSight],
    clearance_mm: Optional[float] = None,
) -> tuple[tuple[ObstacleSight, ...], bool]:
    """返回保留的圆柱，以及这些圆柱是否已经能拿来规划绕行。

    置信度、宽度容差或绕行间距为空时，不能规划绕行，也不因此停车。
    这一帧都不达标时，保留上一帧已经确认的圆柱。
    """
    if min_confidence is None or width_tolerance_mm is None or width_tolerance_mm < 0:
        return tuple(previous), False
    confirmed = []
    for sight in sights:
        if not sight.in_lane_roi or sight.confidence < float(min_confidence):
            continue
        if sight.width_mm is None:
            continue
        if abs(float(sight.width_mm) - CYLINDER_DIAMETER_MM) > float(width_tolerance_mm):
            continue
        confirmed.append(sight)
    if not confirmed:
        return tuple(previous), False
    can_plan = (
        isinstance(clearance_mm, (int, float))
        and not isinstance(clearance_mm, bool)
        and float(clearance_mm) > 0
    )
    return tuple(confirmed), can_plan


def detect_cylinders(frame, calibration, lane) -> list:
    """有平面单应才把暗块变成毫米观测。分辨率不符或没有单应时返回空列表。"""
    if frame is None or getattr(frame, "ndim", 0) < 2:
        return []
    if calibration is None or not getattr(calibration, "has_plane_mapping", False):
        return []
    height, width = frame.shape[:2]
    if (width, height) != tuple(getattr(calibration, "image_size", ())):
        return []
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
    _level, mask = cv2.threshold(gray, DARK_GRAY_MAX, 255, cv2.THRESH_BINARY_INV)
    contours, _hierarchy = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    polygon = tuple(getattr(lane, "polygon_mm", ()) or ())
    from mission.车道 import point_inside

    sights = []
    for contour in contours:
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        if area < 30 or perimeter <= 0:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            continue
        center_px = (moments["m10"] / moments["m00"], moments["m01"] / moments["m00"])
        try:
            center_mm = calibration.pixel_to_world(center_px)
            width_mm = _width_mm(contour, calibration)
        except (cv2.error, RuntimeError, ValueError, ZeroDivisionError):
            continue
        if width_mm is None:
            continue
        confidence = min(1.0, 4.0 * math.pi * area / (perimeter * perimeter))
        sights.append(ObstacleSight(
            confidence=confidence,
            in_lane_roi=point_inside(center_mm, polygon),
            width_mm=width_mm,
            center_mm=(float(center_mm[0]), float(center_mm[1])),
        ))
    return sights


def _width_mm(contour, calibration) -> Optional[float]:
    """用外接矩形较短边的两端换算毫米，不使用固定的像素比例。"""
    box = cv2.boxPoints(cv2.minAreaRect(contour))
    edges = []
    for index in range(4):
        start = box[index]
        end = box[(index + 1) % 4]
        length = float(np.hypot(end[0] - start[0], end[1] - start[1]))
        edges.append((start, end, length))
    start, end, _length = min(edges, key=lambda item: item[2])
    world_start = calibration.pixel_to_world((float(start[0]), float(start[1])))
    world_end = calibration.pixel_to_world((float(end[0]), float(end[1])))
    return float(np.hypot(world_end[0] - world_start[0], world_end[1] - world_start[1]))
