# -*- coding: utf-8 -*-
"""串口协议：树莓派与 F407 的二进制通信。ACK 仅表示收到，必须等待 DONE 才表示执行完成。"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import IntEnum
from typing import List, Optional


class MessageType(IntEnum):
    HEARTBEAT = 0x01
    ACK = 0x02
    DONE = 0x03
    FAIL = 0x04
    ERROR = 0x05
    APPROACH_TARGET = 0x10
    FINE_LOCALIZE = 0x11
    GRASP = 0x12
    PLACE = 0x13
    STOP = 0x14
    SEARCH_TARGET = 0x15
    RECOVER = 0x16
    VERIFY_GRASP = 0x17
    VERIFY_PLACE = 0x18
    PING = 0x20
    SET_POSE = 0x21
    MOVE_REL = 0x22
    DOCK_STATION = 0x23
    PICK = 0x24
    PLACE_RING = 0x25
    ASSEMBLE = 0x26
    HOME = 0x27
    QUERY_STATUS = 0x28


PROTOCOL_VERSION = 1
HEADER = b"\xAA\x55"
TAIL = b"\x0D\x0A"
MAX_PAYLOAD = 255

# 统一动作 -> 命令映射（全项目唯一权威来源）。
# 任务级动作：任务状态机（mission/任务状态机.py）输出，实机运行（app/实机运行.py）与
# 协议仿真（protocol/协议仿真.py）共用，保证仿真与实机行为一致。
# 视觉级动作：视觉任务状态机（mission/视觉任务状态机.py）输出，用于单目标搜索-抓放闭环。
ACTION_TYPES = {
    # 任务级
    "self_test": MessageType.PING,
    "load_first_material": MessageType.PICK,
    "load_second_material": MessageType.PICK,
    "place_ring": MessageType.PLACE_RING,
    "assemble_material": MessageType.ASSEMBLE,
    "return_home": MessageType.HOME,
    "recover": MessageType.RECOVER,
    "safe_stop": MessageType.STOP,
    # 视觉级
    "search_target": MessageType.SEARCH_TARGET,
    "approach_target": MessageType.APPROACH_TARGET,
    "request_fine_localization": MessageType.FINE_LOCALIZE,
    "grasp": MessageType.GRASP,
    "verify_grasp": MessageType.VERIFY_GRASP,
    "place_target": MessageType.PLACE,
    "verify_place": MessageType.VERIFY_PLACE,
}


def crc16(data: bytes) -> int:
    """计算 CRC-16/IBM 校验值，低字节先发送。"""
    value = 0xFFFF
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return value & 0xFFFF


@dataclass(frozen=True)
class Frame:
    """通信帧：帧头、版本、序号、命令、长度、数据、CRC、帧尾。"""

    message_type: MessageType
    payload: bytes = b""
    version: int = PROTOCOL_VERSION
    sequence: int = 0

    def encode(self) -> bytes:
        """将已校验的帧编码为发送字节。"""
        if not 0 <= self.version <= 255 or not 0 <= self.sequence <= 255:
            raise ValueError("version 和 sequence 必须在 0..255 范围内")
        if len(self.payload) > MAX_PAYLOAD:
            raise ValueError("payload 超过 255 字节")
        body = bytes((self.version, self.sequence, int(self.message_type), len(self.payload))) + self.payload
        return HEADER + body + crc16(body).to_bytes(2, "little") + TAIL


class FrameParser:
    """增量解析分包、粘包和噪声中的有效帧。"""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.invalid_frames = 0

    def feed(self, data: bytes) -> List[Frame]:
        """接收任意字节块，输出所有完整有效帧。"""
        self.buffer.extend(data)
        frames: List[Frame] = []
        while True:
            start = self.buffer.find(HEADER)
            if start < 0:
                self.buffer = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 10:
                break
            payload_length = self.buffer[5]
            total_length = 2 + 4 + payload_length + 2 + 2
            if len(self.buffer) < total_length:
                break
            packet = bytes(self.buffer[:total_length])
            body = packet[2:6 + payload_length]
            received_crc = int.from_bytes(packet[6 + payload_length:8 + payload_length], "little")
            if packet[-2:] != TAIL or crc16(body) != received_crc:
                self.invalid_frames += 1
                # 校验失败说明帧头后的长度字段可能不可信：丢弃第一个字节并从下一个
                # 帧头位置重新同步，避免固定跳过 total_length 时越过真实帧边界。
                self._resync()
                continue
            # 校验协议版本；不兼容版本视为无效帧
            if packet[2] != PROTOCOL_VERSION:
                self.invalid_frames += 1
                self._resync()
                continue
            try:
                message_type = MessageType(packet[4])
            except ValueError:
                self.invalid_frames += 1
                self._resync()
                continue
            frames.append(Frame(message_type, packet[6:6 + payload_length], packet[2], packet[3]))
            del self.buffer[:total_length]
        return frames

    def _resync(self) -> None:
        """当前帧头位置不可信时丢弃一个字节，回到循环顶部重新查找帧头。

        逐字节滑动而非直接跳到下一个 HEADER，避免坏帧 payload 内嵌的
        伪帧头导致误同步。
        """
        del self.buffer[:1]


def json_payload(value: object) -> bytes:
    """编码紧凑 JSON，并检查 255 字节上限。"""
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("JSON payload 超过 255 字节")
    return payload


def decode_json_payload(payload: bytes) -> object:
    """在帧校验通过后解析 JSON 数据。"""
    return json.loads(payload.decode("utf-8"))


@dataclass
class PendingCommand:
    sequence: int
    message_type: MessageType
    packet: bytes
    deadline_ms: float
    retries: int = 0
    acknowledged: bool = False
    completed: bool = False
    failed: bool = False
    error_code: Optional[int] = None


class ProtocolEndpoint:
    """协议状态机：管理序号、心跳、重试和待完成命令。"""

    def __init__(
        self,
        heartbeat_interval_ms: float = 100.0,
        offline_timeout_ms: float = 1000.0,
        ack_timeout_ms: float = 5000.0,
        done_timeout_ms: float = 15000.0,
        max_retries: int = 1,
    ) -> None:
        if ack_timeout_ms <= 0 or done_timeout_ms <= 0:
            raise ValueError("ack_timeout_ms 与 done_timeout_ms 必须为正数")
        self.heartbeat_interval_ms = heartbeat_interval_ms
        self.offline_timeout_ms = offline_timeout_ms
        self.ack_timeout_ms = ack_timeout_ms
        self.done_timeout_ms = done_timeout_ms
        self.max_retries = max_retries
        self.parser = FrameParser()
        self.next_sequence = 1
        self.next_heartbeat_sequence = 251
        self.last_received_ms: Optional[float] = None
        self.last_heartbeat_sent_ms = -heartbeat_interval_ms
        self.pending: Optional[PendingCommand] = None
        self.last_error: Optional[str] = None  # 上层轮询时读取最近错误

    def _allocate_sequence(self) -> int:
        """循环分配 1 到 250 的命令序号。"""
        sequence = self.next_sequence
        self.next_sequence = 1 if sequence >= 250 else sequence + 1
        return sequence

    def _allocate_heartbeat_sequence(self) -> int:
        """心跳使用 251..255 独立序号域，与命令序号空间隔离，避免误匹配待完成命令。"""
        sequence = self.next_heartbeat_sequence
        self.next_heartbeat_sequence = 251 if sequence >= 255 else sequence + 1
        return sequence

    def is_online(self, now_ms: Optional[float] = None) -> bool:
        """检查最近是否收到有效控制器数据。"""
        now = time.monotonic() * 1000 if now_ms is None else now_ms
        return self.last_received_ms is not None and now - self.last_received_ms <= self.offline_timeout_ms

    def fail_pending(self, reason: str, error_code: int = 0x01) -> bool:
        """将当前未完成命令标为失败，允许立即下发下一条（含 recover/safe_stop）。

        用于串口写失败、状态超时清障等场景；已完成/已失败的 pending 不改动。
        返回是否实际失效了一条 pending。
        """
        if self.pending is None or self.pending.completed or self.pending.failed:
            return False
        self.pending.failed = True
        self.pending.error_code = error_code
        self.last_error = reason
        return True

    def send_command(self, message_type: MessageType, payload: bytes, now_ms: float) -> bytes:
        """创建一条待完成命令；未 DONE/FAIL 前禁止发送下一条。"""
        if not isinstance(payload, bytes):
            raise TypeError(f"payload 必须是 bytes，实际类型 {type(payload).__name__}")
        if self.pending is not None and not self.pending.completed and not self.pending.failed:
            raise RuntimeError("已有未完成指令，必须等待 DONE 或 FAIL")
        sequence = self._allocate_sequence()
        packet = Frame(message_type, payload, sequence=sequence).encode()
        # 首阶段 deadline 仅覆盖 ACK 窗口；收到 ACK 后改用 done_timeout_ms。
        self.pending = PendingCommand(sequence, message_type, packet, now_ms + self.ack_timeout_ms)
        return packet

    def send_action(self, action_name: str, data: object, now_ms: float) -> Optional[bytes]:
        """把通用动作映射为串口命令，映射表见模块级 ACTION_TYPES。"""
        message_type = ACTION_TYPES.get(action_name)
        return self.send_command(message_type, json_payload(data), now_ms) if message_type else None

    def receive(self, data: bytes, now_ms: float) -> List[Frame]:
        """处理控制器响应，只更新序号匹配的待执行命令。"""
        frames = self.parser.feed(data)
        for frame in frames:
            self.last_received_ms = now_ms
            if self.pending is None or frame.sequence != self.pending.sequence:
                continue
            if frame.message_type == MessageType.ACK:
                self.pending.acknowledged = True
                # ACK 后切换到 DONE 等待窗口，不再用 ACK 超时驱动重发。
                self.pending.deadline_ms = now_ms + self.done_timeout_ms
            elif frame.message_type == MessageType.DONE:
                if self.pending.acknowledged:
                    self.pending.completed = True
                else:
                    # DONE 到达但未先收到 ACK，视为协议错误
                    self.last_error = "protocol_done_without_ack"
            elif frame.message_type in (MessageType.FAIL, MessageType.ERROR):
                self.pending.failed = True
                self.pending.error_code = frame.payload[0] if frame.payload else None
        return frames

    def poll(self, now_ms: float) -> List[bytes]:
        """生成到期心跳；未 ACK 超时可重发，已 ACK 超时只失败不重发。"""
        output: List[bytes] = []
        if now_ms - self.last_heartbeat_sent_ms >= self.heartbeat_interval_ms:
            output.append(Frame(MessageType.HEARTBEAT, sequence=self._allocate_heartbeat_sequence()).encode())
            self.last_heartbeat_sent_ms = now_ms
        pending = self.pending
        if pending is None or pending.completed or pending.failed or now_ms < pending.deadline_ms:
            return output
        if not pending.acknowledged:
            # 仅未收到 ACK 时重发；避免物理动作被重复执行。
            if pending.retries < self.max_retries:
                pending.retries += 1
                pending.deadline_ms = now_ms + self.ack_timeout_ms
                output.append(pending.packet)
            else:
                pending.failed = True
                pending.error_code = 0x01
                self.last_error = "communication_lost"
        else:
            # 已 ACK：超时视为动作执行超时，不重发同包。
            pending.failed = True
            pending.error_code = 0x02
            self.last_error = "action_timeout"
        return output
