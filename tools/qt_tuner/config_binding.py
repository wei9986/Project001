# -*- coding: utf-8 -*-
"""把界面控件值写回颜色追踪配置字典（纯逻辑，便于单测）。"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple  # Any used by live camera source

from tools.参数调试 import update_hsv_range


def clamp_hsv_pair(
    h_low: int, h_high: int, s_low: int, s_high: int, v_low: int, v_high: int
) -> Tuple[int, int, int, int, int, int]:
    """保证上下限合法且 low <= high。"""
    h_low = max(0, min(179, int(h_low)))
    h_high = max(0, min(179, int(h_high)))
    s_low = max(0, min(255, int(s_low)))
    s_high = max(0, min(255, int(s_high)))
    v_low = max(0, min(255, int(v_low)))
    v_high = max(0, min(255, int(v_high)))
    if h_low > h_high:
        h_low, h_high = h_high, h_low
    if s_low > s_high:
        s_low, s_high = s_high, s_low
    if v_low > v_high:
        v_low, v_high = v_high, v_low
    return h_low, h_high, s_low, s_high, v_low, v_high


def apply_hsv_sliders(
    config: Dict[str, Any],
    color_name: str,
    range_index: int,
    values: Sequence[int],
) -> None:
    """将六个 HSV 滑块值写入指定颜色的 ranges[range_index]。"""
    if len(values) != 6:
        raise ValueError("HSV 滑块值必须是 6 个整数")
    clamped = clamp_hsv_pair(*values)
    update_hsv_range(config, color_name, range_index, clamped)


def apply_tracking_values(
    config: Dict[str, Any],
    *,
    min_area: int,
    max_area: int,
    confirm_frames: int,
    max_match_distance: float,
    max_lost_frames: int,
    min_circularity: float,
    min_solidity: float,
    roi_enabled: bool,
    roi: Optional[Sequence[int]] = None,
) -> None:
    """写回 tracking 段关键阈值。"""
    tracking = config.setdefault("tracking", {})
    tracking["min_area"] = max(0, int(min_area))
    tracking["max_area"] = max(0, int(max_area))
    tracking["confirm_frames"] = max(1, int(confirm_frames))
    tracking["max_match_distance"] = max(0.0, float(max_match_distance))
    tracking["max_lost_frames"] = max(0, int(max_lost_frames))
    tracking["min_circularity"] = max(0.0, min(1.0, float(min_circularity)))
    tracking["min_solidity"] = max(0.0, min(1.0, float(min_solidity)))
    if roi_enabled and roi is not None and len(roi) == 4:
        x, y, w, h = (int(v) for v in roi)
        if w > 0 and h > 0 and x >= 0 and y >= 0:
            tracking["roi"] = [x, y, w, h]
        else:
            tracking["roi"] = None
    else:
        tracking["roi"] = None


def apply_color_morphology(
    config: Dict[str, Any],
    color_name: str,
    *,
    kernel_size: int,
    open_iterations: int,
    close_iterations: int,
) -> None:
    """写回当前颜色的形态学参数。"""
    color = config["colors"][color_name]
    ksize = max(1, int(kernel_size))
    if ksize % 2 == 0:
        ksize += 1
    color["kernel_size"] = ksize
    color["open_iterations"] = max(0, int(open_iterations))
    color["close_iterations"] = max(0, int(close_iterations))


def apply_preprocess_values(
    config: Dict[str, Any],
    *,
    blur_ksize: int,
    blur_sigma: float,
) -> None:
    """写回 preprocess 段。"""
    preprocess = config.setdefault("preprocess", {})
    ksize = max(1, int(blur_ksize))
    if ksize % 2 == 0:
        ksize += 1
    preprocess["blur_ksize"] = ksize
    preprocess["blur_sigma"] = max(0.0, float(blur_sigma))


def read_hsv_from_config(
    config: Dict[str, Any], color_name: str, range_index: int
) -> List[int]:
    """读取当前 range 的 [Hlow,Hhigh,Slow,Shigh,Vlow,Vhigh]。"""
    lower, upper = config["colors"][color_name]["ranges"][range_index]
    return [int(lower[0]), int(upper[0]), int(lower[1]), int(upper[1]), int(lower[2]), int(upper[2])]


def build_station_config(
    *,
    dictionary: str,
    tag_size_mm: float,
    stations: Dict[str, str],
) -> Dict[str, Any]:
    """构造可保存的站点配置（stations 整表）。"""
    if tag_size_mm <= 0:
        raise ValueError("tag_size_mm 必须为正数")
    if not dictionary:
        raise ValueError("dictionary 不能为空")
    normalized: Dict[str, str] = {}
    for key, value in stations.items():
        text = str(key).strip()
        if not text:
            continue
        try:
            int(text)
        except ValueError as exc:
            raise ValueError(f"tag_id 必须是整数: {text}") from exc
        name = str(value).strip()
        if not name:
            raise ValueError(f"station_id 不能为空 (tag_id={text})")
        normalized[text] = name
    return {
        "dictionary": str(dictionary),
        "tag_size_mm": float(tag_size_mm),
        "stations": normalized,
    }


def build_fine_localization_config(
    *,
    required_samples: int,
    max_spread_mm: float,
    max_step_mm: float,
    timeout_ms: float,
    lost_timeout_ms: float,
) -> Dict[str, Any]:
    """构造可保存的精定位配置。"""
    if required_samples < 2:
        raise ValueError("required_samples 至少为 2")
    for name, value in (
        ("max_spread_mm", max_spread_mm),
        ("max_step_mm", max_step_mm),
        ("timeout_ms", timeout_ms),
        ("lost_timeout_ms", lost_timeout_ms),
    ):
        if float(value) <= 0:
            raise ValueError(f"{name} 必须为正数")
    return {
        "required_samples": int(required_samples),
        "max_spread_mm": float(max_spread_mm),
        "max_step_mm": float(max_step_mm),
        "timeout_ms": float(timeout_ms),
        "lost_timeout_ms": float(lost_timeout_ms),
    }


def apply_live_basic_values(
    live_config: Dict[str, Any],
    *,
    camera_source: Any,
    width: int,
    height: int,
    fps: int,
    colors: Sequence[str],
    minimum_tof_mm: float,
    display: bool,
    sleep_s: float,
) -> None:
    """写回实机配置中与调试相关的基础字段。"""
    camera = live_config.setdefault("camera", {})
    if isinstance(camera_source, str) and camera_source.isdigit():
        camera["source"] = int(camera_source)
    else:
        camera["source"] = camera_source
    camera["width"] = max(1, int(width))
    camera["height"] = max(1, int(height))
    camera["fps"] = max(1, int(fps))
    live_config["colors"] = [str(name) for name in colors]
    safety = live_config.setdefault("safety", {})
    if minimum_tof_mm <= 0:
        raise ValueError("minimum_tof_mm 必须为正数")
    safety["minimum_tof_mm"] = float(minimum_tof_mm)
    live_config["display"] = bool(display)
    loop = live_config.setdefault("loop", {})
    loop["sleep_s"] = max(0.0, float(sleep_s))
