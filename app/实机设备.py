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
from vision.配置加载 import normalize_source


class OpenCVCamera:
    """USB/Pi 相机适配器，读取失败后按间隔重新打开设备。"""

    def __init__(
        self, source: Any = 0, width: Optional[int] = None, height: Optional[int] = None,
        fps: Optional[float] = None, reconnect_delay_s: float = 2.0,
    ) -> None:
        self.source = self._normalize_source(source)
        self.width = width
        self.height = height
        self.fps = fps
        self.reconnect_delay_s = reconnect_delay_s
        self._capture: Optional[cv2.VideoCapture] = None
        self._next_open_at = 0.0
        self.read_failures = 0
        self.reconnects = 0
        self.last_error: Optional[str] = None

    @staticmethod
    def _normalize_source(source: Any) -> Any:
        """把 JSON 里常见的 '0' 转成整数索引，避免部分平台 VideoCapture('0') 失败。"""
        return normalize_source(source)

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

    def __init__(
        self, port: str, baudrate: int = 115200, reconnect_delay_s: float = 2.0,
        write_timeout_s: Optional[float] = 0.5,
    ) -> None:
        self.transport = SerialTransport(port, baudrate=baudrate, timeout_s=0.0, write_timeout_s=write_timeout_s)
        self.reconnect_delay_s = reconnect_delay_s
        self._next_open_at = 0.0
        self.reconnects = 0
        self.last_error: Optional[str] = None
        self._opened_once = False

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
        # 仅重连计数；首次打开不计为 reconnect（P3-21）。
        if self._opened_once:
            self.reconnects += 1
        self._opened_once = True
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
        reconnect_delay_s: float = 2.0, max_line_length: int = 512,
    ) -> None:
        self.transport = ReconnectingSerialTransport(port, baudrate, reconnect_delay_s)
        self.parser = ExternalScannerParser(scan_type, max_line_length=max_line_length)
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
class LiveSensors:
    """实机传感器读数集合，接口与仿真 VirtualSensors 保持一致。

    字段含义：
      tof_distance_mm  -- 前向 ToF 距离；None 表示传感器未接入，安全门跳过该项检查
      limit_triggered  -- 任一限位信号是否触发；未接入时恒为 False（无触发即安全）
      grasp_confirmed  -- 夹爪抓取确认信号（装配动作前必须满足）；
                          None 表示未接入（跳过装配检查），
                          False 表示已接入但未确认（拦截装配），True 表示已确认
    """

    tof_distance_mm: Optional[float] = None
    limit_triggered: bool = False
    grasp_confirmed: Optional[bool] = None

    @classmethod
    def from_config(cls, data: Optional[Dict[str, object]]) -> "LiveSensors":
        """从实机配置的 sensors 段读取，缺失字段保持默认安全值。

        grasp_confirmed / tof_distance_mm 为 null 表示传感器未接入，必须保持
        None 而非强制转 False——否则「未接入」会被当成「未确认」误拦截装配。
        """
        values = data or {}
        sensors = cls()
        if values.get("tof_distance_mm") is not None:
            sensors.update(tof_distance_mm=values["tof_distance_mm"])
        if "limit_triggered" in values:
            sensors.update(limit_triggered=values["limit_triggered"])
        if values.get("grasp_confirmed") is not None:
            sensors.update(grasp_confirmed=values["grasp_confirmed"])
        return sensors

    def update(self, **kwargs: Any) -> "LiveSensors":
        """按运行循环每帧刷新传感器读数；未提供的字段保持不变。

        只接受已知字段；非法类型立即报错，避免配置/采集脏值静默进入安全判定。
        """
        for key, value in kwargs.items():
            if key == "tof_distance_mm":
                if value is not None and not isinstance(value, (int, float)):
                    raise ValueError(f"tof_distance_mm 必须是数值或 null，实际 {type(value).__name__}")
                self.tof_distance_mm = float(value) if value is not None else None
            elif key == "limit_triggered":
                if not isinstance(value, bool):
                    raise ValueError(f"limit_triggered 必须是布尔值，实际 {type(value).__name__}")
                self.limit_triggered = value
            elif key == "grasp_confirmed":
                if value is not None and not isinstance(value, bool):
                    raise ValueError(f"grasp_confirmed 必须是布尔值或 null，实际 {type(value).__name__}")
                self.grasp_confirmed = value
            else:
                raise ValueError(f"未知传感器字段: {key}")
        return self

    def safety_snapshot(self) -> dict:
        """返回便于写日志的传感器快照。"""
        return {
            "tof_distance_mm": self.tof_distance_mm,
            "limit_triggered": self.limit_triggered,
            "grasp_confirmed": self.grasp_confirmed,
        }


@dataclass
class PiTelemetry:
    """采集 Pi 可用的基础运行状态；非 Pi 环境会优雅降级。

    vcgencmd 有节流：默认最多每 2s 采样一次，避免 30fps 下每帧 fork 子进程（P3-16）。
    """

    sample_interval_s: float = 2.0
    _last_sample_at: float = 0.0
    _cached: Optional[Dict[str, object]] = None

    def snapshot(self) -> Dict[str, object]:
        now = time.monotonic()
        if self._cached is not None and now - self._last_sample_at < self.sample_interval_s:
            return dict(self._cached)
        result: Dict[str, object] = {}
        thermal = Path("/sys/class/thermal/thermal_zone0/temp")
        if thermal.exists():
            try:
                result["cpu_temp_c"] = round(int(thermal.read_text().strip()) / 1000.0, 1)
            except (OSError, ValueError):
                pass
        try:
            output = subprocess.check_output(
                ["vcgencmd", "get_throttled"],
                text=True,
                stderr=subprocess.DEVNULL,
                timeout=0.5,
            ).strip()
            result["vcgencmd_throttled"] = output
            result["undervoltage_detected"] = output != "throttled=0x0"
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            result["undervoltage_detected"] = None
        self._cached = result
        self._last_sample_at = now
        return dict(result)
