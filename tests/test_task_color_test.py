import unittest
from pathlib import Path
import cv2
import numpy as np
from tools.task_color_test import parse_order, UnsupportedTaskCode, current_targets
from vision.颜色追踪 import ColorTracker, load_config

ROOT = Path(__file__).resolve().parents[1]

class TaskColorTests(unittest.TestCase):
    def test_official_four_groups(self):
        task, items = parse_order(" 123+231+312+123\r\n")
        self.assertEqual([i.color for i in items], ["red", "yellow", "blue", "blue", "red", "yellow"])
        self.assertEqual(len(task.first_batch), 3)
        self.assertEqual(len({i.material_id for i in items}), 6)

    def test_invalid_codes(self):
        for text in ("123+956+123+231", "123", "123+231extra", "１２３+２３１"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_order(text)

    def test_four_groups_include_ring_codes(self):
        task, items = parse_order("452+321+254+312")
        self.assertEqual([i.ring_code for i in items], ["3", "2", "1", "3", "1", "2"])

    def test_legacy_json(self):
        _, items = parse_order((ROOT / "config/仿真任务.json").read_text(encoding="utf-8-sig"))
        self.assertTrue(items)

    def test_actual_detection_wrong_color_and_loss(self):
        _, items = parse_order("123+231+312+123")
        bgr = {"red": (0, 0, 255), "yellow": (0, 255, 255), "green": (0, 255, 0),
               "blue": (255, 0, 0), "black": (0, 0, 0), "light_blue": (255, 255, 0)}
        config = load_config(str(ROOT / "config/颜色追踪配置.json"))
        for item in items:
            if item.color not in bgr or item.color not in config:
                continue
            tracker = ColorTracker([item.color], config=config)
            wrong = "green" if item.color == "red" else "red"
            frame = np.zeros((240, 320, 3), dtype=np.uint8)
            cv2.circle(frame, (160, 120), 35, bgr[wrong], -1)
            for _ in range(4):
                _, result = tracker.track(frame.copy())
                self.assertFalse(current_targets(result, item.color))
            frame[:] = 0
            cv2.circle(frame, (160, 120), 35, bgr[item.color], -1)
            for _ in range(4):
                _, result = tracker.track(frame.copy())
            self.assertTrue(current_targets(result, item.color))
            _, result = tracker.track(np.zeros_like(frame))
            self.assertFalse(current_targets(result, item.color))

    def test_timeout_rejects_stale_targets(self):
        result = {"colors": {"red": {"timed_out": True, "valid_targets": [
            {"color": "red", "valid": True, "status": "confirmed"}]}}}
        self.assertFalse(current_targets(result, "red"))

if __name__ == "__main__":
    unittest.main()
