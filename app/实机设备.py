# -*- coding: utf-8 -*-
"""实机设备适配器：相机、扫码器和可自动重连的串口。"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import cv2
import numpy as np

from mission.扫码读取 import ExternalScannerParser, ScanResult, ScanType
from protocol.传输通道 import SerialTransport


class OpenCVCamera:
    """USB/Pi 相机适配器，读取失败后按间隔重新打开设备。"""

    def __init__(
        self, source: Any = 0, width: Optional[int] = None, height: Optional[int] = None,
        fps: Optional[float] = None, reconnect_delay_s: float = 2.0,
    ) -> None:
        self.source = source
        self.width = width
        self.height = height
        self.fps = fps
        self.reconnect_delay_s = reconnect_delay_s
        self._capture: Optional[cv2.VideoCapture] = None
        self._next_open_at = 0.0
        self.read_failures = 0
        self.reconnects = 0
        self.last_error: Optional[str] = None

    def open(self) -> bool:
        if self.is_opened():
            return True
        if time.monotonic() < self._next_open_at:
            return False
        capture = cv2.VideoCapture(self.source)
        if self.width:
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        if self.height:
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        if self.fps:
            capture.set(cv2.CAP_PROP_FPS, self.fps)
        if not capture.isOpened():
            capture.release()
            self.last_error = f"无法打开相机: {self.source}"
            self._next_open_at = time.monotonic() + self.reconnect_delay_s
            return False
        self._capture = capture
        self.reconnects += 1
        self.last_error = None
        return True

    def is_opened(self) -> bool:
        return self._capture is not None and self._capture.isOpened()

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self.open() or self._capture is None:
            return False, None
        ok, frame = self._capture.read()
        if ok and frame is not None:
            return True, frame
        self.read_failures += 1
        self.last_error = "相机读取失败"
        self.close()
        self._next_open_at = time.monotonic() + self.reconnect_delay_s
        return False, None

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
        self._capture = None

    def metrics(self) -> Dict[str, object]:
        return {
            "source": str(self.source), "opened": self.is_opened(), "read_failures": self.read_failures,
            "reconnects": self.reconnects, "last_error": self.last_error,
        }


class ReconnectingSerialTransport:
    """为控制器串口增加懒打开、断线关闭和下次循环重连能力。"""

    def __init__(self, port: str, baudrate: int = 115200, reconnect_delay_s: float = 2.0) -> None:
        self.transport = SerialTransport(port, baudrate=baudrate, timeout_s=0.0)
        self.reconnect_delay_s = reconnect_delay_s
        self._next_open_at = 0.0
        self.reconnects = 0
        self.last_error: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.transport.is_open

    def ensure_open(self) -> bool:
        if self.is_open:
            return True
        if time.monotonic() < self._next_open_at:
            return False
        try:
            self.transport.open()
        except Exception as exc:  # pyserial exceptions differ by operating system
            self.last_error = str(exc)
            self._next_open_at = time.monotonic() + self.reconnect_delay_s
            return False
        self.reconnects += 1
        self.last_error = None
        return True

    def read_available(self) -> bytes:
        if not self.ensure_open():
            return b""
        try:
            return self.transport.read_available()
        except Exception as exc:
            self._mark_disconnected(exc)
            return b""

    def write(self, data: bytes) -> int:
        if not self.ensure_open():
            raise RuntimeError(self.last_error or "控制器串口未连接")
        try:
            count = self.transport.write(data)
            if count != len(data):
                raise RuntimeError(f"串口仅写入 {count}/{len(data)} 字节")
            return count
        except Exception as exc:
            self._mark_disconnected(exc)
            raise RuntimeError(f"控制器串口写入失败: {exc}") from exc

    def _mark_disconnected(self, exc: Exception) -> None:
        self.last_error = str(exc)
        self.transport.close()
        self._next_open_at = time.monotonic() + self.reconnect_delay_s

    def close(self) -> None:
        self.transport.close()


class SerialScanner:
    """GM865/USB 扫码器串口适配器，输出完整的 ``ScanResult``。"""

    def __init__(
        self, port: str, baudrate: int = 9600, scan_type: ScanType = ScanType.TASK_QR,
        reconnect_delay_s: float = 2.0,
    ) -> None:
        self.transport = ReconnectingSerialTransport(port, baudrate, reconnect_delay_s)
        self.parser = ExternalScannerParser(scan_type)
        self._pending: list[ScanResult] = []

    def read(self) -> Optional[ScanResult]:
        if self._pending:
            return self._pending.pop(0)
        data = self.transport.read_available()
        if data:
            self._pending.extend(self.parser.feed(data))
        return self._pending.pop(0) if self._pending else None

    def close(self) -> None:
        self.transport.close()


@dataclass
class PiTelemetry:
    """采集 Pi 可用的基础运行状态；非 Pi 环境会优雅降级。"""

    def snapshot(self) -> Dict[str, object]:
        result: Dict[str, object] = {}
        thermal = Path("/sys/class/thermal/thermal_zone0/temp")
        if thermal.exists():
            try:
                result["cpu_temp_c"] = round(int(thermal.read_text().strip()) / 1000.0, 1)
            except (OSError, ValueError):
                pass
        try:
            output = subprocess.check_output(["vcgencmd", "get_throttled"], text=True, stderr=subprocess.DEVNULL).strip()
            result["vcgencmd_throttled"] = output
            result["undervoltage_detected"] = output != "throttled=0x0"
        except (FileNotFoundError, subprocess.CalledProcessError):
            result["undervoltage_detected"] = None
        return result
