# -*- coding: utf-8 -*-
"""第 5 项：读码成功后上部显示完整任务码，两个计数先是 0。"""

import unittest

from app.实机设备 import DisplayPort
from app.任务牌 import ROUND_RUNNING, TaskBoard
from mission.扫码读取 import ScanType, validate_scan
from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine

OFFICIAL = "156+123+516+231"


class TaskBoardTests(unittest.TestCase):
    def test_official_code_is_the_first_display_payload(self):
        board = TaskBoard()
        port = DisplayPort("memory")
        machine = MissionStateMachine(require_display=True)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        actions = machine.handle(
            MissionEvent("task_scanned", {"scan": validate_scan(ScanType.TASK_QR, OFFICIAL, "test")}),
            2,
        )
        self.assertEqual(machine.state, MissionState.S1_READ_TASK)
        self.assertEqual(actions[0].name, "show_task_code")
        self.assertEqual(actions[0].data["task_code"], OFFICIAL)
        self.assertEqual(actions[0].data["grab_count"], 0)
        self.assertEqual(actions[0].data["place_count"], 0)
        payload = board.show_task(actions[0].data["task_code"])
        self.assertTrue(port.send(payload))
        self.assertEqual(port.sent[0].task_code, OFFICIAL)
        self.assertEqual((port.sent[0].grab_count, port.sent[0].place_count), (0, 0))
        self.assertEqual(board.status, ROUND_RUNNING)

    def test_display_failure_stays_on_the_scan_step(self):
        port = DisplayPort(None)
        machine = MissionStateMachine(require_display=True)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(
            MissionEvent("task_scanned", {"scan": validate_scan(ScanType.TASK_QR, OFFICIAL, "test")}),
            2,
        )
        payload = TaskBoard().show_task(OFFICIAL)
        self.assertFalse(port.send(payload))
        self.assertEqual(machine.state, MissionState.S1_READ_TASK)
        self.assertIsNone(machine.plan)
        machine.handle(MissionEvent("task_displayed"), 3)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual(len(machine.plan.records), 6)

    def test_stack_event_does_not_increase_place_count(self):
        board = TaskBoard()
        board.show_task(OFFICIAL)
        board.apply_event("released_on_ring")
        board.apply_event("STACKED")
        self.assertEqual((board.grab_count, board.place_count), (0, 1))

    def test_memory_port_rewrites_when_a_count_changes(self):
        empty = DisplayPort(None)
        board = TaskBoard()
        self.assertFalse(empty.send(board.show_task(OFFICIAL)))
        port = DisplayPort("memory")
        self.assertTrue(port.send(board.payload()))
        self.assertEqual(port.sent[0].text(), f"{OFFICIAL} 0 0")
        board.apply_event("slot_loaded_from_source")
        self.assertTrue(port.send(board.payload()))
        self.assertEqual(port.sent[-1].text(), f"{OFFICIAL} 1 0")
        board.apply_event("released_on_ring")
        self.assertTrue(port.send(board.payload()))
        self.assertEqual(port.sent[-1].text(), f"{OFFICIAL} 1 1")
        sent = len(port.sent)
        board.apply_event("stacked")
        self.assertEqual((board.grab_count, board.place_count), (1, 1))
        self.assertEqual(len(port.sent), sent)

    def test_serial_line_uses_its_own_baud_and_rejects_a_short_write(self):
        class Line:
            def __init__(self) -> None:
                self.buf = b""

            def write(self, data: bytes) -> int:
                self.buf += data
                return len(data)

            def close(self) -> None:
                self.buf = b""

        line = Line()
        port = DisplayPort("COM9", baudrate=9600, transport=line)
        board = TaskBoard()
        self.assertTrue(port.send(board.show_task(OFFICIAL)))
        self.assertEqual(line.buf, f"{OFFICIAL} 0 0\n".encode())
        self.assertFalse(DisplayPort("COM9", baudrate=None).send(board.payload()))

        class Short:
            def write(self, data: bytes) -> int:
                return 1

        self.assertFalse(DisplayPort("COM9", baudrate=9600, transport=Short()).send(board.payload()))


if __name__ == "__main__":
    unittest.main()
