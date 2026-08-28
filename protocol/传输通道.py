"""传输通道：提供真实串口和内存串口，不处理协议帧。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


class SerialTransport:
    """pyserial 适配器；帧解析仍由串口协议模块负责。"""

    def __init__(self, port: str, baudrate: int = 115200, timeout_s: float = 0.0,
                 write_timeout_s: Optional[float] = 0.5) -> None:
        if baudrate <= 0 or timeout_s < 0:
            raise ValueError("波特率必须为正数，超时不能为负数")
        if write_timeout_s is not None and write_timeout_s < 0:
            raise ValueError("write_timeout_s 不能为负数")
        self.port = port
        self.baudrate = baudrate
        self.timeout_s = timeout_s
        self.write_timeout_s = write_timeout_s
        self._serial = None

    def open(self) -> None:
        if self._serial is not None:
            return
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError("SerialTransport 需要安装 pyserial") from exc
        self._serial = serial.Serial(
            self.port,
            self.baudrate,
            timeout=self.timeout_s,
            write_timeout=self.write_timeout_s,
        )

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    @property
    def is_open(self) -> bool:
        return bool(self._serial is not None and self._serial.is_open)

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise RuntimeError("串口未打开")
        return int(self._serial.write(data))

    def read_available(self, max_bytes: int = 4096) -> bytes:
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正数")
        if not self.is_open:
            raise RuntimeError("串口未打开")
        return bytes(self._serial.read(min(self._serial.in_waiting, max_bytes)))


@dataclass
class MemoryTransport:
    """用于测试的双向内存字节通道。"""

    _incoming: bytearray
    _peer_incoming: bytearray
    _opened: bool = True

    def open(self) -> None:
        self._opened = True

    def close(self) -> None:
        self._opened = False

    @property
    def is_open(self) -> bool:
        return self._opened

    def write(self, data: bytes) -> int:
        if not self.is_open:
            raise RuntimeError("内存串口端点未打开")
        self._peer_incoming.extend(data)
        return len(data)

    def read_available(self, max_bytes: int = 4096) -> bytes:
        if not self.is_open:
            raise RuntimeError("内存串口端点未打开")
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须为正数")
        count = min(len(self._incoming), max_bytes)
        data = bytes(self._incoming[:count])
        del self._incoming[:count]
        return data


def create_memory_link() -> Tuple[MemoryTransport, MemoryTransport]:
    first_incoming = bytearray()
    second_incoming = bytearray()
    return MemoryTransport(first_incoming, second_incoming), MemoryTransport(second_incoming, first_incoming)
