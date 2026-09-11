"""协议仿真：用固定行为模拟 STM32，便于测试重试和故障。"""

from __future__ import annotations

from enum import Enum
from typing import List

from protocol.串口协议 import ACTION_TYPES, Frame, FrameParser, MessageType, ProtocolEndpoint
from protocol.传输通道 import MemoryTransport


class ControllerBehavior(str, Enum):
    NORMAL = "normal"
    FAIL = "fail"
    DROP_ACK = "drop_ack"
    DROP_DONE = "drop_done"
    CORRUPT_RESPONSE = "corrupt_response"


class VirtualStm32:
    """根据设定行为生成控制器响应。"""

    def __init__(self, behavior: ControllerBehavior = ControllerBehavior.NORMAL) -> None:
        self.behavior = behavior
        self.parser = FrameParser()

    def process(self, packet: bytes) -> bytes:
        """解析树莓派数据并返回 ACK、DONE 或 FAIL。"""
        frames = self.parser.feed(packet)
        response = bytearray()
        for frame in frames:
            if frame.message_type == MessageType.HEARTBEAT:
                response.extend(Frame(MessageType.HEARTBEAT, sequence=frame.sequence).encode())
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
        return bytes(response)


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

    def send_action(self, action_name: str, payload: bytes, now_ms: float) -> bool:
        """发送支持的动作；非串口动作返回 False。"""
        message_type = self.ACTION_TYPES.get(action_name)
        if message_type is None:
            return False
        packet = self.endpoint.send_command(message_type, payload, now_ms)
        written = self.transport.write(packet)
        if written != len(packet):
            # 真实 pyserial 可能出现短写；记录但不抛异常以免中断控制循环
            return False
        return True

    def exchange(self, controller: VirtualStm32, now_ms: float) -> None:
        """执行一次确定性的树莓派与控制器数据交换。"""
        packet = self.controller_transport.read_available()
        if packet:
            self.controller_transport.write(controller.process(packet))
        response = self.transport.read_available()
        if response:
            self.endpoint.receive(response, now_ms)
