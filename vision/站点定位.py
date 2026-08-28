# -*- coding: utf-8 -*-
"""站点定位：AprilTag 检测与站点 ID 映射。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from vision.配置加载 import resolve_path


@dataclass(frozen=True)
class StationDetection:
    tag_id: int
    station_id: Optional[str]
    center_px: Tuple[int, int]
    corners_px: Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int], Tuple[int, int]]


class AprilTagStationDetector:
    """检测 AprilTag 并映射为站点 ID。"""

    def __init__(
        self,
        station_mapping: Dict[int, str],
        *,
        camera_matrix: Optional[np.ndarray] = None,
        distortion: Optional[np.ndarray] = None,
        marker_size_m: float = 0.05,
        dictionary_name: str = "DICT_APRILTAG_36h11",
    ) -> None:
        self.station_mapping = station_mapping
        self.camera_matrix = camera_matrix
        self.distortion = distortion
        self.marker_size_m = marker_size_m
        self.dictionary_name = dictionary_name
        dictionary = getattr(cv2.aruco, dictionary_name, None)
        if dictionary is None:
            dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, dictionary_name))
        self.detector = cv2.aruco.ArucoDetector(dictionary, cv2.aruco.DetectorParameters())

    @classmethod
    def from_config(
        cls,
        *,
        path: Optional[Path] = None,
        camera_matrix: Optional[np.ndarray] = None,
        distortion: Optional[np.ndarray] = None,
    ) -> "AprilTagStationDetector":
        resolved = resolve_path(path) if path else None
        station_mapping: Dict[int, str] = {}
        marker_size_m = 0.05
        dictionary_name = "DICT_APRILTAG_36h11"
        if resolved is not None:
            with Path(resolved).open("r", encoding="utf-8") as file:
                data = json.load(file)
            for tag_id_str, station_id in data.get("stations", {}).items():
                station_mapping[int(tag_id_str)] = str(station_id)
            marker_size_m = float(data.get("marker_size_m", 0.05))
            dictionary_name = data.get("dictionary", "DICT_APRILTAG_36h11")
        return cls(
            station_mapping,
            camera_matrix=camera_matrix,
            distortion=distortion,
            marker_size_m=marker_size_m,
            dictionary_name=dictionary_name,
        )

    def detect(self, frame: np.ndarray) -> List[StationDetection]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        corners, ids, _ = self.detector.detectMarkers(gray)
        if ids is None:
            return []
        results: List[StationDetection] = []
        for index, tag_id_arr in enumerate(ids):
            tag_id = int(tag_id_arr[0])
            corner = corners[index][0]
            cx = int(corner[:, 0].mean())
            cy = int(corner[:, 1].mean())
            station_id = self.station_mapping.get(tag_id)
            results.append(StationDetection(
                tag_id=tag_id,
                station_id=station_id,
                center_px=(cx, cy),
                corners_px=(
                    (int(corner[0][0]), int(corner[0][1])),
                    (int(corner[1][0]), int(corner[1][1])),
                    (int(corner[2][0]), int(corner[2][1])),
                    (int(corner[3][0]), int(corner[3][1])),
                ),
            ))
        return results
