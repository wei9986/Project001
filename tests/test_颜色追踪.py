import unittest

import cv2
import numpy as np

from vision.颜色追踪 import ColorTracker, load_config


class ColorTrackerTests(unittest.TestCase):
    def setUp(self):
        config = load_config(None)
        config["tracking"]["min_area"] = 100
        config["tracking"]["confirm_frames"] = 2
        config["tracking"]["timeout_ms"] = 1000
        self.tracker = ColorTracker(["red"], config=config)

    def make_frame(self, second_target=True):
        frame = np.zeros((300, 500, 3), dtype=np.uint8)
        cv2.rectangle(frame, (50, 100), (130, 180), (0, 0, 255), -1)
        if second_target:
            cv2.rectangle(frame, (300, 100), (380, 180), (0, 0, 255), -1)
        return frame

    def test_multiple_targets_are_detected_and_confirmed(self):
        _, first = self.tracker.track(self.make_frame())
        self.assertEqual(len(first["colors"]["red"]["valid_targets"]), 0)
        _, second = self.tracker.track(self.make_frame())
        valid = second["colors"]["red"]["valid_targets"]
        self.assertEqual(len(valid), 2)
        self.assertEqual({target["color"] for target in valid}, {"red"})
        self.assertEqual(len({target["target_id"] for target in valid}), 2)

    def test_small_blob_is_rejected(self):
        frame = np.zeros((300, 500, 3), dtype=np.uint8)
        cv2.rectangle(frame, (10, 10), (14, 14), (0, 0, 255), -1)
        _, result = self.tracker.track(frame)
        self.assertEqual(result["colors"]["red"]["candidates"], [])

    def test_roi_out_of_bounds_is_rejected(self):
        tracker = ColorTracker(["red"], config=load_config(None), roi=(450, 0, 100, 100))
        with self.assertRaises(ValueError):
            tracker.track(self.make_frame())

    def test_confirmed_target_becomes_lost_without_fake_valid_coordinate(self):
        self.tracker.track(self.make_frame(second_target=False))
        _, confirmed = self.tracker.track(self.make_frame(second_target=False))
        self.assertEqual(len(confirmed["colors"]["red"]["valid_targets"]), 1)
        _, lost = self.tracker.track(np.zeros((300, 500, 3), dtype=np.uint8))
        self.assertEqual(lost["colors"]["red"]["valid_targets"], [])
        self.assertEqual(lost["colors"]["red"]["lost_targets"][0]["status"], "lost")


if __name__ == "__main__":
    unittest.main()
