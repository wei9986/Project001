# -*- coding: utf-8 -*-
"""第 13 项：按比赛流程核对官方码、预检、停车、屏幕、绕行和下一轮。"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from app.任务牌 import TaskBoard
from app.启动锁存 import LatchState
from app.虚拟设备 import VirtualCamera, VirtualScanner
from app.视觉应用 import main
from mission.初赛流程 import CompetitionFlow
from mission.比赛时钟 import MATCH_DURATION_MS, STALL_MS, TURNTABLE_STALL_MS
from mission.扫码读取 import QrSearch, parse_task_code
from mission.任务计划 import MaterialStatus, TaskPlan
from mission.任务状态机 import MissionAction, MissionState
from mission.车道 import LaneMap
from protocol.串口协议 import GRIPPER_CLOSED, GRIPPER_OPEN, FrameParser, ZoneId
from tests.test_初赛流程_11_15 import drive, occupied, plan
from tests.test_实机运行时 import SimulatedController, _build_runtime
from vision.障碍物 import ObstacleSight
from vision.配置加载 import project_root

OFFICIAL = "156+123+516+231"


def _events_until_second_batch(flow: CompetitionFlow):
    steps = flow.start()
    events = []
    guard = 0
    while guard < 80:
        guard += 1
        if flow.awaiting_turntable:
            steps = flow.offer_turntable(True, True)
            continue
        if flow.awaiting_color:
            steps = flow.offer_color(flow.expected_color())
            continue
        if not steps:
            break
        step = steps[0]
        if step.name == "GOTO" and int(step.data["zone"]) == int(ZoneId.SOURCE):
            break
        if step.name in ("GOTO", "HOME"):
            steps = flow.travel_done(0)
        elif step.name == "PICK_TO_SLOT":
            steps = flow.confirm(occupied(step.data["slot"]), GRIPPER_OPEN)
        else:
            steps = flow.confirm(0, GRIPPER_OPEN)
        events.extend(flow.take_events())
    return events


class AcceptanceTests(unittest.TestCase):
    def test_clock_limits_stay_at_the_rule_values(self):
        self.assertEqual(MATCH_DURATION_MS, 180_000)
        self.assertEqual(STALL_MS, 15_000)
        self.assertEqual(TURNTABLE_STALL_MS, 23_000)

    def test_official_code_counts_homes_and_keeps_counts_when_stack_is_blocked(self):
        board = TaskBoard()
        board.show_task(OFFICIAL)
        flow = CompetitionFlow(plan(), still_speed_mm_s=30)
        commands = []
        events = []
        drive(flow, flow.start(), commands, events)
        for name in events:
            board.apply_event(name)
        self.assertEqual(board.grab_count, 12)
        self.assertEqual(board.place_count, 9)
        self.assertEqual(flow.stack_ok, 3)
        self.assertEqual(commands[-1], ("HOME", 0, 0, int(ZoneId.HOME_A), 0, 0))
        self.assertEqual(int(ZoneId.HOME_A), 5)

        home_b = CompetitionFlow(plan(), home_zone=int(ZoneId.HOME_B), still_speed_mm_s=30)
        commands = []
        drive(home_b, home_b.start(), commands, [])
        self.assertEqual(commands[-1][3], 6)

        first = CompetitionFlow(plan(), still_speed_mm_s=30)
        first_events = _events_until_second_batch(first)
        first_board = TaskBoard()
        for name in first_events:
            first_board.apply_event(name)
        self.assertEqual(first_board.grab_count, 6)
        self.assertEqual(first_board.place_count, 6)

        blocked = CompetitionFlow(plan(), still_speed_mm_s=30)
        kept = TaskBoard()
        kept.show_task(OFFICIAL)
        steps = blocked.start()
        guard = 0
        while guard < 80:
            guard += 1
            if blocked.awaiting_turntable:
                steps = blocked.offer_turntable(True, True)
                continue
            if blocked.awaiting_color:
                steps = blocked.offer_color(blocked.expected_color())
                continue
            if not steps:
                break
            step = steps[0]
            if step.name == "GOTO" and blocked.phase == "goto_staging" and blocked.batch == 2:
                lower = next(record for record in blocked.plan.records if record.instruction.material_id == "F2")
                lower.status = MaterialStatus.PLACED_COARSE
                for name in blocked.take_events():
                    kept.apply_event(name)
                before = (kept.grab_count, kept.place_count)
                self.assertEqual(blocked.travel_done(0), [])
                self.assertEqual((kept.grab_count, kept.place_count), before)
                self.assertNotEqual(before, (0, 0))
                return
            if step.name in ("GOTO", "HOME"):
                steps = blocked.travel_done(0)
            elif step.name == "PICK_TO_SLOT":
                steps = blocked.confirm(occupied(step.data["slot"]), GRIPPER_OPEN)
            else:
                steps = blocked.confirm(0, GRIPPER_OPEN)
            for name in blocked.take_events():
                kept.apply_event(name)
        self.fail("没有走到第二批暂存")

    def test_competition_preflight_fails_on_the_real_files(self):
        live = project_root() / "config" / "实机配置.json"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = __import__("json").loads(live.read_text(encoding="utf-8"))
            source["mode"] = "competition"
            config_path = root / "实机配置.json"
            config_path.write_text(__import__("json").dumps(source), encoding="utf-8")
            code = main([
                "--mode", "live",
                "--live-config", str(config_path),
                "--test-root", str(root / "logs"),
                "--log", str(root / "app.jsonl"),
            ])
        self.assertEqual(code, 1)
        text = (project_root() / "config" / "实机配置.json").read_text(encoding="utf-8")
        self.assertIn('"mode": "debug"', text)
        self.assertIn('"measured": false', (project_root() / "config" / "车道.json").read_text(encoding="utf-8"))

    def test_screen_uplink_detour_and_next_round(self):
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
                    "match_clock": {"still_speed_mm_s": 5, "start_window_s": 30},
                    "wireless": {"address": "memory", "downlink": True},
                    "loop": {"sleep_s": 0.0},
                },
            )
            try:
                self.assertFalse(runtime.wireless.downlink)
                runtime.inject_start_edge("clock")
                runtime.inject_start_edge("robot")
                runtime._poll_start_edges()
                self.assertFalse(runtime.wireless.downlink)
                runtime._publish_task_board(OFFICIAL)
                self.assertEqual(runtime.wireless.sent, [f"{OFFICIAL} 0 0"])
                runtime.task_board.apply_event("slot_loaded_from_source")
                runtime._sync_task_board((0, 0))
                self.assertEqual(runtime.wireless.sent[-1], f"{OFFICIAL} 1 0")
                runtime._sync_task_board((1, 0))
                self.assertEqual(len(runtime.wireless.sent), 2)
                runtime.speed_mm_s = 12
                runtime.note_downlink({"speed_mm_s": 300})
                self.assertEqual(runtime.speed_mm_s, 12)

                runtime.lane = LaneMap(True, ((0, 0), (200, 0), (200, 200), (0, 200)), {"coarse": [190, 100]})
                runtime.odometry_xy = (10, 100)
                runtime.obstacle_min_confidence = 0.8
                runtime.obstacle_width_tolerance_mm = 10
                runtime.obstacle_clearance_mm = None
                runtime.obstacle_sights = [ObstacleSight(0.95, True, 50, center_mm=(100, 100))]
                runtime._review_obstacles()
                goto = MissionAction("GOTO", {"slot": 0, "color": 0, "zone": int(ZoneId.COARSE), "ring": 0, "batch": 1})
                runtime._execute([goto], 1_000)
                self.assertIsNone(runtime.round_end_reason)
                self.assertNotIn("GOTO", self._names(runtime))
                runtime.obstacle_clearance_mm = 30
                runtime._review_obstacles()
                runtime._execute([goto], 1_100)
                self.assertIsNone(runtime.round_end_reason)
                # 折线路径先发 WAYPOINT，航点逐个 ACK/DONE 后才续发最终 GOTO。
                for _ in range(10):
                    if "GOTO" in self._names(runtime):
                        break
                    runtime._poll_controller(1_200)
                self.assertIn("GOTO", self._names(runtime))

                runtime.task_board.grab_count = 12
                runtime.task_board.place_count = 9
                runtime.round_end_reason = "stalled"
                runtime.latch.end()
                self.assertTrue(runtime.begin_next_round())
                self.assertEqual(runtime.task_board.grab_count, 0)
                self.assertEqual(runtime.task_board.place_count, 0)
                self.assertIsNone(runtime.round_end_reason)
                self.assertFalse(runtime.latch.robot_start(10))
                mismatch = CompetitionFlow(plan())
                mismatch.phase = "place_coarse"
                mismatch.awaiting_color = True
                runtime.machine.flow = mismatch
                runtime.machine.competition_mode = True
                runtime.machine.round_end_reason = None
                runtime.round_end_reason = None
                runtime.latch.state = LatchState.RUNNING
                runtime._dispatch("target_color", {"color": "blue"})
                self.assertEqual(runtime.round_end_reason, "color_mismatch")
                self.assertIn("STOP", self._names(runtime))
            finally:
                runtime.close()

    def test_review_fixes_for_stack_search_and_uplink(self):
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
                    "match_clock": {"still_speed_mm_s": 5, "start_window_s": 30},
                    "wireless": {"address": "memory", "downlink": False},
                    "loop": {"sleep_s": 0.0},
                },
            )
            try:
                runtime.sensors.tof_distance_mm = 120
                runtime.gripper = GRIPPER_CLOSED
                runtime.use_fine_localization = False
                stack = MissionAction("STACK_FROM_SLOT", {"slot": 1, "color": 1, "zone": int(ZoneId.STAGING), "ring": 1, "batch": 2})
                runtime._execute([stack], 1_000)
                self.assertIsNone(runtime.round_end_reason)
                self.assertIn("STACK_FROM_SLOT", self._names(runtime))

                runtime.arrival_threshold_mm = None
                runtime.lane = LaneMap(
                    True,
                    ((0, 0), (80, 0), (80, 80), (0, 80)),
                    {"qr_view": [10, 10]},
                    arrival_threshold_mm=20,
                )
                runtime.odometry_xy = (10, 10)
                runtime.speed_mm_s = 0
                runtime.machine.still_speed_mm_s = 5
                self.assertTrue(runtime._search_settled((12, 12)))

                runtime._obstacle_previous = (ObstacleSight(0.95, True, 50, center_mm=(10, 10)),)
                runtime._obstacle_can_plan = False
                runtime._qr_pose_sent = -1
                runtime.scanner_mode = "camera_qr"
                runtime.machine.state = MissionState.S1_READ_TASK
                runtime._qr_search = QrSearch(((10, 10), (40, 10)))
                self.assertIsNone(runtime._competition_qr_read(np.zeros((8, 8, 3), dtype=np.uint8)))
                self.assertEqual(runtime._qr_pose_sent, -1)
                self.assertIsNone(runtime.round_end_reason)
                self.assertNotIn("GOTO", self._names(runtime))

                runtime.display.fail = True
                runtime.machine.display_pending = True
                runtime._publish_task_board(OFFICIAL)
                self.assertEqual(runtime.wireless.sent, [])
                runtime.display.fail = False
                runtime._retry_task_board()
                self.assertEqual(runtime.wireless.sent, [f"{OFFICIAL} 0 0"])
            finally:
                runtime.close()

    def _names(self, runtime) -> list:
        parser = FrameParser()
        return [
            frame.message_type.name
            for packet in runtime.controller.written_packets
            for frame in parser.feed(packet)
        ]


if __name__ == "__main__":
    unittest.main()
