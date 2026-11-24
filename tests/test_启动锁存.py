# -*- coding: utf-8 -*-
"""第 1 项：调试按下不计数，发令后只有第一次一键进入自检。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.启动锁存 import LatchState, RisingEdges, StartLatch
from protocol.串口协议 import Frame, FrameParser, MessageType, pack_buttons
from app.虚拟设备 import VirtualCamera, VirtualScanner
from mission.任务状态机 import MissionState
from tests.test_实机运行时 import SimulatedController, _build_runtime
from tools.参数调试 import save_config


class StartLatchTests(unittest.TestCase):
    def test_debug_press_does_not_arm_or_start(self):
        latch = StartLatch(competition=False, start_window_s=30)
        self.assertEqual(latch.state, LatchState.DEBUG)
        self.assertFalse(latch.debug_press())
        self.assertEqual(latch.state, LatchState.DEBUG)
        self.assertTrue(latch.mission_armed())

    def test_first_press_after_whistle_starts_once(self):
        latch = StartLatch(competition=True, start_window_s=30)
        self.assertEqual(latch.state, LatchState.STAGED)
        self.assertFalse(latch.mission_armed())
        self.assertFalse(latch.robot_start(0))
        self.assertTrue(latch.clock_start(1_000))
        self.assertEqual(latch.state, LatchState.CLOCK_ON)
        self.assertTrue(latch.robot_start(2_000))
        self.assertEqual(latch.state, LatchState.RUNNING)
        self.assertFalse(latch.robot_start(2_100))
        self.assertEqual(latch.state, LatchState.RUNNING)
        self.assertFalse(latch.allows_config_write())

    def test_next_round_can_arm_again_without_power_cycle(self):
        latch = StartLatch(competition=True, start_window_s=30)
        latch.clock_start(0)
        latch.robot_start(1_000)
        latch.end()
        self.assertEqual(latch.state, LatchState.ENDED)
        self.assertFalse(latch.clock_start(9_000))
        self.assertTrue(latch.next_round())
        self.assertEqual(latch.state, LatchState.STAGED)
        self.assertTrue(latch.clock_start(10_000))
        self.assertTrue(latch.robot_start(11_000))

    def test_competition_refuses_to_arm_without_window(self):
        with self.assertRaises(ValueError):
            StartLatch(competition=True, start_window_s=None)

    def test_missed_window_ends_the_round(self):
        latch = StartLatch(competition=True, start_window_s=30)
        latch.clock_start(0)
        self.assertIsNone(latch.tick(30_000))
        self.assertEqual(latch.tick(30_001), "start_missed")
        self.assertEqual(latch.state, LatchState.ENDED)
        self.assertFalse(latch.robot_start(30_100))

    def test_running_latch_rejects_parameter_save(self):
        latch = StartLatch(competition=True, start_window_s=30)
        latch.clock_start(0)
        latch.robot_start(1)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "color.json")
            with self.assertRaises(PermissionError):
                save_config({"colors": {}}, path, latch)
            self.assertFalse(Path(path).exists())

    def test_competition_runtime_does_not_self_test_on_power_up(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _build_runtime(
                Path(directory),
                camera=VirtualCamera([np.zeros((8, 8, 3), dtype=np.uint8)], loop=True),
                controller=SimulatedController(),
                scanner=VirtualScanner(),
                live_overrides={
                    "mode": "competition",
                    "home_station": "home_a",
                    "start_window_s": 30,
                    "sensor_source": "status_frame",
                    "speed_source": "status_frame",
                    "task_board": {"port": "memory", "display_required": True},
                    "loop": {"sleep_s": 0.0},
                },
            )
            try:
                state = runtime.run(max_runtime_s=0.05)
                self.assertEqual(state, MissionState.S0_INIT)
                self.assertEqual(runtime.latch.state, LatchState.STAGED)
                parser = FrameParser()
                types = [
                    frame.message_type
                    for packet in runtime.controller.written_packets
                    for frame in parser.feed(packet)
                ]
                self.assertNotIn(MessageType.PING, types)
            finally:
                runtime.close()

    def test_debounce_uses_only_a_measured_gap(self):
        raw = RisingEdges(None)
        self.assertTrue(raw.update("robot", True, 0))
        self.assertFalse(raw.update("robot", True, 5))
        held = RisingEdges(20)
        self.assertFalse(held.update("robot", True, 0))
        self.assertFalse(held.update("robot", True, 19))
        self.assertTrue(held.update("robot", True, 20))

    def test_gpio_edges_start_once_and_buttons_are_not_status(self):
        levels = {4: False, 5: False}

        def reader(pin: int) -> bool:
            return levels[pin]

        with tempfile.TemporaryDirectory() as directory:
            runtime = _build_runtime(
                Path(directory),
                camera=VirtualCamera([np.zeros((8, 8, 3), dtype=np.uint8)], loop=True),
                controller=SimulatedController(),
                scanner=VirtualScanner(),
                live_overrides={
                    "mode": "competition",
                    "home_station": "home_a",
                    "start_window_s": 30,
                    "sensor_source": "status_frame",
                    "speed_source": "status_frame",
                    "task_board": {"port": "memory", "display_required": True},
                    "start_inputs": {
                        "clock": {"source": "gpio", "pin": 4},
                        "robot": {"source": "button_frame"},
                        "debounce_ms": None,
                    },
                    "match_clock": {"still_speed_mm_s": 5, "start_window_s": 30},
                    "loop": {"sleep_s": 0.0},
                },
            )
            runtime.gpio_reader = reader
            try:
                with self.assertRaises(ValueError):
                    runtime.inject_start_edge("keyboard")
                levels[4] = True
                runtime._sample_start_inputs(1_000)
                runtime._poll_start_edges()
                self.assertEqual(runtime.latch.state, LatchState.CLOCK_ON)
                runtime.slot_mask = 1
                packet = Frame(MessageType.BUTTON, pack_buttons(0, 1), sequence=0).encode()
                runtime.protocol.receive(packet, 1)
                runtime._apply_reports()
                self.assertEqual(runtime.slot_mask, 1)
                runtime._sample_start_inputs(1_100)
                runtime._poll_start_edges()
                self.assertEqual(runtime.latch.state, LatchState.RUNNING)
                runtime._button_level["robot"] = False
                runtime._edges.update("robot", False, 1_200)
                runtime._button_level["robot"] = True
                runtime._sample_start_inputs(1_300)
                runtime._poll_start_edges()
                self.assertEqual(runtime.latch.state, LatchState.RUNNING)
            finally:
                runtime.close()

    def test_begin_next_round_clears_score_and_waits_for_a_new_whistle(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _build_runtime(
                Path(directory),
                camera=VirtualCamera([np.zeros((8, 8, 3), dtype=np.uint8)], loop=True),
                controller=SimulatedController(),
                scanner=VirtualScanner(),
                live_overrides={
                    "mode": "competition",
                    "home_station": "home_b",
                    "start_window_s": 30,
                    "sensor_source": "gpio",
                    "speed_source": "status_frame",
                    "task_board": {"port": "memory", "display_required": True},
                    "match_clock": {"still_speed_mm_s": 5, "start_window_s": 30},
                    "loop": {"sleep_s": 0.0},
                },
            )
            try:
                self.assertFalse(runtime.begin_next_round())
                self.assertTrue(runtime.latch.clock_start(0))
                self.assertTrue(runtime.latch.robot_start(10))
                runtime.task_board.show_task("156+123+516+231")
                runtime.task_board.grab_count = 12
                runtime.task_board.place_count = 9
                runtime.round_end_reason = "time_up"
                runtime.machine.round_end_reason = "time_up"
                runtime.latch.end()
                self.assertTrue(runtime.begin_next_round())
                self.assertEqual(runtime.latch.state, LatchState.STAGED)
                self.assertEqual(runtime.task_board.grab_count, 0)
                self.assertEqual(runtime.task_board.place_count, 0)
                self.assertEqual(runtime.task_board.task_code, "")
                self.assertIsNone(runtime.round_end_reason)
                self.assertIsNone(runtime.machine.round_end_reason)
                self.assertIsNone(runtime.match_clock.clock_start_ms)
                self.assertFalse(runtime.latch.robot_start(20))
                self.assertEqual(runtime.machine.home_zone, 6)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
