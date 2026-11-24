# -*- coding: utf-8 -*-
"""方案里其余几项：场地门禁、颜色冲突、圆度、转盘时间、无线和传感器来源。"""

import unittest

import cv2
import numpy as np

from app.无线日志 import WirelessLog
from app.启动锁存 import LatchState, StartLatch
from mission.传感器门禁 import reports_stale, sources_ready
from mission.初赛流程 import CompetitionFlow
from mission.扫码读取 import parse_task_code
from mission.转盘 import grab_window_open, turntable_config_errors
from mission.任务计划 import TaskPlan
from vision.颜色追踪 import ColorTracker, load_config
from vision.配置加载 import project_root

OFFICIAL = "156+123+516+231"


class FieldGateTests(unittest.TestCase):
    def test_competition_field_file_is_unmeasured_and_sim_file_is_separate(self):
        import json
        field = json.loads((project_root() / "config" / "任务配置.json").read_text(encoding="utf-8"))
        sim = json.loads((project_root() / "config" / "仿真场地.json").read_text(encoding="utf-8"))
        self.assertIsNone(field["odometry_source"])
        self.assertIsNone(field["ring_centers_mm"]["coarse"]["1"])
        self.assertTrue(sim["synthetic"])
        self.assertEqual(sim["stations"]["coarse"]["x_mm"], 200.0)
        self.assertNotEqual(sim["ring_centers_mm"]["coarse"]["1"], sim["ring_centers_mm"]["staging"]["1"])


class ColorAndShapeTests(unittest.TestCase):
    def test_overlap_is_conflict_and_not_confirmed(self):
        config = load_config(str(project_root() / "config" / "颜色追踪配置.json"))
        config["colors"]["red"]["ranges"] = [[[0, 0, 0], [179, 255, 255]]]
        config["colors"]["blue"]["ranges"] = [[[0, 0, 0], [179, 255, 255]]]
        config["tracking"]["confirm_frames"] = 1
        config["tracking"]["min_area"] = 50
        tracker = ColorTracker(("red", "blue"), config)
        frame = np.zeros((80, 80, 3), dtype=np.uint8)
        cv2.circle(frame, (40, 40), 18, (0, 0, 255), -1)
        _image, result = tracker.track(frame)
        self.assertFalse(any(target.get("valid") for target in result["targets"]))
        self.assertTrue(result["conflicts"])
        self.assertTrue(all(item["status"] == "conflict" for item in result["conflicts"]))

    def test_measured_circularity_keeps_circle_and_drops_square(self):
        config = load_config(str(project_root() / "config" / "颜色追踪配置.json"))
        config["tracking"]["confirm_frames"] = 1
        config["tracking"]["min_area"] = 80
        config["tracking"]["min_circularity"] = 0.85
        config["tracking"]["min_solidity"] = 0.85
        tracker = ColorTracker(("red",), config)
        circle = np.zeros((120, 120, 3), dtype=np.uint8)
        square = np.zeros((120, 120, 3), dtype=np.uint8)
        cv2.circle(circle, (60, 60), 28, (0, 0, 255), -1)
        cv2.rectangle(square, (20, 20), (100, 100), (0, 0, 255), -1)
        _image, round_result = tracker.track(circle)
        tracker.use_colors(("red",))
        _image, square_result = tracker.track(square)
        self.assertTrue(any(target["color"] == "red" and target["shape"] == "revolute" for target in round_result["targets"]))
        self.assertFalse(square_result["targets"])

    def test_unmeasured_shape_confirms_nothing_in_competition(self):
        config = load_config(str(project_root() / "config" / "颜色追踪配置.json"))
        config["tracking"]["confirm_frames"] = 1
        config["tracking"]["min_area"] = 50
        tracker = ColorTracker(("red",), config)
        tracker.require_measured_shape = True
        frame = np.zeros((80, 80, 3), dtype=np.uint8)
        cv2.circle(frame, (40, 40), 18, (0, 0, 255), -1)
        _image, result = tracker.track(frame)
        self.assertEqual(result["targets"], [])


