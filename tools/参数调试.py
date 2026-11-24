# -*- coding: utf-8 -*-
"""HSV 参数调试工具。调试时先看掩膜，再看标注结果；按 s 保存，按 q 退出。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Tuple

import cv2

from vision.颜色追踪 import ColorTracker, load_config


def save_config(config: Dict[str, Any], path: str, latch: Any = None) -> None:
    """保存完整追踪配置到 JSON 文件。一键启动之后锁存会拒绝写入。"""
    if latch is not None and not latch.allows_config_write():
        raise PermissionError("一键启动之后不能保存参数")
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)


def update_hsv_range(
    config: Dict[str, Any], color_name: str, range_index: int, values: Tuple[int, int, int, int, int, int]
) -> None:
    """更新一个 HSV 范围；越界值按通道 clamp，上下限交叉时自动交换。

    调参滑块是独立 trackbar，用户把 low 拖过 high 或拖出边界很常见，
    直接抛异常会让工具退出（P1-11）。与 Qt 版 clamp_hsv_pair 行为对齐。
    """
    if color_name not in config["colors"]:
        raise ValueError(f"未配置颜色: {color_name}")
    ranges = config["colors"][color_name]["ranges"]
    if range_index < 0 or range_index >= len(ranges):
        raise IndexError(f"颜色 {color_name} 没有第 {range_index} 个 HSV 范围")
    h_low, h_high, s_low, s_high, v_low, v_high = (int(value) for value in values)

    def _clamp_pair(low: int, high: int, maximum: int) -> Tuple[int, int]:
        low = max(0, min(maximum, low))
        high = max(0, min(maximum, high))
        return (high, low) if low > high else (low, high)

    h_low, h_high = _clamp_pair(h_low, h_high, 179)
    s_low, s_high = _clamp_pair(s_low, s_high, 255)
    v_low, v_high = _clamp_pair(v_low, v_high, 255)
    ranges[range_index] = [[h_low, s_low, v_low], [h_high, s_high, v_high]]


def _trackbar_values(window: str) -> Tuple[int, int, int, int, int, int]:
    """按固定顺序读取 HSV 滑块值。"""
    return tuple(cv2.getTrackbarPos(name, window) for name in ("H low", "H high", "S low", "S high", "V low", "V high"))


def _set_trackbars(window: str, hsv_range: Any) -> None:
    lower, upper = hsv_range
    for name, value in zip(("H low", "H high", "S low", "S high", "V low", "V high"), (*lower, *upper)):
        cv2.setTrackbarPos(name, window, int(value))


def _create_trackbars(window: str, frame_width: int, frame_height: int) -> None:
    """按当前图像尺寸创建可调控件。"""
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    for name, maximum in (("H low", 179), ("H high", 179), ("S low", 255), ("S high", 255), ("V low", 255), ("V high", 255)):
        cv2.createTrackbar(name, window, 0, maximum, lambda _: None)
    cv2.createTrackbar("min area", window, 0, max(1000, frame_width * frame_height), lambda _: None)
    cv2.createTrackbar("min circularity x100", window, 0, 100, lambda _: None)
    cv2.createTrackbar("min solidity x100", window, 0, 100, lambda _: None)
    cv2.createTrackbar("ROI x", window, 0, max(1, frame_width - 1), lambda _: None)
    cv2.createTrackbar("ROI y", window, 0, max(1, frame_height - 1), lambda _: None)
    cv2.createTrackbar("ROI width", window, frame_width, frame_width, lambda _: None)
    cv2.createTrackbar("ROI height", window, frame_height, frame_height, lambda _: None)


def _set_numeric_trackbars(window: str, config: Dict[str, Any], frame_width: int, frame_height: int) -> None:
    tracking = config["tracking"]
    roi = tracking.get("roi") or [0, 0, frame_width, frame_height]
    values = {
        "min area": int(tracking["min_area"]),
        "min circularity x100": int(float(tracking.get("min_circularity", 0.0)) * 100),
        "min solidity x100": int(float(tracking.get("min_solidity", 0.0)) * 100),
        "ROI x": int(roi[0]), "ROI y": int(roi[1]),
        "ROI width": int(roi[2]), "ROI height": int(roi[3]),
    }
    for name, value in values.items():
        cv2.setTrackbarPos(name, window, value)


def _source_value(source: str) -> Any:
    return int(source) if source.isdigit() else source


def build_parser() -> argparse.ArgumentParser:
    """定义命令行参数。"""
    parser = argparse.ArgumentParser(description="HSV 和跟踪参数调试工具")
    parser.add_argument("--source", required=True, help="摄像头编号、图片或视频路径")
    parser.add_argument("--config", required=True, help="要加载和保存的 JSON 配置路径")
    parser.add_argument("--color", default="red", choices=("red", "green", "blue", "yellow"))
    parser.add_argument("--range-index", type=int, default=0, help="当前颜色的 HSV 范围序号，红色通常需要 0 或 1")
    parser.add_argument("--min-area", type=int, default=None, help="启动时覆盖最小面积")
    return parser


def main() -> None:
    """运行实时调参窗口并显示标注图与掩膜。"""
    args = build_parser().parse_args()
    config = load_config(args.config)
    if args.min_area is not None:
        config["tracking"]["min_area"] = args.min_area
    capture = cv2.VideoCapture(_source_value(args.source))
    if not capture.isOpened():
        raise RuntimeError(f"无法打开输入源: {args.source}")
    ok, first_frame = capture.read()
    if not ok:
        capture.release()
        raise RuntimeError("输入源没有可读取的图像")
    frame_height, frame_width = first_frame.shape[:2]
    window = "HSV tuner"
    _create_trackbars(window, frame_width, frame_height)
    color_ranges = config["colors"][args.color]["ranges"]
    if args.range_index < 0 or args.range_index >= len(color_ranges):
        raise ValueError(f"range_index {args.range_index} 超出 {args.color} 的 ranges 索引范围 0..{len(color_ranges)-1}")
    _set_trackbars(window, color_ranges[args.range_index])
    _set_numeric_trackbars(window, config, frame_width, frame_height)
    tracker = ColorTracker([args.color], config=config)
    pending_frame = first_frame
    try:
        while True:
            frame = pending_frame
            pending_frame = None
            if frame is None:
                ok, frame = capture.read()
                if not ok:
                    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
            values = _trackbar_values(window)
            update_hsv_range(config, args.color, args.range_index, values)
            tracking = config["tracking"]
            tracking["min_area"] = cv2.getTrackbarPos("min area", window)
            tracking["min_circularity"] = cv2.getTrackbarPos("min circularity x100", window) / 100.0
            tracking["min_solidity"] = cv2.getTrackbarPos("min solidity x100", window) / 100.0
            roi = [cv2.getTrackbarPos(name, window) for name in ("ROI x", "ROI y", "ROI width", "ROI height")]
            if roi[2] > 0 and roi[3] > 0 and roi[0] + roi[2] <= frame_width and roi[1] + roi[3] <= frame_height:
                tracking["roi"] = roi
                tracker.roi = tuple(roi)
            tracker.config = config
            annotated, _ = tracker.track(frame)
            cv2.imshow(window, annotated)
            # 显示掩膜时应用与 tracker 相同的 ROI 裁剪
            preprocessed = tracker.preprocess(frame)
            roi_preprocessed, _ = tracker.apply_roi(preprocessed)
            cv2.imshow("HSV mask", tracker.create_mask(roi_preprocessed, args.color))
            key = cv2.waitKey(1) & 0xFF
            if key == ord("s"):
                save_config(config, args.config)
                print(f"配置已保存: {args.config}")
            elif key == ord("q"):
                break
            elif key == ord("r"):
                tracker = ColorTracker([args.color], config=config)
    finally:
        capture.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
