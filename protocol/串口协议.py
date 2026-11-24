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
    # 初赛动作命令（等 ACK 和 DONE）。坐标和任务码字符串不进这 5 字节。
    GOTO = 0x30
    PICK_TO_SLOT = 0x31
    PLACE_FROM_SLOT = 0x32
    STACK_FROM_SLOT = 0x33
    WAYPOINT = 0x34
    # 主动上报：不等 ACK，序号固定为 REPORT_SEQUENCE，不占用动作序号 1..250。
    STATUS = 0x40
    SPEED = 0x41
    POSITION = 0x42
    BUTTON = 0x43


# PackUnpack 固定 10 字节协议（参考 PackUnpack.c）：模块 ID、帧头、命令 ID、6 字节数据、校验和。
PROTOCOL_VERSION = 1
MAX_PAYLOAD = 6
MODULE_ID = 0x01

# 比赛动作命令（全项目唯一权威来源）。旧任务名和视觉级坐标命令不在这张表里同时有效。
# 每条命令的业务载荷只有 5 字节，由 pack_action / send_action 填入。
ACTION_TYPES = {
    "PING": MessageType.PING,
    "GOTO": MessageType.GOTO,
    "PICK_TO_SLOT": MessageType.PICK_TO_SLOT,
    "PLACE_FROM_SLOT": MessageType.PLACE_FROM_SLOT,
    "STACK_FROM_SLOT": MessageType.STACK_FROM_SLOT,
    "WAYPOINT": MessageType.WAYPOINT,
    "HOME": MessageType.HOME,
    "STOP": MessageType.STOP,
}

# 上报帧类型。receive 见到它们时不匹配 pending。
REPORT_TYPES = frozenset({MessageType.STATUS, MessageType.SPEED, MessageType.POSITION, MessageType.BUTTON})
# 与动作序号 1..250、心跳序号 251..255 都隔开。
REPORT_SEQUENCE = 0

# 区域号。GOTO / HOME 放在数据第 1 字节，其余补 0。
class ZoneId(IntEnum):
    NONE = 0
    QR_VIEW = 1
    SOURCE = 2
    COARSE = 3
    STAGING = 4
    HOME_A = 5
    HOME_B = 6


# 颜色编号与赛题一致：1 红、2 黄、3 蓝、4 绿、5 黑、6 浅蓝。0 表示本命令不带颜色。
COLOR_RED = 1
COLOR_YELLOW = 2
COLOR_BLUE = 3
COLOR_GREEN = 4
COLOR_BLACK = 5
COLOR_LIGHT_BLUE = 6

GRIPPER_OPEN = 0
GRIPPER_CLOSED = 1


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
        if len(self.payload) > MAX_PAYLOAD - 1:
            raise ValueError("payload 超过 5 字节（第 1 字节保留给序号）")
        # 先保留 bit7，再写入帧头。心跳序号 251..255 依赖这一位，不能先掩成 7 位。
        fields = [int(self.message_type), int(self.sequence), *self.payload.ljust(MAX_PAYLOAD - 1, b"\0")]
        head = 0x80
        raw = []
        for index, value in enumerate(fields):
            if not 0 <= value <= 255:
                raise ValueError("帧字段必须在 0..255 范围内")
            head |= ((value >> 7) & 1) << index
            raw.append((value & 0x7f) | 0x80)
        packet = bytes((MODULE_ID, head, *raw))
        return packet + bytes(((sum(packet) & 0x7f) | 0x80,))


class FrameParser:
    """增量解析分包、粘包和噪声中的有效帧。

    STATUS / SPEED 与动作帧使用同一 10 字节外形，靠命令号区分。
    它们不在这里匹配 pending，匹配规则在 ProtocolEndpoint.receive。
    """

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.invalid_frames = 0

    def feed(self, data: bytes) -> List[Frame]:
        """接收任意字节块，输出所有完整有效帧。"""
        self.buffer.extend(data)
        frames: List[Frame] = []
        while True:
            start = next((i for i, value in enumerate(self.buffer) if value < 0x80), -1)
            if start < 0:
                self.buffer = self.buffer[-1:]
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 10:
                break
            packet = bytes(self.buffer[:10])
            if packet[0] & 0x80 or any((b & 0x80) == 0 for b in packet[1:]):
                self.invalid_frames += 1
                del self.buffer[:1]
                continue
            if (sum(packet[:9]) & 0x7f) != (packet[9] & 0x7f):
                self.invalid_frames += 1
                del self.buffer[:1]
                continue
            try:
                message_type = MessageType(packet[2] & 0x7f)
            except ValueError:
                self.invalid_frames += 1
                self._resync()
                continue
            head = packet[1]
            values = []
            for i in range(7):
                values.append((packet[2 + i] & 0x7f) | (((head >> i) & 1) << 7))
            frames.append(Frame(message_type, bytes(values[2:]), sequence=values[1]))
            del self.buffer[:10]
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
    if len(payload) > MAX_PAYLOAD - 1:
        raise ValueError("JSON payload 超过 5 字节（第 1 字节保留给序号）")
    return payload


