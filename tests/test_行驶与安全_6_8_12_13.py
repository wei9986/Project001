# -*- coding: utf-8 -*-
"""第 6、8、12、13 项：车道、到站、障碍、伸臂距离，以及放下和回取前的颜色。"""

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from app.虚拟设备 import VirtualCamera, VirtualScanner
from mission.初赛流程 import CompetitionFlow
from mission.到站 import arm_allowed, arrived, ring_aligned
from mission.扫码读取 import parse_task_code
from mission.车道 import LaneMap, body_left_lane, load_lane, point_inside, route_to_station
from mission.任务状态机 import MissionAction
from mission.任务计划 import TaskPlan
from protocol.串口协议 import FrameParser, ZoneId
from tests.test_实机运行时 import SimulatedController, _build_runtime
from vision.相机标定 import CameraCalibration
from vision.障碍物 import ObstacleSight, assess, detect_cylinders
from vision.配置加载 import project_root

OFFICIAL = "156+123+516+231"


class LaneAndArrivalTests(unittest.TestCase):
    def test_unmeasured_lane_has_no_route(self):
        lane = load_lane(project_root() / "config" / "车道.json")
        self.assertFalse(lane.measured)
        self.assertFalse(lane.synthetic)
        self.assertIsNone(route_to_station(lane, (200, 100), "coarse"))

    def test_synthetic_lane_rejects_points_outside(self):
        lane = load_lane(project_root() / "config" / "仿真车道.json")
        self.assertTrue(lane.synthetic)
        self.assertTrue(point_inside((200, 100), lane.polygon_mm))
        self.assertFalse(point_inside((500, 100), lane.polygon_mm))
        self.assertIsNone(route_to_station(lane, (500, 100), "coarse"))
        route = route_to_station(lane, (200, 100), "coarse")
        self.assertEqual(route[1], (200.0, 900.0))

    def test_arrival_needs_position_and_speed(self):
        dock = (200.0, 900.0)
        self.assertFalse(arrived(None, dock, 50, 0, 30))
        self.assertFalse(arrived((200, 900), dock, None, 0, 30))
        self.assertFalse(arrived((200, 900), dock, 50, 0, None))
        self.assertFalse(arrived((200, 900), dock, 50, 80, 30))
        self.assertTrue(arrived((210, 910), dock, 50, 10, 30))
        self.assertFalse(arrived((210, 910), dock, 50, 10, 30, 180, 0, 20, require_heading=True))
        self.assertTrue(arrived((210, 910), dock, 50, 10, 30, 350, 0, 20, require_heading=True))
        self.assertFalse(arrived((210, 910), dock, 50, 10, 30, None, None, None, require_heading=True))

    def test_junction_polyline_stays_inside_and_body_leaves_the_lane(self):
        lane = LaneMap(
            True,
            ((0, 0), (100, 0), (100, 30), (30, 30), (30, 100), (0, 100)),
            {"coarse": [10, 80, 90]},
            junction_mm=(10, 10),
            home_zones_mm=(((0, 0), (20, 0), (20, 20), (0, 20)),),
        )
        self.assertIsNone(route_to_station(lane, (80, 10), "missing"))
        route = route_to_station(lane, (80, 10), "coarse")
        self.assertEqual(route, ((80, 10), (10, 10), (10.0, 80.0)))
        stop, departed = body_left_lane((10, 10), lane, False)
        self.assertFalse(stop)
        self.assertFalse(departed)
        stop, departed = body_left_lane((200, 200), lane, False)
        self.assertTrue(stop)
        self.assertTrue(departed)
        self.assertFalse(ring_aligned(None, (0, 0), 10))
        self.assertFalse(ring_aligned((0, 0), None, 10))
        self.assertTrue(ring_aligned((3, 4), (0, 0), 5))

    def test_tag_alone_does_not_allow_the_arm(self):
        allowed, conflict = arm_allowed(False, "coarse", None)
        self.assertFalse(allowed)
        self.assertIsNone(conflict)
        allowed, conflict = arm_allowed(True, "staging", "coarse")
        self.assertFalse(allowed)
        self.assertEqual(conflict, "localization_conflict")
        allowed, conflict = arm_allowed(True, None, "coarse")
        self.assertTrue(allowed)
        self.assertIsNone(conflict)


