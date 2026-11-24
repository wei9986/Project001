# -*- coding: utf-8 -*-
"""第 2 项：3 分钟从发令起算，静止 15 秒结束，等转盘为 23 秒。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.虚拟设备 import VirtualCamera, VirtualScanner
from mission.扫码读取 import ScanType, parse_task_code, validate_scan
from mission.初赛流程 import CompetitionFlow
from mission.任务计划 import TaskPlan
from mission.任务状态机 import MissionAction, MissionEvent, MissionState, MissionStateMachine
from protocol.串口协议 import ZoneId
from mission.比赛时钟 import MatchClock
from protocol.串口协议 import FrameParser
from tests.test_实机运行时 import SimulatedController, _build_runtime


class MatchClockTests(unittest.TestCase):
    def clock(self, window=30.0) -> MatchClock:
        return MatchClock(still_speed_mm_s=5, start_window_s=window)

    def test_still_speed_is_not_hardcoded_zero(self):
        with self.assertRaises(ValueError):
            MatchClock(0)

    def test_clear_round_waits_for_the_next_whistle(self):
        clock = self.clock()
        clock.start(1_000)
        clock.note_robot_start(1_200)
        clock.clear_round()
        self.assertEqual(clock.still_speed_mm_s, 5)
        self.assertIsNone(clock.clock_start_ms)
        self.assertEqual(clock.tick(5_000, 0, False), "running")
        with self.assertRaises(RuntimeError):
            clock.remaining_ms(5_000)

    def test_zero_speed_stalls_at_15_seconds(self):
        clock = self.clock()
        clock.start(0)
        clock.note_robot_start(0)
        self.assertEqual(clock.tick(0, 0, False), "running")
        self.assertEqual(clock.tick(14_999, 0, False), "running")
        self.assertEqual(clock.tick(15_000, 0, False), "stalled")

    def test_turntable_wait_uses_23_seconds(self):
        clock = self.clock()
        clock.start(0)
        clock.note_robot_start(0)
        self.assertEqual(clock.tick(0, 0, True), "running")
        self.assertEqual(clock.tick(15_000, 0, True), "running")
        self.assertEqual(clock.tick(23_000, 0, True), "stalled")

    def test_time_up_is_180_seconds_from_the_whistle(self):
        clock = self.clock()
        clock.start(0)
        clock.note_robot_start(20_000)
        self.assertEqual(clock.remaining_ms(20_000), 160_000)
        self.assertEqual(clock.tick(179_999, 100, False), "running")
        self.assertEqual(clock.tick(180_000, 100, False), "time_up")

    def test_missing_start_inside_window_ends_without_inventing_a_timeout(self):
        clock = self.clock(window=30)
        clock.start(0)
        self.assertEqual(clock.tick(30_000, 0, False), "running")
        self.assertEqual(clock.tick(30_001, 0, False), "start_missed")
        unset = MatchClock(still_speed_mm_s=5, start_window_s=None)
        unset.start(0)
        self.assertEqual(unset.tick(120_000, 0, False), "running")

    def test_motion_resets_the_still_timer(self):
        clock = self.clock()
        clock.start(0)
        clock.note_robot_start(0)
        self.assertEqual(clock.tick(0, 0, False), "running")
        self.assertEqual(clock.tick(10_000, 40, False), "running")
        self.assertEqual(clock.tick(20_000, 0, False), "running")
        self.assertEqual(clock.tick(34_999, 0, False), "running")
        self.assertEqual(clock.tick(35_000, 0, False), "stalled")

    def test_finished_match_is_not_a_stall(self):
        clock = self.clock()
        clock.start(0)
        clock.note_robot_start(0)
        self.assertEqual(clock.tick(20_000, 0, False, finished=True), "running")
        self.assertIsNone(clock.outcome)

    def test_state_machine_15_seconds_is_still_step_timeout(self):
        machine = MissionStateMachine(state_timeout_ms=5000)
        machine.handle(MissionEvent("start"), 0)
        actions = machine.tick(15_000)
        self.assertTrue(any(action.name == "recover" for action in actions))
        self.assertIsNone(machine.round_end_reason)

    def test_runtime_stall_sends_stop_and_does_not_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = _build_runtime(
                Path(directory),
                camera=VirtualCamera([np.zeros((8, 8, 3), dtype=np.uint8)]),
                controller=SimulatedController(),
                scanner=VirtualScanner(),
                live_overrides={"match_clock": {"still_speed_mm_s": 5, "start_window_s": 30}},
            )
            try:
                runtime.speed_mm_s = 0
                runtime.note_clock_start(0)
                runtime.note_robot_start(0)
                runtime._tick_mission(0)
                runtime._tick_mission(15_000)
                self.assertEqual(runtime.round_end_reason, "stalled")
                self.assertEqual(runtime.machine.round_end_reason, "stalled")
                self.assertEqual(runtime.machine.tick(20_000), [])
                parser = FrameParser()
                sent = [
                    frame.message_type.name
                    for packet in runtime.controller.written_packets
                    for frame in parser.feed(packet)
                ]
                self.assertIn("STOP", sent)
                self.assertNotIn("RECOVER", sent)
            finally:
                runtime.close()

    def test_missing_speed_does_not_accumulate_stall(self):
        clock = self.clock()
        clock.start(0)
        clock.note_robot_start(0)
        self.assertEqual(clock.tick(20_000, None, False), "running")
        self.assertIsNone(clock.outcome)

    def test_competition_wait_over_5_seconds_does_not_recover(self):
        machine = MissionStateMachine(state_timeout_ms=5000)
        started = machine.handle(MissionEvent("start"), 0)
        self.assertEqual(started[0].name, "self_test")
        competition = MissionStateMachine(state_timeout_ms=5000)
        reading = competition.begin_task_read(0)
        self.assertEqual([action.name for action in reading], ["read_task_qr"])
        self.assertEqual(competition.state, MissionState.S1_READ_TASK)
        self.assertEqual(competition.tick(6_000), [])
        competition.handle(
            MissionEvent("task_scanned", {"scan": validate_scan(ScanType.TASK_QR, "156+123+516+231", "test")}),
            1,
        )
        self.assertEqual(competition.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual(competition.tick(8_000), [])
        self.assertNotEqual(competition.state, MissionState.RECOVERY)
        self.assertIsNone(competition.round_end_reason)

    def _competition_runtime(self, directory: str):
        return _build_runtime(
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
                "match_clock": {"still_speed_mm_s": 5, "start_window_s": 30},
                "loop": {"sleep_s": 0.0},
            },
        )

    def _packet_names(self, runtime) -> list[str]:
        parser = FrameParser()
        return [
            frame.message_type.name
            for packet in runtime.controller.written_packets
            for frame in parser.feed(packet)
        ]

    def test_competition_running_reads_qr_and_failures_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._competition_runtime(directory)
            try:
                runtime.inject_start_edge("clock")
                runtime.inject_start_edge("robot")
                runtime._poll_start_edges()
                self.assertEqual(runtime.machine.state, MissionState.S1_READ_TASK)
                self.assertNotIn("PING", self._packet_names(runtime))
                runtime.machine.entered_ms = 0
                runtime._status_seen_ms = 6_000
                runtime._speed_seen_ms = 6_000
                runtime._tick_mission(6_000)
                self.assertNotIn("RECOVER", self._packet_names(runtime))
                self.assertIsNone(runtime.round_end_reason)
                runtime._dispatch("action_failed", {"error": "done_timeout"})
                self.assertEqual(runtime.round_end_reason, "action_failed")
                self.assertEqual(runtime.latch.state.value, "ended")
                names = self._packet_names(runtime)
                self.assertIn("STOP", names)
                self.assertNotIn("RECOVER", names)
                self.assertNotIn("PING", names)
            finally:
                runtime.close()

    def test_competition_clock_limits_each_send_one_stop(self):
        cases = (
            (False, 0, 15_000, "stalled"),
            (True, 0, 23_000, "stalled"),
            (False, 100, 180_000, "time_up"),
        )
        for waiting, speed, at_ms, reason in cases:
            with self.subTest(waiting=waiting, at_ms=at_ms), tempfile.TemporaryDirectory() as directory:
                runtime = self._competition_runtime(directory)
                try:
                    runtime.inject_start_edge("clock")
                    runtime.inject_start_edge("robot")
                    runtime._poll_start_edges()
                    runtime.speed_mm_s = speed
                    runtime.waiting_turntable = waiting
                    runtime.note_clock_start(0)
                    runtime.note_robot_start(0)
                    runtime._status_seen_ms = 0
                    runtime._speed_seen_ms = 0
                    runtime._tick_mission(0)
                    runtime._status_seen_ms = at_ms
                    runtime._speed_seen_ms = at_ms
                    runtime._tick_mission(at_ms)
                    self.assertEqual(runtime.round_end_reason, reason)
                    names = self._packet_names(runtime)
                    self.assertEqual(names.count("STOP"), 1)
                    self.assertNotIn("RECOVER", names)
                finally:
                    runtime.close()

    def test_empty_turntable_and_missing_calibration_do_not_send_motion(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = self._competition_runtime(directory)
            try:
                runtime.inject_start_edge("clock")
                runtime.inject_start_edge("robot")
                runtime._poll_start_edges()
                flow = CompetitionFlow(TaskPlan(parse_task_code("156+123+516+231")))
                flow.awaiting_turntable = True
                runtime.machine.flow = flow
                runtime.turntable_stable = True
                runtime._offer_turntable(1_000)
                self.assertTrue(flow.awaiting_turntable)
                self.assertNotIn("PICK_TO_SLOT", self._packet_names(runtime))
                source = MissionAction("PICK_TO_SLOT", {"zone": int(ZoneId.SOURCE), "color": 1, "slot": 1, "ring": 0, "batch": 1})
                place = MissionAction("PLACE_FROM_SLOT", {"zone": int(ZoneId.STAGING), "color": 1, "slot": 1, "ring": 1, "batch": 1})
                self.assertFalse(runtime._wants_fine_localization(source))
                self.assertTrue(runtime._wants_fine_localization(place))
                runtime.use_fine_localization = True
                runtime.odometry_xy = (1, 2)
                runtime._position_seen_ms = 0
                self.assertEqual(runtime._fresh_pose(5_000), (None, None))
                self.assertEqual(runtime.odometry_xy, (1, 2))
                runtime._execute([place], 1_000)
                self.assertEqual(runtime.round_end_reason, "calibration_missing")
                self.assertNotIn("PLACE_FROM_SLOT", self._packet_names(runtime))
                self.assertFalse(runtime._ring_ready(place, [0, 0]))
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
