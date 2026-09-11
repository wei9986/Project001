# -*- coding: utf-8 -*-
"""相机标定：建立像素到平面世界坐标的转换。标定后必须固定相机位置和分辨率。"""

from __future__ import annotations

import argparse
import glob
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import cv2
import numpy as np


def _matrix(value: Any, name: str) -> np.ndarray:
    matrix = np.asarray(value, dtype=np.float64)
    if matrix.size == 0:
        raise ValueError(f"{name} 不能为空")
    return matrix


@dataclass
class CameraCalibration:
    """保存的相机内参与可选平面映射。"""

    image_size: Tuple[int, int]
    camera_matrix: np.ndarray
    dist_coeffs: np.ndarray
    reprojection_error_px: float
    homography: Optional[np.ndarray] = None
    world_unit: str = "mm"
    valid_image_count: Optional[int] = None

    @property
    def has_plane_mapping(self) -> bool:
        return self.homography is not None

    @classmethod
    def from_file(cls, path: str) -> "CameraCalibration":
        """从可版本管理的 JSON 文件读取标定结果。"""
        with Path(path).open("r", encoding="utf-8") as file:
            data = json.load(file)
        image_size = tuple(int(value) for value in data["image_size"])
        if len(image_size) != 2 or min(image_size) <= 0:
            raise ValueError("image_size 必须是正整数 [width, height]")
        camera_matrix = _matrix(data["camera_matrix"], "camera_matrix")
        if camera_matrix.shape != (3, 3):
            raise ValueError(f"camera_matrix 必须是 3x3，实际 shape={camera_matrix.shape}")
        dist_coeffs = _matrix(data["dist_coeffs"], "dist_coeffs")
        if dist_coeffs.ndim != 2 or (dist_coeffs.shape[0] != 1 and dist_coeffs.shape[1] != 1):
            raise ValueError(f"dist_coeffs 必须是 1xN 或 Nx1 向量，实际 shape={dist_coeffs.shape}")
        if min(dist_coeffs.shape) != 1 or max(dist_coeffs.shape) < 4:
            raise ValueError(f"dist_coeffs 至少需要 4 个系数，实际 shape={dist_coeffs.shape}")
        homography = data.get("homography")
        if homography is not None:
            homography = _matrix(homography, "homography")
            if homography.shape != (3, 3):
                raise ValueError(f"homography 必须是 3x3，实际 shape={homography.shape}")
        return cls(
            image_size=(image_size[0], image_size[1]),
            camera_matrix=camera_matrix,
            dist_coeffs=dist_coeffs,
            reprojection_error_px=float(data.get("reprojection_error_px", 0.0)),
            homography=homography,
            world_unit=str(data.get("world_unit", "mm")),
            valid_image_count=(int(data["valid_image_count"]) if data.get("valid_image_count") is not None else None),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "image_size": list(self.image_size),
            "camera_matrix": self.camera_matrix.tolist(),
            "dist_coeffs": self.dist_coeffs.tolist(),
            "reprojection_error_px": self.reprojection_error_px,
            "homography": self.homography.tolist() if self.homography is not None else None,
            "world_unit": self.world_unit,
            "valid_image_count": self.valid_image_count,
        }

    def save(self, path: str) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as file:
            json.dump(self.to_dict(), file, ensure_ascii=False, indent=2)

    def undistort(self, frame: np.ndarray) -> np.ndarray:
        """去除镜头畸变；输入分辨率必须与标定一致。"""
        height, width = frame.shape[:2]
        if (width, height) != self.image_size:
            raise ValueError(
                f"输入画面分辨率 {(width, height)} 与标定分辨率 {self.image_size} 不一致；"
                "请设置摄像头输出分辨率与标定一致，或使用当前分辨率重新标定"
            )
        return cv2.undistort(frame, self.camera_matrix, self.dist_coeffs)

    def set_plane_mapping(
        self,
        image_points: Sequence[Sequence[float]],
        world_points: Sequence[Sequence[float]],
    ) -> float:
        """拟合图像到平面的单应矩阵，并返回 RMS 误差。"""
        image = np.asarray(image_points, dtype=np.float32).reshape(-1, 1, 2)
        world = np.asarray(world_points, dtype=np.float32).reshape(-1, 1, 2)
        if len(image) < 4 or len(image) != len(world):
            raise ValueError("平面映射至少需要 4 组一一对应的点")
        undistorted = cv2.undistortPoints(image, self.camera_matrix, self.dist_coeffs, P=self.camera_matrix)
        homography, _ = cv2.findHomography(undistorted, world, cv2.RANSAC, 3.0)
        if homography is None:
            raise ValueError("无法计算平面单应性矩阵，请检查点位是否共线或对应错误")
        self.homography = homography
        predicted = cv2.perspectiveTransform(undistorted, homography)
        error = np.linalg.norm(predicted - world, axis=2)
        return float(np.sqrt(np.mean(error ** 2)))

    def pixel_to_world(
        self, pixel: Sequence[float], *, already_undistorted: bool = False
    ) -> Tuple[float, float]:
        """把一个像素坐标转换为已标定平面坐标。"""
        if self.homography is None:
            raise RuntimeError("当前标定文件没有 homography，无法输出场地坐标")
        point = np.asarray(pixel, dtype=np.float32).reshape(1, 1, 2)
        if not already_undistorted:
            point = cv2.undistortPoints(point, self.camera_matrix, self.dist_coeffs, P=self.camera_matrix)
        world = cv2.perspectiveTransform(point, self.homography)[0, 0]
        return float(world[0]), float(world[1])