class ObstacleTests(unittest.TestCase):
    def test_low_confidence_does_not_stop(self):
        previous = (ObstacleSight(0.9, True, 50, blocks_path=True),)
        dim = ObstacleSight(0.2, True, 50, blocks_path=True)
        kept, stop = assess([dim], 0.8, 15, previous)
        self.assertFalse(stop)
        self.assertEqual(kept, previous)

    def test_missing_threshold_does_not_stop(self):
        sight = ObstacleSight(0.99, True, 50, blocks_path=True)
        kept, stop = assess([sight], None, None, ())
        self.assertFalse(stop)
        self.assertEqual(kept, ())

    def test_confirmed_cylinder_plans_only_with_clearance(self):
        sight = ObstacleSight(0.95, True, 48, blocks_path=True, center_mm=(100, 100))
        kept, can_plan = assess([sight], 0.8, 15, ())
        self.assertFalse(can_plan)
        self.assertEqual(len(kept), 1)
        _kept, can_plan = assess([sight], 0.8, 15, (), clearance_mm=30)
        self.assertTrue(can_plan)

    def test_cylinder_on_the_straight_line_uses_a_bypass(self):
        lane = LaneMap(True, ((0, 0), (200, 0), (200, 200), (0, 200)), {"coarse": [190, 100]})
        blocked = ObstacleSight(0.95, True, 50, center_mm=(100, 100))
        straight = route_to_station(lane, (10, 100), "coarse")
        self.assertEqual(straight[1], (190.0, 100.0))
        bypass = route_to_station(lane, (10, 100), "coarse", (blocked,), 30)
        self.assertEqual(len(bypass), 3)
        self.assertNotEqual(bypass[1], (100, 100))
        narrow = LaneMap(True, ((0, 80), (200, 80), (200, 120), (0, 120)), {"coarse": [190, 100]})
        self.assertIsNone(route_to_station(narrow, (10, 100), "coarse", (blocked,), 30))

    def test_dark_blob_uses_the_plane_mapping_and_ignores_a_bright_disc(self):
        calibration = CameraCalibration((80, 80), np.array([[100.0, 0, 40], [0, 100.0, 40], [0, 0, 1]]), np.zeros((1, 5)), 0.0)
        calibration.set_plane_mapping([(0, 0), (79, 0), (0, 79), (79, 79)], [(0, 0), (79, 0), (0, 79), (79, 79)])
        lane = LaneMap(True, ((0, 0), (80, 0), (80, 80), (0, 80)), {})
        frame = np.full((80, 80, 3), 255, dtype=np.uint8)
        cv2.circle(frame, (40, 40), 25, (0, 0, 0), -1)
        sights = detect_cylinders(frame, calibration, lane)
        self.assertEqual(len(sights), 1)
        self.assertTrue(sights[0].in_lane_roi)
        self.assertGreater(sights[0].width_mm, 40)
        self.assertLess(sights[0].width_mm, 70)
        self.assertAlmostEqual(sights[0].center_mm[0], 40, delta=8)
        outside = LaneMap(True, ((0, 0), (20, 0), (20, 20), (0, 20)), {})
        self.assertFalse(detect_cylinders(frame, calibration, outside)[0].in_lane_roi)
        bare = CameraCalibration((80, 80), calibration.camera_matrix, calibration.dist_coeffs, 0.0)
        self.assertEqual(detect_cylinders(frame, bare, lane), [])
        bright = np.full((80, 80, 3), 255, dtype=np.uint8)
        cv2.circle(bright, (40, 40), 25, (0, 0, 255), -1)
        self.assertEqual(detect_cylinders(bright, calibration, lane), [])
        mismatched = np.full((40, 40, 3), 0, dtype=np.uint8)
        self.assertEqual(detect_cylinders(mismatched, calibration, lane), [])

    def test_outside_lane_roi_is_ignored(self):
        sight = ObstacleSight(0.95, False, 50, blocks_path=True)
        _kept, stop = assess([sight], 0.8, 15, ())
        self.assertFalse(stop)


    def test_bend_sends_a_waypoint_before_the_zone_command(self):
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
                },
            )
            try:
                runtime.controller.respond = False
                runtime.lane = LaneMap(
                    True,
                    ((0, 0), (100, 0), (100, 30), (30, 30), (30, 100), (0, 100)),
                    {"coarse": [10, 80]},
                    junction_mm=(10, 10),
                )
                runtime.odometry_xy = (80, 10)
                runtime._execute([MissionAction("GOTO", {"slot": 0, "color": 0, "zone": int(ZoneId.COARSE), "ring": 0, "batch": 1})], 1_000)
                names = self._names(runtime)
                self.assertEqual(names, ["WAYPOINT"])
                self.assertIsNone(runtime.round_end_reason)
                runtime.lane = LaneMap(True, ((0, 0), (100, 0), (100, 100), (0, 100)), {"coarse": [90, 10]})
                runtime.odometry_xy = (10, 10)
                runtime.protocol.fail_pending("test")
                runtime.current_action = None
                runtime.controller.written_packets.clear()
                runtime._execute([MissionAction("GOTO", {"slot": 0, "color": 0, "zone": int(ZoneId.COARSE), "ring": 0, "batch": 1})], 1_100)
                self.assertEqual(self._names(runtime), ["GOTO"])
            finally:
                runtime.close()

    def _names(self, runtime):
        parser = FrameParser()
        return [frame.message_type.name for packet in runtime.controller.written_packets for frame in parser.feed(packet)]


