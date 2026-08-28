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
    """校验并规范化精定位参数。"""
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
    """读取精定位 JSON，并与安全默认值合并；overrides 可覆盖路径内容。"""
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
        self.samples: Deque[Tuple[float, float]] = deque(maxlen=required_samples)
        self.started_ms: Optional[float] = None
        self.last_seen_ms: Optional[float] = None
        self.last_sample: Optional[Tuple[float, float]] = None
        self.terminal_status: Optional[LocalizationStatus] = None

    @classmethod
    def from_config(
        cls,
        config: Optional[Dict[str, Any]] = None,
        *,
        path: Optional[Union[str, Any]] = None,
        target_id: Optional[int] = None,
        color: Optional[str] = None,
        **overrides: Any,
    ) -> "FineLocalizer":
        """从配置字典或 JSON 路径构造精定位器；overrides 可覆盖单项阈值。"""
        if config is not None and path is not None:
            raise ValueError("不能同时指定 config 与 path")
        if config is not None:
            loaded = normalize_fine_localization_config({**DEFAULT_CONFIG, **config, **overrides})
        else:
            loaded = load_fine_localization_config(path, overrides=overrides or None)
        return cls(
            required_samples=int(loaded["required_samples"]),
            max_spread_mm=float(loaded["max_spread_mm"]),
            max_step_mm=float(loaded["max_step_mm"]),
            timeout_ms=float(loaded["timeout_ms"]),
            lost_timeout_ms=float(loaded["lost_timeout_ms"]),
            target_id=target_id,
            color=color,
            auto_reset_on_lost=bool(loaded.get("auto_reset_on_lost", False)),
            auto_reset_on_timeout=bool(loaded.get("auto_reset_on_timeout", False)),
        )

    def reset(self, now_ms: Optional[float] = None) -> None:
        """开始新的精定位，清空旧样本。"""
        self.samples.clear()
        self.started_ms = time.monotonic() * 1000 if now_ms is None else now_ms
        self.last_seen_ms = None
        self.last_sample = None
        self.terminal_status = None

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
                spread: Optional[float] = None, reason: str = "", needs_reset: bool = False) -> LocalizationResult:
        started = now_ms if self.started_ms is None else self.started_ms
        return LocalizationResult(status, status == LocalizationStatus.STABLE, coordinate,
                                  len(self.samples), spread, max(0.0, now_ms - started), reason, needs_reset)

    def _terminal_result(self, status: LocalizationStatus, now_ms: float, reason: str) -> LocalizationResult:
        self.terminal_status = status
        return self._result(status, now_ms, reason=reason, needs_reset=True)

    def update(self, vision_result: Dict[str, Any], now_ms: Optional[float] = None) -> LocalizationResult:
        """用一帧结构化识别结果推进精定位。"""
        now = time.monotonic() * 1000 if now_ms is None else now_ms
        if self.terminal_status is not None:
            if ((self.terminal_status == LocalizationStatus.LOST and self.auto_reset_on_lost)
                    or (self.terminal_status == LocalizationStatus.TIMEOUT and self.auto_reset_on_timeout)):
                self.reset(now)
            else:
                return self._result(
                    self.terminal_status,
                    now,
                    reason="精定位已进入终止态，请调用 reset() 后重新开始",
                    needs_reset=True,
                )
        if self.started_ms is None:
            self.reset(now)
        if now - self.started_ms > self.timeout_ms:  # pyright: ignore[reportOperatorIssue]
            return self._terminal_result(LocalizationStatus.TIMEOUT, now, "二次定位超时")
        target = self._select_target(vision_result)
        if target is None:
            if self.last_seen_ms is not None and now - self.last_seen_ms > self.lost_timeout_ms:
                return self._terminal_result(LocalizationStatus.LOST, now, "目标连续丢失")
            return self._result(LocalizationStatus.WAITING, now, reason="等待目标")
        coordinate = target.get("world_mm")
        if not coordinate or len(coordinate) != 2:
            return self._result(LocalizationStatus.NO_COORDINATE, now, reason="目标没有 world_mm")
        point = (float(coordinate[0]), float(coordinate[1]))
        self.last_seen_ms = now
        if self.last_sample is not None and math.dist(point, self.last_sample) > self.max_step_mm:
            self.samples.clear()
            self.samples.append(point)
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
