# -*- coding: utf-8 -*-
"""颜色追踪：检测、筛选、关联并确认多个彩色目标。调试时依次看原图、掩膜、轮廓和有效目标。"""

from __future__ import annotations

import argparse
import json
import math
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from vision.相机标定 import CameraCalibration


DEFAULT_CONFIG: Dict[str, Any] = {
    "colors": {
        "red": {
            "ranges": [[[0, 100, 100], [10, 255, 255]], [[160, 100, 100], [179, 255, 255]]],
            "kernel_size": 5,
            "open_iterations": 1,
            "close_iterations": 1,
            "draw_color": [0, 0, 255],
        },
        "green": {
            "ranges": [[[35, 80, 80], [85, 255, 255]]],
            "kernel_size": 5,
            "open_iterations": 1,
            "close_iterations": 1,
            "draw_color": [0, 255, 0],
        },
        "blue": {
            "ranges": [[[90, 80, 80], [130, 255, 255]]],
            "kernel_size": 5,
            "open_iterations": 1,
            "close_iterations": 1,
            "draw_color": [255, 0, 0],
        },
        "yellow": {
            "ranges": [[[20, 100, 100], [35, 255, 255]]],
            "kernel_size": 5,
            "open_iterations": 1,
            "close_iterations": 1,
            "draw_color": [0, 255, 255],
        },
    },
    "tracking": {
        "min_area": 500,
        "max_area": 0,
        "max_targets_per_color": 8,
        "confirm_frames": 3,
        "max_match_distance": 80.0,
        "max_lost_frames": 5,
        "min_circularity": 0.0,
        "min_solidity": 0.0,
        "min_aspect_ratio": 0.0,
        "max_aspect_ratio": 0.0,
        "trail_length": 20,
        "timeout_ms": 50.0,
    },
}


def load_config(path: Optional[str]) -> Dict[str, Any]:
    """读取 JSON 配置，并与安全默认值合并。"""
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is None:
        return config

    with Path(path).open("r", encoding="utf-8") as file:
        user_config = json.load(file)

    if not isinstance(user_config, dict):
        raise ValueError("配置文件根节点必须是 JSON 对象")

    # tracking 做顶层合并；colors 逐色 deep-merge 以避免丢失默认值
    tracking_values = user_config.get("tracking", {})
    if not isinstance(tracking_values, dict):
        raise ValueError("配置项 tracking 必须是 JSON 对象")
    config["tracking"].update(tracking_values)

    for name, values in user_config.get("colors", {}).items():
        if not isinstance(values, dict):
            raise ValueError(f"颜色配置 {name} 必须是 JSON 对象")
        if name not in config["colors"]:
            config["colors"][name] = dict(values)
        else:
            config["colors"][name].update(values)
    return config

def _as_int_tuple(values: Sequence[int]) -> Tuple[int, int, int]:
    return int(values[0]), int(values[1]), int(values[2])


@dataclass
class Target:
    """图像中的候选目标或已确认目标。"""

    target_id: int
    color: str
    center: Tuple[int, int]
    area: float
    radius: float
    angle_deg: float
    width: float
    height: float
    aspect_ratio: float
    circularity: float
    solidity: float
    confidence: float
    status: str
    valid: bool
    lost_frames: int = 0
    world_mm: Optional[Tuple[float, float]] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["center"] = list(self.center)
        result["pixel"] = list(self.center)
        result["world_mm"] = list(self.world_mm) if self.world_mm is not None else None
        return result


@dataclass
class TrackState:
    """用于确认、丢失判断和稳定 ID 的时间状态。"""

    target_id: int
    color: str
    center: Tuple[int, int]
    confirm_count: int = 1
    confirmed: bool = False
    lost_frames: int = 0
    trail: Optional[deque] = None


class DetectionLogger:
    """将每帧结果写成 JSONL，供离线回放。"""

    def __init__(self, path: Optional[str]):
        self.file = None
        if path:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            self.file = output.open("a", encoding="utf-8")

    def write(self, frame_index: int, result: Dict[str, Any]) -> None:
        if self.file is None:
            return
        record = {"frame_index": frame_index, "timestamp": time.time(), **result}
        self.file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.file.flush()

    def close(self) -> None:
        if self.file is not None:
            self.file.close()