class ColorGateTests(unittest.TestCase):
    def test_place_waits_until_the_same_color_is_seen(self):
        flow = CompetitionFlow(TaskPlan(parse_task_code(OFFICIAL)))
        steps = flow.start()
        self.assertEqual(flow.offer_turntable(False, True), [])
        steps = flow.offer_turntable(True, True)
        guard = 0
        while guard < 8:
            guard += 1
            if flow.awaiting_turntable:
                steps = flow.offer_turntable(True, True)
            if not steps or steps[0].name != "PICK_TO_SLOT":
                break
            step = steps[0]
            steps = flow.confirm(1 << (step.data["slot"] - 1), 0)
        self.assertEqual(steps[0].name, "GOTO")
        held = flow.travel_done(0)
        self.assertEqual(held, [])
        self.assertTrue(flow.awaiting_color)
        self.assertEqual(flow.offer_color(None), [])
        self.assertIsNone(flow.blocked)
        self.assertEqual(flow.offer_color("blue"), [])
        self.assertEqual(flow.blocked, "color_mismatch")

    def test_pick_back_rejects_the_neighbor_color(self):
        flow = CompetitionFlow(TaskPlan(parse_task_code(OFFICIAL)))
        steps = flow.start()
        steps = flow.offer_turntable(True, True)
        guard = 0
        while guard < 40:
            guard += 1
            if flow.awaiting_turntable:
                steps = flow.offer_turntable(True, True)
                continue
            if flow.awaiting_color and flow.phase == "pick_coarse":
                rejected = flow.offer_color("yellow")
                self.assertEqual(rejected, [])
                self.assertEqual(flow.blocked, "color_mismatch")
                return
            if flow.awaiting_color:
                steps = flow.offer_color(flow.expected_color())
                continue
            if not steps:
                break
            step = steps[0]
            if step.name in ("GOTO", "HOME"):
                steps = flow.travel_done(0)
            elif step.name == "PICK_TO_SLOT":
                steps = flow.confirm(1 << (step.data["slot"] - 1), 0)
            else:
                steps = flow.confirm(0, 0)
        self.fail("没有进入粗加工回取")


if __name__ == "__main__":
    unittest.main()