def decode_json_payload(payload: bytes) -> object:
    """在帧校验通过后解析 JSON 数据。调试工具可用；动作发送路径不再调用。"""
    return json.loads(payload.decode("utf-8"))


def _byte(name: str, value: int, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} 必须是整数，实际类型 {type(value).__name__}")
    if not low <= value <= high:
        raise ValueError(f"{name} 必须在 {low}..{high}，实际 {value}")
    return value


def pack_action(slot: int, color: int, zone: int, ring: int, batch: int) -> bytes:
    """把车载位、颜色、区域、环位、批次打成 5 字节。原料区抓取的环位填 0。"""
    return bytes((
        _byte("slot", slot, 0, 3),
        _byte("color", color, 0, 6),
        _byte("zone", zone, 0, 6),
        _byte("ring", ring, 0, 3),
        _byte("batch", batch, 0, 2),
    ))


def pack_prefixed(head: int) -> bytes:
    """GOTO、HOME、STOP、PING：第 1 字节是编号或原因码，其余补 0。"""
    return bytes((_byte("head", head, 0, 255), 0, 0, 0, 0))


def unpack_action(payload: bytes) -> tuple:
    """解回五个整数。不足 5 字节时按 0 补齐。"""
    raw = bytes(payload[:5]).ljust(5, b"\0")
    return tuple(raw)


def pack_status(slot_mask: int, gripper: int, limit_switch: int, tof_mm: int) -> bytes:
    """STATUS：载位掩码、夹爪、限位、ToF 高字节、ToF 低字节。"""
    tof = _byte("tof_mm", tof_mm, 0, 0xFFFF)
    return bytes((
        _byte("slot_mask", slot_mask, 0, 255),
        _byte("gripper", gripper, 0, 255),
        _byte("limit_switch", limit_switch, 0, 255),
        (tof >> 8) & 0xFF,
        tof & 0xFF,
    ))


def unpack_status(payload: bytes) -> dict:
    raw = bytes(payload[:5]).ljust(5, b"\0")
    return {
        "slot_mask": raw[0],
        "gripper": raw[1],
        "limit": raw[2],
        "tof_mm": (raw[3] << 8) | raw[4],
    }


