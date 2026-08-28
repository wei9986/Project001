# -*- coding: utf-8 -*-
"""精定位：抓取前连续采样有效世界坐标，确认稳定后才允许抓取。"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Deque, Dict, Optional, Tuple, Union

from vision.配置加载 import deep_merge, load_merged_config, resolve_path


DEFAULT_CONFIG: Dict[str, Any] = {
    "required_samples": 5,
    "max_spread_mm": 5.0,
    "max_step_mm": 30.0,
    "timeout_ms": 1500.0,
    "lost_timeout_ms": 300.0,
    "auto_reset_on_lost": False,
    "auto_reset_on_timeout": False,
}


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
    needs_reset: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "valid": self.valid,
            "coordinate_mm": list(self.coordinate_mm) if self.coordinate_mm else None,
            "sample_count": self.sample_count,
            "spread_mm": self.spread_mm,
            "elapsed_ms": self.elapsed_ms,
            "reason": self.reason,
            "needs_reset": self.needs_reset,
        }


def normalize_fine_localization_config(config: Dict[str, Any]) -> Dict[str, Any]:
    required_samples = int(config["required_samples"])
    max_spread_mm = float(config["max_spread_mm"])
    max_step_mm = float(config["max_step_mm"])
    timeout_ms = float(config["timeout_ms"])
    lost_timeout_ms = float(config["lost_timeout_ms"])
    auto_reset_on_lost = bool(config.get("auto_reset_on_lost", False))
    auto_reset_on_timeout = bool(config.get("auto_reset_on_timeout", False))
    if required_samples < 2 or max_spread_mm <= 0 or max_step_mm <= 0 or timeout_ms <= 0 or lost_timeout_ms <= 0:
        raise ValueError("二次定位参数必须为正数，required_samples 至少为 2")
    return {
        "required_samples": required_samples,
        "max_spread_mm": max_spread_mm,
        "max_step_mm": max_step_mm,
        "timeout_ms": timeout_ms,
        "lost_timeout_ms": lost_timeout_ms,
        "auto_reset_on_lost": auto_reset_on_lost,
        "auto_reset_on_timeout": auto_reset_on_timeout,
    }


def load_fine_localization_config(
    path: Optional[Union[str, Any]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    resolved = resolve_path(path) if path else None
    config = load_merged_config(resolved, DEFAULT_CONFIG)
    if overrides:
        config = deep_merge(config, overrides)
    return normalize_fine_localization_config(config)


class FineLocalizer:
    """收集一个选定目标的稳定世界坐标样本。"""

    def __init__(self, required_samples: int = 5, max_spread_mm: float = 5.0,
                 max_step_mm: float = 30.0, timeout_ms: float = 1500.0,
                 lost_timeout_ms: float = 300.0, target_id: Optional[int] = None,
                 color: Optional[str] = None, auto_reset_on_lost: bool = False,
                 auto_reset_on_timeout: bool = False) -> None:
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
        self.auto_reset_on_lost = auto_reset_on_lost
        self.auto_reset_on_timeout = auto_reset_on_timeout
        self._samples: Deque[Tuple[float, float]] = deque(maxlen=required_samples)
        self._start_ms: Optional[float] = None
        self._last_sample: Optional[Tuple[float, float]] = None
        self._last_sample_ms: float = 0.0

    @classmethod
    def from_config(cls, config: Dict[str, Any], *, target_id: Optional[int] = None, color: Optional[str] = None) -> "FineLocalizer":
        return cls(
            required_samples=config["required_samples"],
            max_spread_mm=config["max_spread_mm"],
            max_step_mm=config["max_step_mm"],
            timeout_ms=config["timeout_ms"],
            lost_timeout_ms=config["lost_timeout_ms"],
            target_id=target_id,
            color=color,
            auto_reset_on_lost=config.get("auto_reset_on_lost", False),
            auto_reset_on_timeout=config.get("auto_reset_on_timeout", False),
        )

    def reset(self, now_ms: float) -> None:
        self._samples.clear()
        self._start_ms = now_ms
        self._last_sample = None
        self._last_sample_ms = 0.0

    def _select_target(self, vision_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        targets = vision_result.get("targets", [])
        if not targets:
            return None
        if self.target_id is not None:
            for target in targets:
                if target.get("target_id") == self.target_id and target.get("world_mm"):
                    return target
        if self.color is not None:
            for target in targets:
                if target.get("color") == self.color and target.get("world_mm"):
                    return target
        return None

    def update(self, vision_result: Dict[str, Any], now_ms: Optional[float] = None) -> LocalizationResult:
        if now_ms is None:
            now_ms = time.monotonic() * 1000.0
        if self._start_ms is None:
            self.reset(now_ms)
        target = self._select_target(vision_result)
        if target is None:
            if self._samples:
                if now_ms - self._last_sample_ms > self.lost_timeout_ms:
                    if self.auto_reset_on_lost:
                        self.reset(now_ms)
                        return self._result(LocalizationStatus.WAITING, now_ms, reason="目标丢失，已自动重置")
                    return self._result(LocalizationStatus.LOST, now_ms, reason="目标丢失")
            return self._result(LocalizationStatus.WAITING, now_ms, reason="等待目标")
        coord = target.get("world_mm")
        if not coord or len(coord) < 2:
            return self._result(LocalizationStatus.NO_COORDINATE, now_ms, reason="无世界坐标")
        sample = (float(coord[0]), float(coord[1]))
        if self._last_sample is not None:
            step = math.dist(sample, self._last_sample)
            if step > self.max_step_mm:
                if self.auto_reset_on_lost:
                    self.reset(now_ms)
                    return self._result(LocalizationStatus.JUMP_REJECTED, now_ms, reason=f"跳变 {step:.1f}mm，已重置")
                return self._result(LocalizationStatus.JUMP_REJECTED, now_ms, reason=f"跳变 {step:.1f}mm")
        self._samples.append(sample)
        self._last_sample = sample
        self._last_sample_ms = now_ms
        if len(self._samples) < self.required_samples:
            return self._result(LocalizationStatus.COLLECTING, now_ms, reason=f"采样 {len(self._samples)}/{self.required_samples}")
        xs = [s[0] for s in self._samples]
        ys = [s[1] for s in self._samples]
        spread = max(max(xs) - min(xs), max(ys) - min(ys))
        if spread > self.max_spread_mm:
            return self._result(LocalizationStatus.COLLECTING, now_ms, reason=f"离散度 {spread:.1f}mm 超阈值")
        avg = (sum(xs) / len(xs), sum(ys) / len(ys))
        return LocalizationResult(
            status=LocalizationStatus.STABLE,
            valid=True,
            coordinate_mm=avg,
            sample_count=len(self._samples),
            spread_mm=spread,
            elapsed_ms=now_ms - self._start_ms,
            reason="坐标稳定",
        )

    def _result(self, status: LocalizationStatus, now_ms: float, *, reason: str = "") -> LocalizationResult:
        needs_reset = False
        if status == LocalizationStatus.TIMEOUT:
            needs_reset = self.auto_reset_on_timeout
            if needs_reset:
                self.reset(now_ms)
        elapsed = now_ms - (self._start_ms or now_ms)
        if elapsed > self.timeout_ms:
            if self.auto_reset_on_timeout:
                self.reset(now_ms)
                return LocalizationResult(LocalizationStatus.WAITING, False, None, len(self._samples), None, 0.0, "超时已重置", True)
            return LocalizationResult(LocalizationStatus.TIMEOUT, False, None, len(self._samples), None, elapsed, "超时", True)
        return LocalizationResult(status, False, None, len(self._samples), None, elapsed, reason, False)
