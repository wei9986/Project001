# -*- coding: utf-8 -*-
"""Qt 调参界面的配置回写逻辑测试（不启动 GUI）。"""

from __future__ import annotations

import copy
import unittest

from tools.qt_tuner.config_binding import (
    apply_color_morphology,
    apply_hsv_sliders,
    apply_live_basic_values,
    apply_preprocess_values,
    apply_tracking_values,
    build_fine_localization_config,
    build_station_config,
    clamp_hsv_pair,
    read_hsv_from_config,
)
from vision.颜色追踪 import load_config


class QtConfigBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(None)

    def test_clamp_hsv_pair_swaps_inverted_bounds(self):
        self.assertEqual(clamp_hsv_pair(100, 10, 200, 50, 255, 0), (10, 100, 50, 200, 0, 255))

    def test_apply_hsv_sliders_writes_range(self):
        apply_hsv_sliders(self.config, "red", 0, (5, 15, 40, 180, 60, 220))
        self.assertEqual(self.config["colors"]["red"]["ranges"][0], [[5, 40, 60], [15, 180, 220]])
        self.assertEqual(read_hsv_from_config(self.config, "red", 0), [5, 15, 40, 180, 60, 220])

    def test_apply_tracking_values_and_roi(self):
        apply_tracking_values(
            self.config,
            min_area=321,
            max_area=0,
            confirm_frames=4,
            max_match_distance=66.5,
            max_lost_frames=7,
            min_circularity=0.35,
            min_solidity=0.4,
            roi_enabled=True,
            roi=[10, 20, 100, 80],
        )
        tracking = self.config["tracking"]
        self.assertEqual(tracking["min_area"], 321)
        self.assertEqual(tracking["confirm_frames"], 4)
        self.assertEqual(tracking["roi"], [10, 20, 100, 80])
        apply_tracking_values(
            self.config,
            min_area=321,
            max_area=0,
            confirm_frames=4,
            max_match_distance=66.5,
            max_lost_frames=7,
            min_circularity=0.35,
            min_solidity=0.4,
            roi_enabled=False,
            roi=[10, 20, 100, 80],
        )
        self.assertIsNone(self.config["tracking"]["roi"])

    def test_apply_morphology_and_preprocess(self):
        apply_color_morphology(
            self.config, "green", kernel_size=6, open_iterations=2, close_iterations=3
        )
        self.assertEqual(self.config["colors"]["green"]["kernel_size"], 7)  # 偶数抬到奇数
        self.assertEqual(self.config["colors"]["green"]["open_iterations"], 2)
        apply_preprocess_values(self.config, blur_ksize=4, blur_sigma=1.5)
        self.assertEqual(self.config["preprocess"]["blur_ksize"], 5)
        self.assertEqual(self.config["preprocess"]["blur_sigma"], 1.5)

    def test_hsv_write_does_not_touch_other_colors(self):
        before = copy.deepcopy(self.config["colors"]["blue"])
        apply_hsv_sliders(self.config, "red", 0, (1, 2, 3, 4, 5, 6))
        self.assertEqual(self.config["colors"]["blue"], before)

    def test_build_station_config_replaces_mapping(self):
        config = build_station_config(
            dictionary="DICT_APRILTAG_36h11",
            tag_size_mm=40.0,
            stations={"1": "start"},
        )
        self.assertEqual(config["stations"], {"1": "start"})
        self.assertNotIn("2", config["stations"])

    def test_build_station_config_rejects_bad_tag_id(self):
        with self.assertRaises(ValueError):
            build_station_config(
                dictionary="DICT_APRILTAG_36h11",
                tag_size_mm=40.0,
                stations={"abc": "start"},
            )
        with self.assertRaises(ValueError):
            build_station_config(
                dictionary="DICT_APRILTAG_36h11",
                tag_size_mm=40.0,
                stations={"1": "  "},
            )

    def test_build_fine_localization_config(self):
        config = build_fine_localization_config(
            required_samples=4,
            max_spread_mm=3.0,
            max_step_mm=25.0,
            timeout_ms=1200.0,
            lost_timeout_ms=250.0,
        )
        self.assertEqual(config["required_samples"], 4)
        with self.assertRaises(ValueError):
            build_fine_localization_config(
                required_samples=1,
                max_spread_mm=3.0,
                max_step_mm=25.0,
                timeout_ms=1200.0,
                lost_timeout_ms=250.0,
            )

    def test_apply_live_basic_values(self):
        live = {"camera": {}, "safety": {}, "loop": {}}
        apply_live_basic_values(
            live,
            camera_source="1",
            width=640,
            height=480,
            fps=25,
            colors=["red", "blue"],
            minimum_tof_mm=90.0,
            display=True,
            sleep_s=0.01,
        )
        self.assertEqual(live["camera"]["source"], 1)
        self.assertEqual(live["colors"], ["red", "blue"])
        self.assertEqual(live["safety"]["minimum_tof_mm"], 90.0)
        self.assertTrue(live["display"])


if __name__ == "__main__":
    unittest.main()