def pack_position(x_mm: int, y_mm: int, yaw_deg: float) -> bytes:
    """POSITION：x、y 为有符号毫米，低字节在前。航向是一字节，0 度与 360 度相同。"""
    def signed(name: str, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{name} 必须是整数")
        if not -32768 <= value <= 32767:
            raise ValueError(f"{name} 超出毫米范围")
        return value & 0xFFFF

    yaw = float(yaw_deg) % 360.0
    yaw_byte = int(round(yaw * 256.0 / 360.0)) % 256
    x_raw = signed("x_mm", x_mm)
    y_raw = signed("y_mm", y_mm)
    return bytes((x_raw & 0xFF, (x_raw >> 8) & 0xFF, y_raw & 0xFF, (y_raw >> 8) & 0xFF, yaw_byte))


def unpack_position(payload: bytes) -> dict:
    raw = bytes(payload[:5]).ljust(5, b"\0")

    def signed(lo: int, hi: int) -> int:
        value = lo | (hi << 8)
        return value - 0x10000 if value >= 0x8000 else value

    return {
        "x_mm": signed(raw[0], raw[1]),
        "y_mm": signed(raw[2], raw[3]),
        "yaw_deg": raw[4] * 360.0 / 256.0,
    }


def pack_waypoint(x_mm: int, y_mm: int, point_index: int) -> bytes:
    """WAYPOINT：中间点的毫米坐标，以及从 0 起的序号。不包含区域号。"""
    position = pack_position(x_mm, y_mm, 0.0)
    if isinstance(point_index, bool) or not isinstance(point_index, int) or not 0 <= point_index <= 255:
        raise ValueError("point_index 超出序号范围")
    return bytes((position[0], position[1], position[2], position[3], point_index))


def unpack_waypoint(payload: bytes) -> dict:
    position = unpack_position(payload)
    raw = bytes(payload[:5]).ljust(5, b"\0")
    return {"x_mm": position["x_mm"], "y_mm": position["y_mm"], "point_index": raw[4]}


def pack_buttons(clock_level: int, robot_level: int) -> bytes:
    """按键上报：bit0 发令，bit1 车上按钮。不写入 STATUS 的五个字节。"""
    flags = (_byte("clock_level", clock_level, 0, 1) << 0) | (_byte("robot_level", robot_level, 0, 1) << 1)
    return bytes((flags, 0, 0, 0, 0))


def unpack_buttons(payload: bytes) -> dict:
    raw = bytes(payload[:1]).ljust(1, b"\0")
    return {"clock": bool(raw[0] & 0x01), "robot": bool(raw[0] & 0x02)}


def pack_speed(speed_mm_s: int) -> bytes:
    """SPEED：有符号速度，低字节在前，单位 mm/s，其余补 0。"""
    if isinstance(speed_mm_s, bool) or not isinstance(speed_mm_s, int):
        raise TypeError(f"speed_mm_s 必须是整数，实际类型 {type(speed_mm_s).__name__}")
    if not -32768 <= speed_mm_s <= 32767:
        raise ValueError(f"speed_mm_s 必须在 -32768..32767，实际 {speed_mm_s}")
    raw = speed_mm_s & 0xFFFF
    return bytes((raw & 0xFF, (raw >> 8) & 0xFF, 0, 0, 0))


def unpack_speed(payload: bytes) -> int:
    raw = bytes(payload[:2]).ljust(2, b"\0")
    value = raw[0] | (raw[1] << 8)
    if value >= 0x8000:
        value -= 0x10000
    return value


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
        self.last_status: Optional[dict] = None
        self.last_buttons: Optional[dict] = None
        self.last_position: Optional[dict] = None
        self.last_speed_mm_s: Optional[int] = None
        self.reports: List[Frame] = []

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
        x_mm: int = 0,
        y_mm: int = 0,
        point_index: int = 0,
    ) -> Optional[bytes]:
        """发送一条 5 字节动作。不接收字典或 JSON。未知命令返回 None。"""
        message_type = ACTION_TYPES.get(action_name)
        if message_type is None:
            return None
        if action_name == "PING":
            payload = pack_action(0, 0, 0, 0, 0)
        elif action_name == "WAYPOINT":
            payload = pack_waypoint(x_mm, y_mm, point_index)
        elif action_name in ("GOTO", "HOME"):
            payload = pack_prefixed(zone)
        elif action_name == "STOP":
            payload = pack_prefixed(reason)
        else:
            payload = pack_action(slot, color, zone, ring, batch)
        return self.send_command(message_type, payload, now_ms)

    def receive(self, data: bytes, now_ms: float) -> List[Frame]:
        """处理控制器响应。上报帧不匹配 pending，直接记入传感器和速度。"""
        frames = self.parser.feed(data)
        for frame in frames:
            self.last_received_ms = now_ms
            if frame.message_type in REPORT_TYPES:
                self._take_report(frame)
                continue
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

    def _take_report(self, frame: Frame) -> None:
        """主动上报不需要 ACK，也不改 pending。"""
        self.reports.append(frame)
        if frame.message_type == MessageType.STATUS:
            self.last_status = unpack_status(frame.payload)
        elif frame.message_type == MessageType.SPEED:
            self.last_speed_mm_s = unpack_speed(frame.payload)
        elif frame.message_type == MessageType.BUTTON:
            self.last_buttons = unpack_buttons(frame.payload)
        elif frame.message_type == MessageType.POSITION:
            self.last_position = unpack_position(frame.payload)

    def take_reports(self) -> List[Frame]:
        reports = self.reports
        self.reports = []
        return reports

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
