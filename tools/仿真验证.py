# -*- coding: utf-8 -*-
"""无硬件仿真验证：检查视觉、标定、协议和状态机的关键边界。"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from vision.相机标定 import CameraCalibration
from vision.颜色追踪 import ColorTracker, load_config
from protocol.串口协议 import Frame, FrameParser, MessageType
from mission.视觉任务状态机 import TaskEvent, TaskState, TaskStateMachine
from vision.精定位 import FineLocalizer
from vision.配置加载 import project_root


DEFAULT_COLOR_CONFIG = project_root() / "config" / "颜色追踪配置.json"
DEFAULT_FINE_CONFIG = project_root() / "config" / "精定位配置.json"
ACTIVE_COLOR_CONFIG = DEFAULT_COLOR_CONFIG
ACTIVE_TIMEOUT_MS: Optional[float] = 50.0


def synthetic_frame(index: int, mode: str, rng: np.random.Generator) -> np.ndarray:
    """生成带已知目标位置和可控缺陷的固定测试画面。"""
    frame = np.full((480, 640, 3), 28, dtype=np.uint8)
    cv2.rectangle(frame, (65, 115), (145, 195), (0, 0, 255), -1)
    cv2.circle(frame, (320, 150), 42, (0, 255, 0), -1)
    cv2.rectangle(frame, (470, 90), (560, 190), (255, 0, 0), -1)
    cv2.circle(frame, (170, 360), 38, (0, 255, 255), -1)
    # 目标连续移动；跨画面跳跃属于重新识别场景，不用于稳定跟踪测试。
    moving_x = 80 + min(index * 4, 430)
    cv2.rectangle(frame, (moving_x, 270), (moving_x + 45, 315), (0, 0, 255), -1)
    if mode == "occlusion":
        cv2.rectangle(frame, (95, 145), (135, 210), (28, 28, 28), -1)
    if mode == "noise":
        noise = rng.normal(0, 12, frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    if mode == "brightness":
        gain = 0.65 + 0.55 * ((index % 30) / 29.0)
        frame = np.clip(frame.astype(np.float32) * gain, 0, 255).astype(np.uint8)
    if mode == "blur":
        frame = cv2.GaussianBlur(frame, (5, 5), 0)
    return frame


def build_tracker(
    timeout_ms: Optional[float] = None,
    color_config_path: Optional[Path] = None,
) -> ColorTracker:
    """从磁盘颜色配置构造追踪器；仅 timeout_ms 允许显式覆盖（仿真性能测试）。"""
    config = load_config(str(color_config_path or ACTIVE_COLOR_CONFIG))
    effective_timeout = ACTIVE_TIMEOUT_MS if timeout_ms is None else timeout_ms
    if effective_timeout is not None:
        config["tracking"]["timeout_ms"] = float(effective_timeout)
    return ColorTracker(["red", "green", "blue", "yellow"], config=config)


def run_visual_scenarios(frames_per_mode: int, rng: np.random.Generator) -> Dict[str, Any]:
    report: Dict[str, Any] = {}
    # confirm_frames 默认 3：前 2 帧无法确认，gate 用稳态窗口避免冷启动假失败。
    warmup_frames = 2
    for mode in ("normal", "brightness", "noise", "blur", "occlusion"):
        tracker = build_tracker()
        confirmed = 0
        steady_confirmed = 0
        total_targets = 0
        elapsed: List[float] = []
        for index in range(frames_per_mode):
            frame = synthetic_frame(index, mode, rng)
            start = time.perf_counter()
            _, result = tracker.track(frame)
            elapsed.append((time.perf_counter() - start) * 1000.0)
            valid = result["targets"]
            total_targets += len(valid)
            if len(valid) >= 4:
                confirmed += 1
                if index >= warmup_frames:
                    steady_confirmed += 1
        steady_den = max(1, frames_per_mode - warmup_frames)
        report[mode] = {
            "frames": frames_per_mode,
            "frames_with_all_four_static_targets": confirmed,
            "confirmation_rate": round(confirmed / frames_per_mode, 4),
            "steady_confirmation_rate": round(steady_confirmed / steady_den, 4),
            "average_valid_targets": round(total_targets / frames_per_mode, 3),
            "avg_ms": round(float(np.mean(elapsed)), 3),
            "p95_ms": round(float(np.percentile(elapsed, 95)), 3),
            "max_ms": round(float(np.max(elapsed)), 3),
        }
    return report


def run_tracking_stability(frames: int, rng: np.random.Generator) -> Dict[str, Any]:
    tracker = build_tracker()
    ids: List[int] = []
    for index in range(frames):
        _, result = tracker.track(synthetic_frame(index, "normal", rng))
        moving = [target for target in result["targets"] if target["center"][1] > 250]
        if moving:
            ids.append(moving[0]["target_id"])
    return {
        "frames": frames,
        "observed_moving_target_ids": sorted(set(ids)),
        "id_switches": sum(current != previous for previous, current in zip(ids, ids[1:])),
    }


def run_boundary_simulation(rng: np.random.Generator) -> Dict[str, Any]:
    """检查丢目标、ROI 外目标和非法图像是否被安全处理。"""
    tracker = build_tracker()
    for index in range(4):
        tracker.track(synthetic_frame(index, "normal", rng))
    _, blank_result = tracker.track(np.full((480, 640, 3), 28, dtype=np.uint8))
    red_result = blank_result["colors"]["red"]
    stale_coordinate_blocked = not blank_result["targets"] and bool(red_result["lost_targets"])

    roi_tracker = build_tracker()
    roi_tracker.roi = (0, 0, 220, 230)
    _, roi_result = roi_tracker.track(synthetic_frame(0, "normal", rng))
    roi_only_red = all(target["color"] == "red" for target in roi_result["targets"])

    invalid_frame_rejected = False
    try:
        tracker.track(None)
    except ValueError:
        invalid_frame_rejected = True
    invalid_roi_rejected = False
    try:
        tracker.roi = (600, 0, 100, 100)
        tracker.track(synthetic_frame(0, "normal", rng))
    except ValueError:
        invalid_roi_rejected = True
    return {
        "stale_coordinate_blocked": stale_coordinate_blocked,
        "roi_only_red": roi_only_red,
        "invalid_frame_rejected": invalid_frame_rejected,
        "invalid_roi_rejected": invalid_roi_rejected,
        "boundary_passed": all((stale_coordinate_blocked, roi_only_red, invalid_frame_rejected, invalid_roi_rejected)),
    }


def run_protocol_simulation(packet_count: int) -> Dict[str, Any]:
    packets = [Frame(MessageType.GRASP, bytes((index % 255,)) + b"payload").encode() for index in range(packet_count)]
    stream = b"\x00\x01noise" + b"".join(packets) + b"trailing"
    parser = FrameParser()
    frames = []
    for start in range(0, len(stream), 7):
        frames.extend(parser.feed(stream[start:start + 7]))
    corrupted = bytearray(packets[0])
    corrupted[4] ^= 0x01
    parser.feed(bytes(corrupted))
    return {
        "sent_valid_packets": packet_count,
        "parsed_valid_packets": len(frames),
        "invalid_frames": parser.invalid_frames,
        "fragmentation_passed": len(frames) == packet_count,
        "corruption_rejected": parser.invalid_frames >= 1,
    }


def run_calibration_simulation() -> Dict[str, Any]:
    calibration = CameraCalibration(
        image_size=(640, 480), camera_matrix=np.eye(3),
        dist_coeffs=np.zeros((5, 1)), reprojection_error_px=0.0,
    )
    homography = np.array([[1.8, 0.1, 20.0], [0.02, 1.6, 30.0], [0.0001, 0.0002, 1.0]])
    image_points = np.array([[30, 30], [600, 30], [600, 430], [30, 430]], dtype=np.float32).reshape(-1, 1, 2)
    world_points = cv2.perspectiveTransform(image_points, homography)
    calibration.set_plane_mapping(image_points.reshape(-1, 2), world_points.reshape(-1, 2))
    errors = []
    for image_point, expected in zip(image_points.reshape(-1, 2), world_points.reshape(-1, 2)):
        actual = calibration.pixel_to_world(image_point)
        errors.append(math.dist(actual, tuple(float(value) for value in expected)))
    return {"max_mapping_error": max(errors), "mapping_passed": max(errors) < 1.0}


def run_state_machine_simulation() -> Dict[str, Any]:
    target = {"target_id": 1, "color": "red", "valid": True, "confidence": 0.9, "world_mm": [100, 200]}
    # 测试场景显式覆盖采样数，其余阈值来自精定位配置文件
    localizer = FineLocalizer.from_config(
        path=DEFAULT_FINE_CONFIG,
        target_id=1,
        required_samples=2,
        max_spread_mm=2,
        max_step_mm=20,
    )
    machine = TaskStateMachine(localizer, state_timeout_ms=100, max_recovery_attempts=1)
    machine.handle(TaskEvent("start"), 0)
    machine.handle(TaskEvent("vision_update", {"targets": [target]}), 10)
    machine.handle(TaskEvent("arrived_near_target"), 20)
    machine.handle(TaskEvent("vision_update", {"targets": [target]}), 30)
    machine.handle(TaskEvent("vision_update", {"targets": [{**target, "world_mm": [101, 201]}]}), 40)
    machine.handle(TaskEvent("grasp_done"), 50)
    machine.handle(TaskEvent("grasp_verified"), 60)
    machine.handle(TaskEvent("place_done"), 70)
    machine.handle(TaskEvent("place_verified"), 80)
    normal_complete = machine.state == TaskState.COMPLETE
    machine.handle(TaskEvent("reset"), 90)
    machine.handle(TaskEvent("start"), 100)
    timeout_action = machine.tick(201)
    return {
        "normal_complete": normal_complete,
        "timeout_recovery_action": timeout_action[0].name if timeout_action else None,
        "state_machine_passed": normal_complete and bool(timeout_action),
    }


def run_simulation(
    frames_per_mode: int,
    packet_count: int,
    seed: int,
    *,
    color_config_path: Optional[Path] = None,
    timeout_ms: Optional[float] = 50.0,
) -> Dict[str, Any]:
    global ACTIVE_COLOR_CONFIG, ACTIVE_TIMEOUT_MS
    ACTIVE_COLOR_CONFIG = color_config_path or DEFAULT_COLOR_CONFIG
    ACTIVE_TIMEOUT_MS = timeout_ms
    rng = np.random.default_rng(seed)
    report = {
        "seed": seed,
        "visual": run_visual_scenarios(frames_per_mode, rng),
        "tracking": run_tracking_stability(frames_per_mode, rng),
        "boundary": run_boundary_simulation(rng),
        "protocol": run_protocol_simulation(packet_count),
        "calibration": run_calibration_simulation(),
        "state_machine": run_state_machine_simulation(),
    }
    report["gates"] = {
        "visual_normal": report["visual"]["normal"].get("steady_confirmation_rate", report["visual"]["normal"]["confirmation_rate"]) >= 0.95,
        "visual_brightness": report["visual"]["brightness"].get("steady_confirmation_rate", report["visual"]["brightness"]["confirmation_rate"]) >= 0.75,
        "visual_noise": report["visual"]["noise"].get("steady_confirmation_rate", report["visual"]["noise"]["confirmation_rate"]) >= 0.50,
        "visual_blur": report["visual"]["blur"].get("steady_confirmation_rate", report["visual"]["blur"]["confirmation_rate"]) >= 0.50,
        "visual_occlusion": report["visual"]["occlusion"].get("steady_confirmation_rate", report["visual"]["occlusion"]["confirmation_rate"]) >= 0.40,
        "tracking_id_stable": report["tracking"]["id_switches"] <= 2,
        "boundary": report["boundary"]["boundary_passed"],
        "protocol": report["protocol"]["fragmentation_passed"] and report["protocol"]["corruption_rejected"],
        "calibration": report["calibration"]["mapping_passed"],
        "state_machine": report["state_machine"]["state_machine_passed"],
    }
    report["passed"] = all(report["gates"].values())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="无硬件深度模拟验证")
    parser.add_argument("--frames", type=int, default=80, help="每种视觉场景的帧数")
    parser.add_argument("--packets", type=int, default=100, help="协议模拟帧数量")
    parser.add_argument("--seed", type=int, default=20260722, help="随机噪声种子")
    parser.add_argument("--output", default="reports/simulation_validation.json", help="JSON 报告路径")
    parser.add_argument(
        "--color-config",
        default=str(DEFAULT_COLOR_CONFIG),
        help="颜色追踪配置 JSON 路径",
    )
    parser.add_argument(
        "--timeout-ms",
        type=float,
        default=50.0,
        help="显式覆盖每帧颜色处理超时（默认对齐生产 50ms）",
    )
    args = parser.parse_args()
    if args.frames < 10 or args.packets < 1:
        raise ValueError("frames 至少为 10，packets 至少为 1")
    report = run_simulation(
        args.frames,
        args.packets,
        args.seed,
        color_config_path=Path(args.color_config),
        timeout_ms=args.timeout_ms,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
