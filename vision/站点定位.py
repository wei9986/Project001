"""站点定位：识别 AprilTag，并按配置映射为站点编号。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

from vision.配置加载 import load_json, resolve_path


DEFAULT_STATION_CONFIG: Dict[str, Any] = {
    "dictionary": "DICT_APRILTAG_36h11",
    "tag_size_mm": 40.0,
    "stations": {
        "1": "material_area",
        "2": "assembly_area",
        "3": "finished_area",
    },
}


@dataclass(frozen=True)
class StationDetection:
    """一个站点标签的图像坐标及可选三维位姿。"""

    tag_id: int
    station_id: Optional[str]
    center_px: Tuple[float, float]
    corners_px: Tuple[Tuple[float, float], ...]
    rvec: Optional[Tuple[float, float, float]] = None
    tvec: Optional[Tuple[float, float, float]] = None


def normalize_station_config(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """合并默认值并校验。用户提供 stations 时整表替换，不与默认键粘合。"""
    user = overrides or {}
    if not isinstance(user, dict):
        raise ValueError("站点配置必须是 JSON 对象")
    tag_size = float(user.get("tag_size_mm", DEFAULT_STATION_CONFIG["tag_size_mm"]))
    if tag_size <= 0:
        raise ValueError("tag_size_mm 必须为正数")
    if "stations" in user:
        stations = user["stations"]
        if not isinstance(stations, dict):
            raise ValueError("配置项 stations 必须是 JSON 对象")
        normalized_stations = {}
        for key, value in stations.items():
            station_key = str(key).strip()
            station_id = str(value).strip()
            if not station_key:
                raise ValueError("配置项 stations 包含空 tag id")
            if not station_id:
                raise ValueError(f"配置项 stations.{key} 的 station_id 不能为空")
            normalized_stations[station_key] = station_id
    else:
        normalized_stations = dict(DEFAULT_STATION_CONFIG["stations"])
    return {
        "dictionary": str(user.get("dictionary", DEFAULT_STATION_CONFIG["dictionary"])),
        "tag_size_mm": tag_size,
        "stations": normalized_stations,
    }


def load_station_config(path: Optional[Union[str, Any]] = None) -> Dict[str, Any]:
    """读取站点标签 JSON，并与安全默认值合并。"""
    if path is None:
        return normalize_station_config(None)
    resolved = resolve_path(path)
    if resolved is None:
        return normalize_station_config(None)
    return normalize_station_config(load_json(resolved))


def station_mapping_from_config(config: Dict[str, Any]) -> Dict[int, str]:
    """将 stations 表（字符串或整型键）转为 int -> station_id。"""
    mapping: Dict[int, str] = {}
    for key, value in config.get("stations", {}).items():
        try:
            tag_id = int(str(key).strip())
        except (TypeError, ValueError) as exc:
            raise ValueError(f"配置项 stations 的 tag id 必须是整数，实际为 {key!r}") from exc
        if tag_id < 0:
            raise ValueError(f"配置项 stations 的 tag id 不能为负数，实际为 {key!r}")
        station_id = str(value).strip()
        if not station_id:
            raise ValueError(f"配置项 stations.{key} 的 station_id 不能为空")
        mapping[tag_id] = station_id
    return mapping


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
        if not hasattr(cv2, "aruco"):
            raise RuntimeError(
                f"当前 OpenCV {cv2.__version__} 不包含 cv2.aruco，"
                "请安装 opencv-contrib-python 后再使用 AprilTag 站点定位"
            )
        dictionary_id = getattr(cv2.aruco, dictionary_name, None)
        if dictionary_id is None:
            raise ValueError(f"OpenCV 不支持字典：{dictionary_name}")
        self.station_mapping = station_mapping or {}
        self.tag_size_mm = float(tag_size_mm)
        self.camera_matrix = camera_matrix
        self.distortion = distortion
        self.dictionary_name = dictionary_name
        self.dictionary = cv2.aruco.getPredefinedDictionary(dictionary_id)
        if hasattr(cv2.aruco, "ArucoDetector"):
            # OpenCV >= 4.7: 新版 API
            self.parameters = cv2.aruco.DetectorParameters()
            self.detector = cv2.aruco.ArucoDetector(self.dictionary, self.parameters)
            self._use_legacy_api = False
        elif hasattr(cv2.aruco, "DetectorParameters_create"):
            # OpenCV 4.0~4.6: 旧版兼容路径
            self.parameters = cv2.aruco.DetectorParameters_create()  # pyright: ignore[reportAttributeAccessIssue]
            self._use_legacy_api = True
        else:
            # OpenCV 4.7+ 已移除旧 API，低于 4.0 则新 API 不存在；两者都缺说明版本不满足。
            raise RuntimeError(
                "当前 OpenCV 版本既不支持 ArucoDetector 也不支持 DetectorParameters_create，"
                "请安装 opencv-contrib-python >= 4.7"
            )

    @classmethod
    def from_config(
        cls,
        config: Optional[Dict[str, Any]] = None,
        *,
        path: Optional[Union[str, Any]] = None,
        camera_matrix: Optional[np.ndarray] = None,
        distortion: Optional[np.ndarray] = None,
    ) -> "AprilTagStationDetector":
        """从配置字典或 JSON 路径构造检测器。"""
        if config is not None and path is not None:
            raise ValueError("不能同时指定 config 与 path")
        loaded = normalize_station_config(config) if config is not None else load_station_config(path)
        return cls(
            station_mapping=station_mapping_from_config(loaded),
            tag_size_mm=float(loaded["tag_size_mm"]),
            camera_matrix=camera_matrix,
            distortion=distortion,
            dictionary_name=str(loaded["dictionary"]),
        )

    def detect(self, frame: np.ndarray) -> List[StationDetection]:
        """返回全部可见标签，包括未配置标签。"""
        if frame is None or frame.size == 0:
            raise ValueError("输入图像不能为空")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        if self._use_legacy_api:
            corners, ids, _ = cv2.aruco.detectMarkers(gray, self.dictionary, parameters=self.parameters)  # pyright: ignore[reportAttributeAccessIssue]
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
        r = rvec.reshape(-1)
        t = tvec.reshape(-1)
        return (
            (float(r[0]), float(r[1]), float(r[2])),
            (float(t[0]), float(t[1]), float(t[2])),
        )
