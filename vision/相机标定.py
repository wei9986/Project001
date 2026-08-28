# -*- coding: utf-8 -*-
"""相机标定：内参读取、去畸变与像素到世界坐标的平面映射。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from vision.配置加载 import resolve_path


class CameraCalibration:
    """相机内参标定结果，支持去畸变和像素到世界坐标的平面映射。"""

    def __init__(
        self,
        camera_matrix: np.ndarray,
        dist_coeffs: np.ndarray,
        image_size: Tuple[int, int],
        *,
        plane_mapping: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.camera_matrix = camera_matrix
        self.dist_coeffs = dist_coeffs
        self.image_size = image_size
        self._plane_mapping = plane_mapping
        self._homography: Optional[np.ndarray] = None
        self._roi: Optional[Tuple[int, int, int, int]] = None
        self._setup_plane_mapping()

    def _setup_plane_mapping(self) -> None:
        if self._plane_mapping is None:
            return
        homography_data = self._plane_mapping.get("homography")
        if homography_data is not None:
            self._homography = np.array(homography_data, dtype=np.float64)
        roi_data = self._plane_mapping.get("roi")
        if roi_data is not None and len(roi_data) == 4:
            self._roi = tuple(int(v) for v in roi_data)

    @property
    def has_plane_mapping(self) -> bool:
        return self._homography is not None

    @classmethod
    def from_file(cls, path: str) -> "CameraCalibration":
        with Path(path).open("r", encoding="utf-8") as file:
            data = json.load(file)
        camera_matrix = np.array(data["camera_matrix"], dtype=np.float64)
        dist_coeffs = np.array(data["dist_coeffs"], dtype=np.float64)
        image_width = int(data["image_width"])
        image_height = int(data["image_height"])
        plane_mapping = data.get("plane_mapping")
        return cls(camera_matrix, dist_coeffs, (image_width, image_height), plane_mapping=plane_mapping)

    def undistort(self, frame: np.ndarray) -> np.ndarray:
        if self.camera_matrix is None or self.dist_coeffs is None:
            return frame
        h, w = frame.shape[:2]
        new_camera_matrix, _ = cv2.getOptimalNewCameraMatrix(
            self.camera_matrix, self.dist_coeffs, (w, h), 1, (w, h)
        )
        return cv2.undistort(frame, self.camera_matrix, self.dist_coeffs, None, new_camera_matrix)

    def pixel_to_world(self, pixel: Tuple[int, int], *, already_undistorted: bool = False) -> Optional[Tuple[float, float]]:
        if self._homography is None:
            return None
        pt = np.array([[pixel[0], pixel[1], 1.0]], dtype=np.float64).T
        world = (self._homography @ pt).flatten()
        if abs(world[2]) < 1e-10:
            return None
        return float(world[0] / world[2]), float(world[1] / world[2])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "camera_matrix": self.camera_matrix.tolist(),
            "dist_coeffs": self.dist_coeffs.tolist(),
            "image_size": list(self.image_size),
            "plane_mapping": self._plane_mapping,
        }