class ColorTracker:
    """检测、筛选、关联并确认多颜色目标。"""

    def __init__(
        self,
        color_names: Iterable[str],
        config: Optional[Dict[str, Any]] = None,
        roi: Optional[Tuple[int, int, int, int]] = None,
        logger: Optional[DetectionLogger] = None,
        calibration: Optional[CameraCalibration] = None,
    ):
        self.config = config or json.loads(json.dumps(DEFAULT_CONFIG))
        available = self.config["colors"]
        self.color_names = list(color_names)
        unknown = [name for name in self.color_names if name not in available]
        if unknown:
            raise ValueError(f"未配置的颜色: {', '.join(unknown)}")
        tracking = self.config["tracking"]
        configured_roi = tracking.get("roi")
        self.roi = tuple(configured_roi) if roi is None and configured_roi else roi
        self.logger = logger or DetectionLogger(None)
        self.calibration = calibration
        trail_length = int(tracking["trail_length"])
        self.max_targets = int(tracking["max_targets_per_color"])
        self.tracks: Dict[str, List[TrackState]] = {name: [] for name in self.color_names}
        self.next_target_id = 1
        self.frame_index = 0
        self.last_elapsed_ms = 0.0
        self.trail_length = trail_length
        self.rejected_counts: Dict[str, int] = {name: 0 for name in self.color_names}

    @property
    def tracking(self) -> Dict[str, Any]:
        return self.config["tracking"]

    def validate_roi(self, frame_shape: Sequence[int]) -> None:
        """检查 ROI 是否合法，非法时直接报错。"""
        if self.roi is None:
            return
        height, width = frame_shape[:2]
        x, y, roi_width, roi_height = self.roi
        if x < 0 or y < 0 or roi_width <= 0 or roi_height <= 0:
            raise ValueError("ROI 必须是非负且宽高大于 0 的 (x, y, w, h)")
        if x + roi_width > width or y + roi_height > height:
            raise ValueError(f"ROI 超出画面范围: ROI={self.roi}, frame={(width, height)}")

    def preprocess(self, frame: np.ndarray) -> np.ndarray:
        """执行保守的图像预处理。"""
        blurred = cv2.GaussianBlur(frame, (5, 5), 0)
        return cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    def apply_roi(self, hsv: np.ndarray) -> Tuple[np.ndarray, Tuple[int, int]]:
        if self.roi is None:
            return hsv, (0, 0)
        x, y, width, height = self.roi
        return hsv[y:y + height, x:x + width], (x, y)

    def create_mask(self, hsv: np.ndarray, color_name: str) -> np.ndarray:
        """生成指定颜色的干净二值掩膜。"""
        color_config = self.config["colors"][color_name]
        mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
        for lower, upper in color_config["ranges"]:
            mask = cv2.bitwise_or(
                mask,
                cv2.inRange(hsv, np.array(lower, dtype=np.uint8), np.array(upper, dtype=np.uint8)),
            )
        kernel_size = int(color_config.get("kernel_size", 5))
        kernel = np.ones((kernel_size, kernel_size), dtype=np.uint8)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_OPEN, kernel, iterations=int(color_config.get("open_iterations", 1))
        )
        return cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE, kernel, iterations=int(color_config.get("close_iterations", 1))
        )

    def _shape_measurements(self, contour: np.ndarray) -> Tuple[float, float, float, float, float, float]:
        area = float(cv2.contourArea(contour))
        perimeter = float(cv2.arcLength(contour, True))
        circularity = 4.0 * math.pi * area / (perimeter * perimeter) if perimeter else 0.0
        hull_area = float(cv2.contourArea(cv2.convexHull(contour)))
        solidity = area / hull_area if hull_area else 0.0
        rect = cv2.minAreaRect(contour)
        width, height = sorted((float(rect[1][0]), float(rect[1][1])), reverse=True)
        aspect_ratio = width / height if height else 0.0
        return area, circularity, solidity, width, height, aspect_ratio

    def _passes_shape_filter(self, values: Tuple[float, float, float, float, float, float]) -> bool:
        area, circularity, solidity, _, _, aspect_ratio = values
        tracking = self.tracking
        max_area = float(tracking.get("max_area", 0))
        if area < float(tracking["min_area"]):
            return False
        if max_area > 0 and area > max_area:
            return False
        if circularity < float(tracking.get("min_circularity", 0)):
            return False
        if solidity < float(tracking.get("min_solidity", 0)):
            return False
        min_ratio = float(tracking.get("min_aspect_ratio", 0))
        max_ratio = float(tracking.get("max_aspect_ratio", 0))
        return not (aspect_ratio < min_ratio or (max_ratio > 0 and aspect_ratio > max_ratio))

    def _candidate_targets(
        self, mask: np.ndarray, color_name: str, offset: Tuple[int, int]
    ) -> List[Target]:
        """将掩膜轮廓转换为几何候选目标。"""
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        dx, dy = offset
        candidates: List[Target] = []
        self.rejected_counts[color_name] = 0
        for contour in contours:
            measurements = self._shape_measurements(contour)
            if not self._passes_shape_filter(measurements):
                self.rejected_counts[color_name] += 1
                continue
            area, circularity, solidity, width, height, aspect_ratio = measurements
            moments = cv2.moments(contour)
            if moments["m00"] == 0:
                continue
            center = (
                int(moments["m10"] / moments["m00"]) + dx,
                int(moments["m01"] / moments["m00"]) + dy,
            )
            (local_x, local_y), radius = cv2.minEnclosingCircle(contour)
            rect = cv2.minAreaRect(contour)
            confidence = min(1.0, 0.5 * min(1.0, area / float(self.tracking["min_area"] * 4))
                             + 0.25 * circularity + 0.25 * solidity)
            candidates.append(Target(
                target_id=0,
                color=color_name,
                center=center,
                area=area,
                radius=float(radius),
                angle_deg=float(rect[2]),
                width=width,
                height=height,
                aspect_ratio=aspect_ratio,
                circularity=circularity,
                solidity=solidity,
                confidence=confidence,
                status="candidate",
                valid=False,
            ))
        candidates.sort(key=lambda item: item.area, reverse=True)
        return candidates[:self.max_targets]

    def _associate(self, color_name: str, candidates: List[Target]) -> List[Target]:
        """用最近邻方法保持目标 ID 稳定。"""
        tracks = self.tracks[color_name]
        max_distance = float(self.tracking["max_match_distance"])
        unmatched_tracks = set(range(len(tracks)))
        matched_candidates: List[Target] = []
        for candidate in candidates:
            best_index = None
            best_distance = max_distance
            for index in unmatched_tracks:
                distance = math.dist(candidate.center, tracks[index].center)
                if distance <= best_distance:
                    best_index, best_distance = index, distance
            if best_index is None:
                track = TrackState(
                    target_id=self.next_target_id,
                    color=color_name,
                    center=candidate.center,
                    trail=deque(maxlen=self.trail_length) if self.trail_length > 0 else None,
                )
                self.next_target_id += 1
                tracks.append(track)
                is_new_track = True
            else:
                unmatched_tracks.remove(best_index)
                track = tracks[best_index]
                is_new_track = False

            track.center = candidate.center
            if not is_new_track:
                track.confirm_count = track.confirm_count + 1 if track.lost_frames == 0 else 1
            track.lost_frames = 0
            track.confirmed = track.confirm_count >= int(self.tracking["confirm_frames"])
            if track.trail is not None:
                track.trail.appendleft(candidate.center)
            candidate.target_id = track.target_id
            candidate.status = "confirmed" if track.confirmed else "tentative"
            candidate.valid = track.confirmed
            matched_candidates.append(candidate)

        max_lost = int(self.tracking["max_lost_frames"])
        retained_tracks: List[TrackState] = []
        matched_ids = {target.target_id for target in matched_candidates}
        for index, track in enumerate(tracks):
            if track.target_id in matched_ids:
                retained_tracks.append(track)
                continue
            track.lost_frames += 1
            track.confirmed = False
            if track.lost_frames <= max_lost:
                retained_tracks.append(track)
        self.tracks[color_name] = retained_tracks
        return matched_candidates

    def _empty_result(self, status: str) -> Dict[str, Any]:
        return {
            "status": status,
            "valid_targets": [],
            "candidates": [],
            "lost_targets": [],
            "rejected_count": 0,
            "timed_out": status == "timeout",
        }

    def _attach_world_coordinates(self, targets: List[Target]) -> None:
        """仅在映射完整时附加平面世界坐标。"""
        if self.calibration is None or not self.calibration.has_plane_mapping:
            return
        for target in targets:
            target.world_mm = self.calibration.pixel_to_world(
                target.center, already_undistorted=self.calibration is not None
            )

    def track(self, frame: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """处理一帧图像，返回标注图和结构化结果。"""
        if frame is None:
            raise ValueError("frame 不能为 None")
        if frame.ndim == 2:
            frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
        elif frame.ndim == 3 and frame.shape[2] == 4:
            frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        elif frame.ndim != 3 or frame.shape[2] != 3:
            raise ValueError(f"frame 必须是 BGR 三通道图像，实际 shape={frame.shape}")
        if self.calibration is not None:
            frame = self.calibration.undistort(frame)
        self.validate_roi(frame.shape)
        self.frame_index += 1
        start = cv2.getTickCount()
        hsv = self.preprocess(frame)
        roi_hsv, offset = self.apply_roi(hsv)
        results: Dict[str, Any] = {
            "schema_version": "2.0",
            "frame_index": self.frame_index,
            "coordinate_status": (
                "world_mm" if self.calibration is not None and self.calibration.has_plane_mapping else "pixel_only"
            ),
            "targets": [],
            "colors": {},
        }
        timed_out = False
        timeout_ms = float(self.tracking["timeout_ms"])

        for color_name in self.color_names:
            elapsed_ms = (cv2.getTickCount() - start) / cv2.getTickFrequency() * 1000.0
            if elapsed_ms > timeout_ms:
                timed_out = True
                results["colors"][color_name] = self._empty_result("timeout")
                continue
            mask = self.create_mask(roi_hsv, color_name)
            candidates = self._candidate_targets(mask, color_name, offset)
            associated = self._associate(color_name, candidates)
            self._attach_world_coordinates(associated)
            valid = [target for target in associated if target.valid]
            lost = [
                {
                    "target_id": track.target_id,
                    "color": color_name,
                    "center": list(track.center),
                    "status": "lost",
                    "valid": False,
                    "lost_frames": track.lost_frames,
                }
                for track in self.tracks[color_name]
                if track.lost_frames > 0
            ]
            results["colors"][color_name] = {
                "status": "ok" if valid else ("tentative" if associated else (
                    "rejected" if self.rejected_counts[color_name] else "no_target")),
                "valid_targets": [target.to_dict() for target in valid],
                "candidates": [target.to_dict() for target in associated],
                "lost_targets": lost,
                "rejected_count": self.rejected_counts[color_name],
                "timed_out": False,
            }
            results["targets"].extend(target.to_dict() for target in valid)
            self._draw_targets(frame, associated, color_name)

        self.last_elapsed_ms = (cv2.getTickCount() - start) / cv2.getTickFrequency() * 1000.0
        results["elapsed_ms"] = round(self.last_elapsed_ms, 3)
        results["fps"] = round(1000.0 / self.last_elapsed_ms, 2) if self.last_elapsed_ms else 0.0
        results["timed_out"] = timed_out
        self._draw_overlay(frame, results)
        self.logger.write(self.frame_index, results)
        return frame, results

    def _draw_targets(self, frame: np.ndarray, targets: List[Target], color_name: str) -> None:
        color = _as_int_tuple(self.config["colors"][color_name].get("draw_color", [255, 255, 255]))
        for target in targets:
            track = next((item for item in self.tracks[color_name] if item.target_id == target.target_id), None)
            if track is not None and track.trail is not None:
                trail_points = list(track.trail)
                for index in range(1, len(trail_points)):
                    thickness = max(1, 3 - index // 6)
                    cv2.line(frame, trail_points[index - 1], trail_points[index], color, thickness)
            x, y = target.center
            radius = max(4, int(target.radius))
            thickness = 3 if target.valid else 1
            cv2.circle(frame, target.center, radius, color, thickness)
            cv2.drawMarker(frame, target.center, color, cv2.MARKER_CROSS, 16, 2)
            label = f"#{target.target_id} {target.status} A={int(target.area)}"
            cv2.putText(frame, label, (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.45, color, 1, cv2.LINE_AA)

    def _draw_overlay(self, frame: np.ndarray, results: Dict[str, Any]) -> None:
        text = f"Frame {self.frame_index} | {results['elapsed_ms']:.1f}ms | FPS {results['fps']:.1f}"
        if results["timed_out"]:
            text += " | TIMEOUT"
        cv2.putText(frame, text, (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)


def parse_source(source: str) -> Any:
    return int(source) if source.isdigit() else source


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="多目标 HSV 颜色检测与跟踪")
    parser.add_argument("--source", default="0", help="摄像头编号或视频文件路径")
    parser.add_argument("--config", default=None, help="JSON 配置文件路径")
    parser.add_argument("--colors", nargs="+", default=["red", "green", "blue"], help="要检测的颜色名称")
    parser.add_argument("--roi", type=int, nargs=4, default=None, metavar=("X", "Y", "W", "H"),
                        help="覆盖配置文件中的 ROI")
    parser.add_argument("--log", default=None, help="JSONL 检测日志路径")
    parser.add_argument("--save-debug", default=None, help="保存带标注视频的路径")
    parser.add_argument("--calibration", default=None, help="相机标定 JSON 文件路径")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = load_config(args.config)
    logger = DetectionLogger(args.log)
    calibration = CameraCalibration.from_file(args.calibration) if args.calibration else None
    configured_roi = config["tracking"].get("roi")
    roi = tuple(args.roi) if args.roi else (tuple(configured_roi) if configured_roi else None)
    tracker = ColorTracker(args.colors, config=config, roi=roi,
                           logger=logger, calibration=calibration)
    capture = cv2.VideoCapture(parse_source(args.source))
    if not capture.isOpened():
        logger.close()
        raise RuntimeError(f"无法打开视频源: {args.source}")
    writer = None
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            annotated, _ = tracker.track(frame)
            if args.save_debug and writer is None:
                height, width = annotated.shape[:2]
                fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
                writer = cv2.VideoWriter(args.save_debug, cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
            if writer is not None:
                writer.write(annotated)
            cv2.imshow("HSV Multi-target Tracking", annotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        logger.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
