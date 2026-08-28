"""虚拟设备：无硬件时模拟相机、扫码器和传感器。调试真实设备前先跑测试。"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Iterable, Optional, Tuple

import numpy as np

from mission.扫码读取 import ScanResult


class VirtualCamera:
    """按运行循环约定回放图像帧。"""

    def __init__(self, frames: Iterable[np.ndarray], loop: bool = False) -> None:
        self._frames = [np.asarray(frame) for frame in frames]
        if not self._frames:
            raise ValueError("虚拟摄像头至少需要一帧图像")
        self._loop = loop
        self._index = 0
        self._opened = True

    def is_opened(self) -> bool:
        return self._opened

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        """读取一帧副本；返回 False 表示相机无可用画面。"""
        if not self._opened or self._index >= len(self._frames):
            if not self._loop:
                return False, None
            self._index = 0
        frame = self._frames[self._index].copy()
        self._index += 1
        return True, frame

    def close(self) -> None:
        self._opened = False

    def metrics(self) -> dict:
        return {
            "source": "virtual",
            "opened": self._opened,
            "read_failures": 0,
            "reconnects": 0,
            "last_error": None,
            "index": self._index,
            "frame_count": len(self._frames),
        }


class VirtualScanner:
    """按队列模拟扫码器，保持 ScanResult 接口。"""

    def __init__(self, scans: Iterable[ScanResult] = ()) -> None:
        self._scans: Deque[ScanResult] = deque(scans)

    def push(self, scan: ScanResult) -> None:
        """压入一条待读取的扫码结果。"""
        self._scans.append(scan)

    def read(self) -> Optional[ScanResult]:
        """读取一条扫码结果；无数据时返回 None。"""
        return self._scans.popleft() if self._scans else None

    def close(self) -> None:
        self._scans.clear()


@dataclass
class VirtualSensors:
    """供安全模块使用的模拟传感器值。"""

    tof_distance_mm: Optional[float] = None
    limit_triggered: bool = False
    # None=未接入（跳过装配检查），与实机 LiveSensors 语义对齐。
    grasp_confirmed: Optional[bool] = None

    def safety_snapshot(self) -> dict:
        """返回便于写日志的传感器快照。"""
        return {
            "tof_distance_mm": self.tof_distance_mm,
            "limit_triggered": self.limit_triggered,
            "grasp_confirmed": self.grasp_confirmed,
        }
