"""第 7 项：比赛时无线只向外发日志。下行的速度和参数一律丢掉。"""

from __future__ import annotations

from typing import Any, List, Optional


class WirelessLog:
    def __init__(
        self,
        downlink: bool = False,
        address: Optional[str] = None,
        fail: bool = False,
        baudrate: Optional[int] = None,
        transport: Optional[object] = None,
    ) -> None:
        self.downlink = downlink
        self.address = "" if address is None else str(address)
        self.fail = fail
        self.baudrate = baudrate if isinstance(baudrate, int) and not isinstance(baudrate, bool) and baudrate > 0 else None
        self.sent: List[str] = []
        self.ignored: List[Any] = []
        self.failures: List[str] = []
        self._transport = transport

    def close(self) -> None:
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()
        self._transport = None

    def send(self, event: Any) -> bool:
        """地址为空不发。memory 只记在 sent。其它地址按串口写任务牌那一行。"""
        text = event.text() if hasattr(event, "text") else str(event)
        if not self.address:
            return False
        if self.fail:
            self.failures.append(text)
            return False
        if self.address == "memory":
            self.sent.append(text)
            return True
        data = (text if text.endswith("\n") else text + "\n").encode("utf-8")
        try:
            if self._transport is None:
                if self.baudrate is None:
                    self.failures.append(text)
                    return False
                from protocol.传输通道 import SerialTransport

                self._transport = SerialTransport(self.address, self.baudrate, write_timeout_s=0.5)
                self._transport.open()
            written = int(self._transport.write(data))
        except (OSError, RuntimeError, TimeoutError, TypeError):
            self.failures.append(text)
            return False
        if written != len(data):
            self.failures.append(text)
            return False
        self.sent.append(text)
        return True

    def receive(self, payload: Any) -> Optional[Any]:
        if not self.downlink:
            self.ignored.append(payload)
            return None
        return payload
