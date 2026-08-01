"""站点定位：识别 AprilTag，并按配置映射为站点编号。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np


@dataclass(frozen=True)
class StationDetection:
    """一个站点标签的图像坐标及可选三维位姿。"""

    tag_id: int
    station_id: Optional[str]
    center_px: Tuple[float, float]
    corners_px: Tuple[Tuple[float, float], ...]
    rvec: Optional[Tuple[float, float, float]] = None
    tvec: Optional[Tuple[float, float, float]] = None


class AprilTagStationDetector:
    """识别 AprilTag，并将已配置 ID 转成站点编号。"""

    def __init__(
        self,
        station_mapping: Optional[Dict[int, str]] = None,
        tag_size_mm: float = 40.0,
        camera_matrix: Optional[np.ndarray] = None,
        distortion: Optional[np.ndarray] = None,
        dictionary_name: str = "DICT_APRILTAG_36h11",
    ) -> None:
        if tag_size_mm <= 0:
            raise ValueError("tag_size_mm 必须为正数")
        dictionary_id = getattr(cv2.aruco, dictionary_name, None)
        if dictionary_id is None:
            raise ValueError(f"OpenCV 不支持字典：{dictionary_name}")
        self.station_mapping = station_mapping or {}
        self.tag_size_mm = float(tag_size_mm)
        self.camera_matrix = camera_matrix
        self.distortion = distortion
        self.dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        if hasattr(cv2.aruco, "ArucoDetector"):
            # OpenCV >= 4.7: 新版 API
            self.parameters = cv2.aruco.DetectorParameters()
            self.detector = cv2.aruco.ArucoDetector(self.dictionary, self.parameters)
            self._use_legacy_api = False
        else:
            # OpenCV < 4.7: 旧版兼容路径
            self.parameters = cv2.aruco.DetectorParameters_create()
            self._use_legacy_api = True

    def detect(self, frame: np.ndarray) -> List[StationDetection]:
        """返回全部可见标签，包括未配置标签。"""
        if frame is None or frame.size == 0:
            raise ValueError("输入图像不能为空")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        if self._use_legacy_api:
            corners, ids, _ = cv2.aruco.detectMarkers(gray, self.dictionary, parameters=self.parameters)
        else:
            corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return []
        detections: List[StationDetection] = []
        for marker_corners, marker_id in zip(corners, ids.flatten().tolist()):
            points = marker_corners.reshape(4, 2).astype(float)
            center = tuple(points.mean(axis=0).tolist())
            rvec, tvec = self._estimate_pose(points)
            detections.append(
                StationDetection(
                    tag_id=int(marker_id),
                    station_id=self.station_mapping.get(int(marker_id)),
                    center_px=(float(center[0]), float(center[1])),
                    corners_px=tuple((float(x), float(y)) for x, y in points),
                    rvec=rvec,
                    tvec=tvec,
                )
            )
        return detections

    def _estimate_pose(
        self, corners_px: np.ndarray
    ) -> Tuple[Optional[Tuple[float, float, float]], Optional[Tuple[float, float, float]]]:
        """仅在有相机内参时估算标签位姿。"""
        if self.camera_matrix is None:
            return None, None
        distortion = self.distortion if self.distortion is not None else np.zeros((5, 1), dtype=np.float64)
        half = self.tag_size_mm / 2.0
        object_points = np.array(
            [[-half, half, 0.0], [half, half, 0.0], [half, -half, 0.0], [-half, -half, 0.0]],
            dtype=np.float64,
        )
        success, rvec, tvec = cv2.solvePnP(object_points, corners_px, self.camera_matrix, distortion)
        if not success:
            return None, None
        return tuple(float(value) for value in rvec.flatten()), tuple(float(value) for value in tvec.flatten())
