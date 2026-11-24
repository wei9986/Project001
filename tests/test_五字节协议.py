# -*- coding: utf-8 -*-
"""C 项：动作命令 5 字节往返，主动上报不占用 pending。"""

import unittest

from protocol.串口协议 import (
    COLOR_BLACK,
    COLOR_LIGHT_BLUE,
    COLOR_RED,
    Frame,
    FrameParser,
    MessageType,
    ProtocolEndpoint,
    ZoneId,
    pack_action,
    pack_position,
    pack_speed,
    pack_status,
    pack_waypoint,
    unpack_action,
    unpack_position,
    unpack_waypoint,
    unpack_speed,
    unpack_status,
)
from protocol.协议仿真 import ControllerBehavior, ProtocolActionBridge, VirtualStm32
from protocol.传输通道 import create_memory_link


def official_commands():
    """官方示例 156+123+516+231 走完时的抓放和行驶编号。"""
    source = int(ZoneId.SOURCE)
    coarse = int(ZoneId.COARSE)
    staging = int(ZoneId.STAGING)
    home = int(ZoneId.HOME_A)
    red, black, light = COLOR_RED, COLOR_BLACK, COLOR_LIGHT_BLUE
    return [
        ("PING", {}),
        ("GOTO", {"zone": source}),
        ("PICK_TO_SLOT", {"slot": 1, "color": red, "zone": source, "ring": 0, "batch": 1}),
        ("PICK_TO_SLOT", {"slot": 2, "color": black, "zone": source, "ring": 0, "batch": 1}),
        ("PICK_TO_SLOT", {"slot": 3, "color": light, "zone": source, "ring": 0, "batch": 1}),
        ("GOTO", {"zone": coarse}),
        ("PLACE_FROM_SLOT", {"slot": 1, "color": red, "zone": coarse, "ring": 1, "batch": 1}),
        ("PLACE_FROM_SLOT", {"slot": 2, "color": black, "zone": coarse, "ring": 2, "batch": 1}),
        ("PLACE_FROM_SLOT", {"slot": 3, "color": light, "zone": coarse, "ring": 3, "batch": 1}),
        ("PICK_TO_SLOT", {"slot": 1, "color": red, "zone": coarse, "ring": 1, "batch": 1}),
        ("PICK_TO_SLOT", {"slot": 2, "color": black, "zone": coarse, "ring": 2, "batch": 1}),
        ("PICK_TO_SLOT", {"slot": 3, "color": light, "zone": coarse, "ring": 3, "batch": 1}),
        ("GOTO", {"zone": staging}),
        ("PLACE_FROM_SLOT", {"slot": 1, "color": red, "zone": staging, "ring": 1, "batch": 1}),
        ("PLACE_FROM_SLOT", {"slot": 2, "color": black, "zone": staging, "ring": 2, "batch": 1}),
        ("PLACE_FROM_SLOT", {"slot": 3, "color": light, "zone": staging, "ring": 3, "batch": 1}),
        ("GOTO", {"zone": source}),
        ("PICK_TO_SLOT", {"slot": 1, "color": black, "zone": source, "ring": 0, "batch": 2}),
        ("PICK_TO_SLOT", {"slot": 2, "color": red, "zone": source, "ring": 0, "batch": 2}),
        ("PICK_TO_SLOT", {"slot": 3, "color": light, "zone": source, "ring": 0, "batch": 2}),
        ("GOTO", {"zone": coarse}),
        ("PLACE_FROM_SLOT", {"slot": 1, "color": black, "zone": coarse, "ring": 2, "batch": 2}),
        ("PLACE_FROM_SLOT", {"slot": 2, "color": red, "zone": coarse, "ring": 3, "batch": 2}),
        ("PLACE_FROM_SLOT", {"slot": 3, "color": light, "zone": coarse, "ring": 1, "batch": 2}),
        ("PICK_TO_SLOT", {"slot": 1, "color": black, "zone": coarse, "ring": 2, "batch": 2}),
        ("PICK_TO_SLOT", {"slot": 2, "color": red, "zone": coarse, "ring": 3, "batch": 2}),
        ("PICK_TO_SLOT", {"slot": 3, "color": light, "zone": coarse, "ring": 1, "batch": 2}),
        ("GOTO", {"zone": staging}),
        ("STACK_FROM_SLOT", {"slot": 1, "color": black, "zone": staging, "ring": 2, "batch": 2}),
        ("STACK_FROM_SLOT", {"slot": 2, "color": red, "zone": staging, "ring": 1, "batch": 2}),
        ("STACK_FROM_SLOT", {"slot": 3, "color": light, "zone": staging, "ring": 3, "batch": 2}),
        ("HOME", {"zone": home}),
        ("STOP", {"reason": 1}),
    ]


