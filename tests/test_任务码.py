# -*- coding: utf-8 -*-
"""第 4 项：四组任务码解析，状态机吃进官方示例。"""

import unittest

from mission.扫码读取 import ScanResult, ScanType, TaskCodeError, parse_task_code, validate_scan
from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine

OFFICIAL = "156+123+516+231"


def scan(text: str) -> ScanResult:
    return validate_scan(ScanType.TASK_QR, text, "test")


class TaskCodeTests(unittest.TestCase):
    def test_official_example_assigns_coarse_and_staging_rings(self):
        task = parse_task_code(scan(OFFICIAL))
        rows = [
            (item.batch, item.order, item.color, item.ring_code, item.staging_ring)
            for item in task.first_batch + task.second_batch
        ]
        self.assertEqual(rows, [
            (1, 1, "red", "1", "1"),
            (1, 2, "black", "2", "2"),
            (1, 3, "light_blue", "3", "3"),
            (2, 1, "black", "2", "2"),
            (2, 2, "red", "3", "1"),
            (2, 3, "light_blue", "1", "3"),
        ])

    def test_rejected_codes_keep_separate_error_codes(self):
        cases = [
            ("111+123+111+321", "duplicate_color"),
            ("156+124+516+231", "bad_ring"),
            ("156+123+516", "bad_length"),
            ("15+123+516+231", "bad_length"),
            ("156+123+234+231", "color_set_mismatch"),
        ]
        for text, code in cases:
            with self.subTest(text=text):
                with self.assertRaises(TaskCodeError) as caught:
                    parse_task_code(scan(text))
                self.assertEqual(caught.exception.code, code)

    def test_state_machine_builds_plan_from_official_code(self):
        machine = MissionStateMachine()
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        actions = machine.handle(MissionEvent("task_scanned", {"scan": scan(OFFICIAL)}), 2)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual(machine.task_code, OFFICIAL)
        self.assertEqual(len(machine.plan.records), 6)
        self.assertEqual(actions, [])
        self.assertTrue(machine.flow.awaiting_turntable)
        actions = machine.handle(MissionEvent("turntable_ready", {"stable": True, "window_open": True}), 3)
        self.assertEqual(actions[0].name, "PICK_TO_SLOT")
        self.assertEqual(actions[0].data["color"], 1)
        self.assertEqual(actions[0].data["slot"], 1)
        self.assertEqual(actions[0].data["ring"], 0)
        self.assertEqual(actions[0].data["batch"], 1)

    def test_bad_code_does_not_start_loading(self):
        machine = MissionStateMachine(max_scan_retries=3)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        actions = machine.handle(MissionEvent("task_scanned", {"scan": scan("156+123+234+231")}), 2)
        self.assertEqual(actions, [])
        self.assertEqual(machine.state, MissionState.S1_READ_TASK)
        self.assertIsNone(machine.plan)


if __name__ == "__main__":
    unittest.main()