class TurntableAndRadioTests(unittest.TestCase):
    def test_empty_turntable_times_do_not_open_the_window(self):
        self.assertIsNone(grab_window_open(None, 100, 400, 50, 0))
        self.assertFalse(grab_window_open(1000, 100, 400, 50, 600))
        self.assertTrue(grab_window_open(2000, 100, 400, 50, 200))
        self.assertEqual(turntable_config_errors({"min_dwell_ms": 1000, "recognition_ms": 0, "action_ms": 400, "margin_ms": 50}), [])
        self.assertIn("recognition_ms", turntable_config_errors({"min_dwell_ms": 1000, "recognition_ms": None, "action_ms": 400, "margin_ms": 50}))
        colors = load_config(str(project_root() / "config" / "颜色追踪配置.json"))
        self.assertTrue(all(color.get("venue_sampled") is False for color in colors["colors"].values()))
        self.assertEqual(colors["tracking"]["min_circularity"], 0.0)

    def test_moving_turntable_does_not_pick(self):
        flow = CompetitionFlow(TaskPlan(parse_task_code(OFFICIAL)))
        flow.start()
        self.assertEqual(flow.offer_turntable(False, True), [])
        self.assertEqual(flow.offer_turntable(True, False), [])
        self.assertEqual(flow.pending, None)
        picked = flow.offer_turntable(True, True)
        self.assertEqual(picked[0].name, "PICK_TO_SLOT")

    def test_running_latch_drops_downlink_and_still_sends_the_code(self):
        latch = StartLatch(competition=True, start_window_s=30)
        latch.clock_start(0)
        latch.robot_start(10)
        self.assertEqual(latch.state, LatchState.RUNNING)
        radio = WirelessLog(downlink=False, address="memory")
        self.assertIsNone(radio.receive({"speed_mm_s": 300}))
        self.assertTrue(radio.send(OFFICIAL))
        self.assertEqual(radio.sent, [OFFICIAL])
        self.assertEqual(radio.ignored, [{"speed_mm_s": 300}])
        silent = WirelessLog(downlink=False, address=None)
        self.assertFalse(silent.send(OFFICIAL))
        self.assertEqual(silent.sent, [])
        broken = WirelessLog(downlink=False, address="memory", fail=True)
        self.assertFalse(broken.send(OFFICIAL))
        self.assertEqual(broken.failures, [OFFICIAL])
        closed = WirelessLog(address="COM9", baudrate=None)
        self.assertFalse(closed.send(OFFICIAL))
        self.assertEqual(closed.sent, [])

        class _Port:
            def __init__(self):
                self.buf = b""
                self.closed = False

            def write(self, data: bytes) -> int:
                self.buf += data
                return len(data)

            def close(self) -> None:
                self.closed = True

        port = _Port()
        radio = WirelessLog(address="COM9", baudrate=9600, transport=port)
        self.assertTrue(radio.send(f"{OFFICIAL} 0 0"))
        self.assertEqual(port.buf, f"{OFFICIAL} 0 0\n".encode("utf-8"))
        radio.close()
        self.assertTrue(port.closed)
        short = WirelessLog(address="COM9", baudrate=9600, transport=type("Short", (), {"write": staticmethod(lambda data: 1)})())
        self.assertFalse(short.send(OFFICIAL))
        self.assertEqual(short.failures, [OFFICIAL])

    def test_sensor_source_and_report_gap(self):
        import json

        self.assertFalse(sources_ready("none", "none"))
        self.assertFalse(sources_ready("gpio", "none"))
        self.assertTrue(sources_ready("gpio", "status_frame"))
        self.assertTrue(sources_ready("status_frame", "status_frame"))
        self.assertFalse(reports_stale(1000, 900, 0, 1000))
        self.assertTrue(reports_stale(2500, 1000, 0, 1000))
        live = json.loads((project_root() / "config" / "实机配置.json").read_text(encoding="utf-8"))
        self.assertEqual(live["sensor_source"], "status_frame")
        self.assertEqual(live["speed_source"], "status_frame")
        self.assertIsNone(live["sensors"]["grasp_confirmed"])
        self.assertEqual(live["safety"]["minimum_tof_mm"], 80.0)


if __name__ == "__main__":
    unittest.main()