class FiveByteProtocolTests(unittest.TestCase):
    def test_pack_action_roundtrip_and_rejects_dict_sized_json(self):
        payload = pack_action(1, COLOR_RED, int(ZoneId.SOURCE), 0, 1)
        self.assertEqual(len(payload), 5)
        self.assertEqual(unpack_action(payload), (1, COLOR_RED, int(ZoneId.SOURCE), 0, 1))
        with self.assertRaises(ValueError):
            pack_action(1, 7, 2, 0, 1)

    def test_status_and_speed_roundtrip(self):
        status = unpack_status(pack_status(0b101, 1, 0, 0x1234))
        self.assertEqual(status, {"slot_mask": 0b101, "gripper": 1, "limit": 0, "tof_mm": 0x1234})
        self.assertEqual(unpack_speed(pack_speed(-25)), -25)
        self.assertEqual(unpack_speed(pack_speed(300)), 300)

    def test_official_commands_are_ten_bytes_and_decode(self):
        endpoint = ProtocolEndpoint(heartbeat_interval_ms=10_000)
        for index, (name, fields) in enumerate(official_commands()):
            if endpoint.pending is not None:
                endpoint.pending.completed = True
            packet = endpoint.send_action(name, float(index), **fields)
            self.assertIsNotNone(packet, name)
            self.assertEqual(len(packet), 10, name)
            frame = FrameParser().feed(packet)[0]
            self.assertEqual(frame.message_type.name, name)
            if name in ("PING",):
                self.assertEqual(unpack_action(frame.payload), (0, 0, 0, 0, 0))
            elif name in ("GOTO", "HOME"):
                self.assertEqual(frame.payload[0], fields["zone"])
                self.assertEqual(frame.payload[1:], b"\0\0\0\0")
            elif name == "STOP":
                self.assertEqual(frame.payload[0], fields["reason"])
            else:
                expected = pack_action(
                    fields["slot"], fields["color"], fields["zone"], fields["ring"], fields["batch"]
                )
                self.assertEqual(unpack_action(frame.payload), tuple(expected))

    def test_report_does_not_clear_pending_even_with_same_sequence(self):
        endpoint = ProtocolEndpoint(heartbeat_interval_ms=10_000)
        endpoint.send_action("PICK_TO_SLOT", 0, slot=1, color=COLOR_RED, zone=int(ZoneId.SOURCE), ring=0, batch=1)
        sequence = endpoint.pending.sequence
        report = Frame(MessageType.STATUS, pack_status(0b001, 1, 0, 80), sequence=sequence).encode()
        report += Frame(MessageType.SPEED, pack_speed(12), sequence=sequence).encode()
        endpoint.receive(report, 10)
        self.assertFalse(endpoint.pending.acknowledged)
        self.assertFalse(endpoint.pending.completed)
        self.assertFalse(endpoint.pending.failed)
        self.assertEqual(endpoint.pending.sequence, sequence)
        self.assertEqual(endpoint.last_status["slot_mask"], 0b001)
        self.assertEqual(endpoint.last_speed_mm_s, 12)
        position = Frame(MessageType.POSITION, pack_position(-20, 400, 360), sequence=sequence).encode()
        endpoint.receive(position, 11)
        self.assertFalse(endpoint.pending.completed)
        self.assertEqual(endpoint.last_position["x_mm"], -20)
        self.assertEqual(endpoint.last_position["y_mm"], 400)
        self.assertEqual(endpoint.last_position["yaw_deg"], 0.0)
        self.assertEqual(unpack_position(pack_position(1, 2, 180))["yaw_deg"], 180.0)

    def test_waypoint_is_five_bytes_and_waits_for_done(self):
        point = unpack_waypoint(pack_waypoint(-20, 400, 1))
        self.assertEqual(point, {"x_mm": -20, "y_mm": 400, "point_index": 1})
        endpoint = ProtocolEndpoint(heartbeat_interval_ms=10_000)
        endpoint.send_action("WAYPOINT", 12, x_mm=-20, y_mm=400, point_index=1)
        self.assertFalse(endpoint.pending.completed)
        self.assertEqual(endpoint.pending.message_type, MessageType.WAYPOINT)

    def test_simulation_acks_five_bytes_and_emits_reports(self):
        pi_transport, stm32_transport = create_memory_link()
        endpoint = ProtocolEndpoint(heartbeat_interval_ms=10_000, ack_timeout_ms=100, max_retries=0)
        bridge = ProtocolActionBridge(endpoint, pi_transport, stm32_transport)
        controller = VirtualStm32(ControllerBehavior.NORMAL, speed_mm_s=40, slot_mask=0b010)
        self.assertTrue(
            bridge.send_action("PICK_TO_SLOT", 0.0, slot=2, color=COLOR_BLACK, zone=int(ZoneId.SOURCE), ring=0, batch=1)
        )
        bridge.exchange(controller, 1.0)
        self.assertTrue(endpoint.pending.completed)
        self.assertEqual(endpoint.last_status["slot_mask"], 0b010)
        self.assertEqual(endpoint.last_speed_mm_s, 40)
        self.assertEqual(endpoint.pending.sequence, 1)


if __name__ == "__main__":
    unittest.main()
