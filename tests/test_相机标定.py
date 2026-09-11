import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from vision.相机标定 import CameraCalibration
from vision.颜色追踪 import ColorTracker, load_config


class CameraCalibrationTests(unittest.TestCase):
    def make_calibration(self):
        return CameraCalibration(
            image_size=(500, 300),
            camera_matrix=np.eye(3, dtype=np.float64),
            dist_coeffs=np.zeros((5, 1), dtype=np.float64),
            reprojection_error_px=0.1,
            homography=np.array([[2.0, 0.0, 10.0], [0.0, 2.0, 20.0], [0.0, 0.0, 1.0]]),
        )

    def test_pixel_to_world_uses_planar_mapping(self):
        calibration = self.make_calibration()
        self.assertEqual(calibration.pixel_to_world((30, 40)), (70.0, 100.0))

    def test_save_and_load_preserves_mapping(self):
        calibration = self.make_calibration()
        calibration.valid_image_count = 4
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "camera.json")
            calibration.save(path)
            loaded = CameraCalibration.from_file(path)
            self.assertEqual(loaded.image_size, (500, 300))
            self.assertEqual(loaded.valid_image_count, 4)
            self.assertEqual(loaded.pixel_to_world((30, 40)), (70.0, 100.0))

    def test_tracker_emits_unified_world_coordinate_interface(self):
        config = load_config(None)
        config["tracking"]["min_area"] = 100
        config["tracking"]["confirm_frames"] = 1
        config["tracking"]["timeout_ms"] = 1000
        tracker = ColorTracker(["red"], config=config, calibration=self.make_calibration())
        frame = np.zeros((300, 500, 3), dtype=np.uint8)
        cv2.rectangle(frame, (50, 100), (130, 180), (0, 0, 255), -1)
        _, result = tracker.track(frame)
        self.assertEqual(result["schema_version"], "2.0")
        self.assertEqual(result["coordinate_status"], "world_mm")
        self.assertEqual(len(result["targets"]), 1)
        self.assertIsNotNone(result["targets"][0]["world_mm"])


if __name__ == "__main__":
    unittest.main()
