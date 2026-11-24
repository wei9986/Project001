"""协议仿真：用固定行为模拟 STM32，便于测试重试和故障。"""

from __future__ import annotations

from enum import Enum
from typing import List

from protocol.串口协议 import (
    ACTION_TYPES,
    GRIPPER_OPEN,
    REPORT_SEQUENCE,
    Frame,
    FrameParser,
    MessageType,
    ProtocolEndpoint,
    pack_speed,
    pack_status,
)
from protocol.传输通道 import MemoryTransport


class ControllerBehavior(str, Enum):
    NORMAL = "normal"
    FAIL = "fail"
    DROP_ACK = "drop_ack"
    DROP_DONE = "drop_done"
    CORRUPT_RESPONSE = "corrupt_response"


class VirtualStm32:
    """根据设定行为生成控制器响应。"""

    def __init__(
        self,
        behavior: ControllerBehavior = ControllerBehavior.NORMAL,
        report_interval_ms: float = 100.0,
        slot_mask: int = 0,
        gripper: int = GRIPPER_OPEN,
        limit_switch: int = 0,
        tof_mm: int = 200,
        speed_mm_s: int = 0,
    ) -> None:
        self.behavior = behavior
        self.parser = FrameParser()
        self.report_interval_ms = report_interval_ms
        self.slot_mask = slot_mask
        self.gripper = gripper
        self.limit_switch = limit_switch
        self.tof_mm = tof_mm
        self.speed_mm_s = speed_mm_s
        self.last_report_ms: float | None = None

    def process(self, packet: bytes, now_ms: float | None = None) -> bytes:
        """解析树莓派数据并返回 ACK、DONE 或 FAIL。到节拍时附带 STATUS 和 SPEED。"""
        frames = self.parser.feed(packet)
        response = bytearray()
        for frame in frames:
            if frame.message_type == MessageType.HEARTBEAT:
                response.extend(Frame(MessageType.HEARTBEAT, sequence=frame.sequence).encode())
                continue
            if frame.message_type in (MessageType.STATUS, MessageType.SPEED):
                continue
            if self.behavior == ControllerBehavior.DROP_ACK:
                response.extend(Frame(MessageType.DONE, sequence=frame.sequence).encode())
            elif self.behavior == ControllerBehavior.DROP_DONE:
                response.extend(Frame(MessageType.ACK, sequence=frame.sequence).encode())
            elif self.behavior == ControllerBehavior.FAIL:
                response.extend(Frame(MessageType.ACK, sequence=frame.sequence).encode())
                response.extend(Frame(MessageType.FAIL, b"\x21", sequence=frame.sequence).encode())
            else:
                response.extend(Frame(MessageType.ACK, sequence=frame.sequence).encode())
                response.extend(Frame(MessageType.DONE, sequence=frame.sequence).encode())
        if self.behavior == ControllerBehavior.CORRUPT_RESPONSE and response:
            response[-3] ^= 0xFF
        if now_ms is not None:
            response.extend(self.reports_due(now_ms))
        return bytes(response)

    def reports_due(self, now_ms: float) -> bytes:
        """按节拍发出主动上报。序号使用 REPORT_SEQUENCE，不占用动作序号。"""
        if self.last_report_ms is not None and now_ms - self.last_report_ms < self.report_interval_ms:
            return b""
        self.last_report_ms = now_ms
        status = Frame(
            MessageType.STATUS,
            pack_status(self.slot_mask, self.gripper, self.limit_switch, self.tof_mm),
            sequence=REPORT_SEQUENCE,
        ).encode()
        speed = Frame(MessageType.SPEED, pack_speed(self.speed_mm_s), sequence=REPORT_SEQUENCE).encode()
        return status + speed


class ProtocolActionBridge:
    """把运行层动作转换为协议帧并通过注入通道发送。

    动作到命令的映射复用 protocol/串口协议.py 的 ACTION_TYPES（唯一权威来源），
    与实机运行（app/实机运行.py）保持一致，保证仿真通过的行为与实机下发一致。
    """

    ACTION_TYPES = ACTION_TYPES

    def __init__(self, endpoint: ProtocolEndpoint, transport: MemoryTransport, controller_transport: MemoryTransport) -> None:
        self.endpoint = endpoint
        self.transport = transport
        self.controller_transport = controller_transport

    def send_action(
        self,
        action_name: str,
        now_ms: float,
        slot: int = 0,
        color: int = 0,
        zone: int = 0,
        ring: int = 0,
        batch: int = 0,
        reason: int = 0,
    ) -> bool:
        """发送支持的动作；非串口动作返回 False。载荷由 send_action 打成 5 字节。"""
        packet = self.endpoint.send_action(
            action_name, now_ms, slot=slot, color=color, zone=zone, ring=ring, batch=batch, reason=reason
        )
        if packet is None:
            return False
        written = self.transport.write(packet)
        if written != len(packet):
            # 真实 pyserial 可能出现短写；记录但不抛异常以免中断控制循环
            return False
        return True

    def exchange(self, controller: VirtualStm32, now_ms: float) -> None:
        """执行一次确定性的树莓派与控制器数据交换。"""
        packet = self.controller_transport.read_available()
        if packet:
            self.controller_transport.write(controller.process(packet, now_ms))
        response = self.transport.read_available()
        if response:
            self.endpoint.receive(response, now_ms)
