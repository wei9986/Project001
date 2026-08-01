# -*- coding: utf-8 -*-
"""精定位：抓取前连续采样有效世界坐标，确认稳定后才允许抓取。"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, Optional, Tuple


class LocalizationStatus(str, Enum):
    WAITING = "waiting"
    COLLECTING = "collecting"
    STABLE = "stable"
    LOST = "lost"
    TIMEOUT = "timeout"
    NO_COORDINATE = "no_coordinate"
    JUMP_REJECTED = "jump_rejected"


@dataclass(frozen=True)
class LocalizationResult:
    status: LocalizationStatus
    valid: bool
    coordinate_mm: Optional[Tuple[float, float]]
    sample_count: int
    spread_mm: Optional[float]
    elapsed_ms: float
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "valid": self.valid,
            "coordinate_mm": list(self.coordinate_mm) if self.coordinate_mm else None,
            "sample_count": self.sample_count,
            "spread_mm": self.spread_mm,
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason,
        }


class FineLocalizer:
    """收集一个选定目标的稳定世界坐标样本。"""

    def __init__(self, required_samples: int = 5, max_spread_mm: float = 5.0,
                 max_step_mm: float = 30.0, timeout_ms: float = 1500.0,
                 lost_timeout_ms: float = 300.0, target_id: Optional[int] = None,
                 color: Optional[str] = None) -> None:
        if required_samples < 2 or max_spread_mm <= 0 or max_step_mm <= 0 or timeout_ms <= 0 or lost_timeout_ms <= 0:
            raise ValueError("二次定位参数必须为正数，required_samples 至少为 2")
        if target_id is None and color is None:
            raise ValueError("必须按 target_id 或 color 选择目标")
        self.required_samples = required_samples
        self.max_spread_mm = max_spread_mm
        self.max_step_mm = max_step_mm
        self.timeout_ms = timeout_ms
        self.lost_timeout_ms = lost_timeout_ms
        self.target_id = target_id
        self.color = color
        self.samples: Deque[Tuple[float, float]] = deque(maxlen=required_samples)
        self.started_ms: Optional[float] = None
        self.last_seen_ms: Optional[float] = None
        self.last_sample: Optional[Tuple[float, float]] = None

    def reset(self, now_ms: Optional[float] = None) -> None:
        """开始新的精定位，清空旧样本。"""
        self.samples.clear()
        self.started_ms = time.monotonic() * 1000 if now_ms is None else now_ms
        self.last_seen_ms = None
        self.last_sample = None

    def _select_target(self, result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从已确认的追踪结果中选择本次目标。"""
        targets = result.get("targets", [])
        selected = [target for target in targets if target.get("valid")]
        if self.target_id is not None:
            selected = [target for target in selected if target.get("target_id") == self.target_id]
        elif self.color is not None:
            selected = [target for target in selected if target.get("color") == self.color]
        return max(selected, key=lambda target: target.get("confidence", 0.0), default=None)

    def _result(self, status: LocalizationStatus, now_ms: float, coordinate: Optional[Tuple[float, float]] = None,
                spread: Optional[float] = None, reason: str = "") -> LocalizationResult:
        started = now_ms if self.started_ms is None else self.started_ms
        return LocalizationResult(status, status == LocalizationStatus.STABLE, coordinate,
                                  len(self.samples), spread, max(0.0, now_ms - started), reason)

    def update(self, vision_result: Dict[str, Any], now_ms: Optional[float] = None) -> LocalizationResult:
        """用一帧结构化识别结果推进精定位。"""
        now = time.monotonic() * 1000 if now_ms is None else now_ms
        if self.started_ms is None:
            self.reset(now)
        if now - self.started_ms > self.timeout_ms:
            return self._result(LocalizationStatus.TIMEOUT, now, reason="二次定位超时")
        target = self._select_target(vision_result)
        if target is None:
            if self.last_seen_ms is not None and now - self.last_seen_ms > self.lost_timeout_ms:
                return self._result(LocalizationStatus.LOST, now, reason="目标连续丢失")
            return self._result(LocalizationStatus.WAITING, now, reason="等待目标")
        coordinate = target.get("world_mm")
        if not coordinate or len(coordinate) != 2:
            return self._result(LocalizationStatus.NO_COORDINATE, now, reason="目标没有 world_mm")
        point = (float(coordinate[0]), float(coordinate[1]))
        self.last_seen_ms = now
        if self.last_sample is not None and math.dist(point, self.last_sample) > self.max_step_mm:
            self.samples.clear()
            self.last_sample = point
            return self._result(LocalizationStatus.JUMP_REJECTED, now, reason="相邻坐标跳变超过阈值，已重置采样")
        self.samples.append(point)
        self.last_sample = point
        if len(self.samples) < self.required_samples:
            return self._result(LocalizationStatus.COLLECTING, now, reason="样本数量不足")
        mean = (sum(point[0] for point in self.samples) / len(self.samples),
                sum(point[1] for point in self.samples) / len(self.samples))
        spread = max(math.dist(point, mean) for point in self.samples)
        if spread <= self.max_spread_mm:
            return self._result(LocalizationStatus.STABLE, now, mean, spread, "坐标已稳定")
        return self._result(LocalizationStatus.COLLECTING, now, mean, spread, "坐标抖动超过阈值")

    def update_from_target(self, target: Dict[str, Any], now_ms: Optional[float] = None) -> LocalizationResult:
        """供已选定目标的调用方使用的简化入口。"""
        return self.update({"targets": [target]}, now_ms)