def calibrate_from_images(
    image_paths: Iterable[str],
    board_size: Tuple[int, int],
    square_size: float,
    show: bool = False,
) -> CameraCalibration:
    """用棋盘格图片标定相机内参。"""
    board_width, board_height = board_size
    if board_width < 2 or board_height < 2 or square_size <= 0:
        raise ValueError("棋盘格内角点数量和方格尺寸必须为正数")
    object_template = np.zeros((board_width * board_height, 3), np.float32)
    object_template[:, :2] = np.mgrid[0:board_width, 0:board_height].T.reshape(-1, 2)
    object_template *= float(square_size)
    object_points: List[np.ndarray] = []
    image_points: List[np.ndarray] = []
    image_size: Optional[Tuple[int, int]] = None

    for image_path in image_paths:
        path = Path(image_path)
        raw = np.fromfile(str(path), dtype=np.uint8)
        frame = cv2.imdecode(raw, cv2.IMREAD_COLOR) if raw.size else None
        if frame is None:
            raise ValueError(f"无法读取标定图像: {image_path}")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        current_size = (gray.shape[1], gray.shape[0])
        if image_size is None:
            image_size = current_size
        elif current_size != image_size:
            raise ValueError("所有标定图像必须使用相同分辨率")
        found, corners = cv2.findChessboardCornersSB(gray, (board_width, board_height))
        if not found:
            continue
        object_points.append(object_template.copy())
        image_points.append(corners.astype(np.float32))
        if show:
            preview = cv2.drawChessboardCorners(frame, (board_width, board_height), corners, found)
            cv2.imshow("Calibration", preview)
            cv2.waitKey(100)

    if show:
        cv2.destroyWindow("Calibration")
    if image_size is None or len(object_points) < 3:
        raise ValueError(f"有效棋盘格图像不足，需要至少 3 张，当前为 {len(object_points)} 张")

    error, camera_matrix, dist_coeffs, _, _ = cv2.calibrateCamera(
        object_points, image_points, image_size, None, None
    )
    return CameraCalibration(
        image_size=image_size,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        reprojection_error_px=float(error),
        valid_image_count=len(object_points),
    )


def _load_plane_points(path: str) -> Tuple[List[List[float]], List[List[float]]]:
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    return data["image_points"], data["world_points"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="棋盘格相机标定与平面坐标映射")
    parser.add_argument("--images", required=True, help="标定图像 glob，例如 calibration/*.jpg")
    parser.add_argument("--board-width", type=int, required=True, help="棋盘格横向内角点数量")
    parser.add_argument("--board-height", type=int, required=True, help="棋盘格纵向内角点数量")
    parser.add_argument("--square-size", type=float, required=True, help="棋盘格方格边长，单位 mm")
    parser.add_argument("--plane-points", default=None, help="平面点 JSON 文件")
    parser.add_argument("--output", required=True, help="输出标定 JSON 文件")
    parser.add_argument("--show", action="store_true", help="显示棋盘格检测结果")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    image_paths = sorted(glob.glob(args.images))
    if not image_paths:
        raise FileNotFoundError(f"没有匹配到标定图像: {args.images}")
    calibration = calibrate_from_images(
        image_paths,
        (args.board_width, args.board_height),
        args.square_size,
        show=args.show,
    )
    if args.plane_points:
        image_points, world_points = _load_plane_points(args.plane_points)
        plane_error = calibration.set_plane_mapping(image_points, world_points)
        print(f"平面映射 RMS 误差: {plane_error:.3f} {calibration.world_unit}")
    calibration.save(args.output)
    valid_count = calibration.valid_image_count if calibration.valid_image_count is not None else len(image_paths)
    print(f"有效标定图像: {valid_count}")
    print(f"重投影 RMS 误差: {calibration.reprojection_error_px:.3f} px")
    print(f"标定文件已保存: {args.output}")


if __name__ == "__main__":
    main()
