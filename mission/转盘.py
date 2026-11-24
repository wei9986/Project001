"""第 9 项：转盘剩余时间够不够抓。时间为空时不能放行。"""

from __future__ import annotations

from typing import Optional


def turntable_config_errors(data: Optional[dict]) -> list[str]:
    """和抓取窗口使用同一套时间：识别延迟可以为 0，其余三项必须大于 0。"""
    values = data or {}
    errors = []
    for key in ("min_dwell_ms", "action_ms", "margin_ms"):
        number = values.get(key)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or float(number) <= 0:
            errors.append(key)
    recognition = values.get("recognition_ms")
    if recognition is None or isinstance(recognition, bool) or not isinstance(recognition, (int, float)) or float(recognition) < 0:
        errors.append("recognition_ms")
    return errors


def grab_window_open(
    min_dwell_ms: Optional[float],
    recognition_ms: Optional[float],
    action_ms: Optional[float],
    margin_ms: Optional[float],
    elapsed_ms: Optional[float],
) -> Optional[bool]:
    """任一时间为空就返回空，调用方不得当成可以抓。"""
    if elapsed_ms is None or float(elapsed_ms) < 0:
        return None
    if turntable_config_errors({
        "min_dwell_ms": min_dwell_ms,
        "recognition_ms": recognition_ms,
        "action_ms": action_ms,
        "margin_ms": margin_ms,
    }):
        return None
    remaining = float(min_dwell_ms) - float(elapsed_ms) - float(recognition_ms)
    return remaining >= float(action_ms) + float(margin_ms)
