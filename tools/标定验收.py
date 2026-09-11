# -*- coding: utf-8 -*-
"""标定验收：棋盘格数量、重投影误差与可选平面映射门禁。"""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from vision.相机标定 import CameraCalibration, _load_plane_points, calibrate_from_images
from vision.配置加载 import project_root


DEFAULT_MIN_VALID_IMAGES = 6
DEFAULT_MAX_RMS_PX = 0.5
DEFAULT_MAX_PLANE_RMS_MM = 5.0
RECOMMENDED_IMAGES = 10


def evaluate_calibration_gate(
    *,
    image_paths: List[str],
    board_width: int,
    board_height: int,
    square_size: float,
    plane_points_path: Optional[str] = None,
    min_valid_images: int = DEFAULT_MIN_VALID_IMAGES,
    max_rms_px: float = DEFAULT_MAX_RMS_PX,
    max_plane_rms_mm: float = DEFAULT_MAX_PLANE_RMS_MM,
) -> Dict[str, Any]:
    """执行标定并返回带 gates 的验收报告（不写盘）。"""
    errors: List[str] = []
    warnings: List[str] = []
    if len(image_paths) < min_valid_images:
        errors.append(f"匹配图像数 {len(image_paths)} < 最小要求 {min_valid_images}")
    if len(image_paths) < RECOMMENDED_IMAGES:
        warnings.append(
            f"建议采集至少 {RECOMMENDED_IMAGES} 张有效棋盘格图，当前匹配 {len(image_paths)} 张"
        )

    calibration: Optional[CameraCalibration] = None
    plane_error: Optional[float] = None
    try:
        calibration = calibrate_from_images(
            image_paths,
            (board_width, board_height),
            square_size,
            show=False,
        )
    except ValueError as exc:
        errors.append(str(exc))

    if calibration is not None:
        valid = calibration.valid_image_count or 0
        if valid < min_valid_images:
            errors.append(f"有效棋盘格图 {valid} < 最小要求 {min_valid_images}")
        if calibration.reprojection_error_px >= max_rms_px:
            errors.append(
                f"重投影 RMS {calibration.reprojection_error_px:.4f} px >= 门限 {max_rms_px} px"
            )
        if plane_points_path:
            image_points, world_points = _load_plane_points(plane_points_path)
            plane_error = calibration.set_plane_mapping(image_points, world_points)
            if plane_error >= max_plane_rms_mm:
                errors.append(f"平面映射 RMS {plane_error:.4f} mm >= 门限 {max_plane_rms_mm} mm")

    if calibration is None:
        gates = {
            "min_valid_images": False,
            "rms_px": False,
            "plane_rms_mm": plane_points_path is None,
        }
        passed = False
    else:
        gates = {
            "min_valid_images": (calibration.valid_image_count or 0) >= min_valid_images,
            "rms_px": calibration.reprojection_error_px < max_rms_px,
            "plane_rms_mm": (
                True
                if plane_points_path is None
                else (plane_error is not None and plane_error < max_plane_rms_mm)
            ),
        }
        passed = not errors and all(gates.values())

    return {
        "passed": passed,
        "matched_images": len(image_paths),
        "valid_images": None if calibration is None else calibration.valid_image_count,
        "reprojection_error_px": None if calibration is None else calibration.reprojection_error_px,
        "plane_error_mm": plane_error,
        "gates": gates,
        "errors": errors,
        "warnings": warnings,
        "calibration": None if calibration is None else calibration.to_dict(),
        "_calibration_obj": calibration,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="相机标定验收门禁")
    parser.add_argument("--images", required=True, help="标定图像 glob")
    parser.add_argument("--board-width", type=int, default=9)
    parser.add_argument("--board-height", type=int, default=6)
    parser.add_argument("--square-size", type=float, default=25.0)
    parser.add_argument("--plane-points", default=None)
    parser.add_argument("--output", default=None, help="通过时保存标定 JSON")
    parser.add_argument("--report", default="logs/calibration_gate.json", help="验收报告路径")
    parser.add_argument("--min-valid-images", type=int, default=DEFAULT_MIN_VALID_IMAGES)
    parser.add_argument("--max-rms-px", type=float, default=DEFAULT_MAX_RMS_PX)
    parser.add_argument("--max-plane-rms-mm", type=float, default=DEFAULT_MAX_PLANE_RMS_MM)
    args = parser.parse_args()

    image_paths = sorted(glob.glob(args.images))
    if not image_paths:
        report: Dict[str, Any] = {
            "passed": False,
            "errors": [f"没有匹配到标定图像: {args.images}"],
            "warnings": [],
            "gates": {"min_valid_images": False, "rms_px": False, "plane_rms_mm": True},
        }
    else:
        report = evaluate_calibration_gate(
            image_paths=image_paths,
            board_width=args.board_width,
            board_height=args.board_height,
            square_size=args.square_size,
            plane_points_path=args.plane_points,
            min_valid_images=args.min_valid_images,
            max_rms_px=args.max_rms_px,
            max_plane_rms_mm=args.max_plane_rms_mm,
        )
        calibration_obj = report.pop("_calibration_obj", None)
        if report["passed"] and args.output and calibration_obj is not None:
            output = Path(args.output)
            if not output.is_absolute():
                output = project_root() / output
            calibration_obj.save(str(output))
            report["output"] = str(output)
    report.pop("_calibration_obj", None)

    report_path = Path(args.report)
    if not report_path.is_absolute():
        report_path = project_root() / report_path
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
