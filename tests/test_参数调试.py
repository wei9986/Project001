# -*- coding: utf-8 -*-
"""P1-11 参数调试 HSV 滑块越界崩溃回归测试。

拖动滑块使同通道 low > high、或越出 0..255/0..179 时，
update_hsv_range 应按通道 clamp + 交换，而不是抛 ValueError 使调参工具退出。
"""

import unittest

from tools.参数调试 import update_hsv_range


def make_config():
    return {"colors": {"red": {"ranges": [[[0, 0, 0], [179, 255, 255]]]}}}


class HsvRangeClampTests(unittest.TestCase):
    def test_crossing_bounds_are_swapped_not_raised(self):
        """H low 拖过 H high：自动交换，不崩溃（P1-11 核心）。"""
        config = make_config()
        update_hsv_range(config, "red", 0, (179, 10, 0, 255, 0, 255))
        self.assertEqual(config["colors"]["red"]["ranges"][0], [[10, 0, 0], [179, 255, 255]])

    def test_out_of_range_values_are_clamped(self):
        config = make_config()
        update_hsv_range(config, "red", 0, (-10, 300, -5, 999, 0, 255))
        self.assertEqual(config["colors"]["red"]["ranges"][0], [[0, 0, 0], [179, 255, 255]])

    def test_clamped_then_swapped(self):
        """clamp 后若 low > high（如 S low 被钳到 255、S high 为 200），仍应交换。"""
        config = make_config()
        update_hsv_range(config, "red", 0, (0, 179, 300, 200, 0, 255))
        self.assertEqual(config["colors"]["red"]["ranges"][0], [[0, 200, 0], [179, 255, 255]])

    def test_valid_values_pass_through(self):
        config = make_config()
        update_hsv_range(config, "red", 0, (10, 30, 20, 200, 30, 220))
        self.assertEqual(config["colors"]["red"]["ranges"][0], [[10, 20, 30], [30, 200, 220]])

    def test_unknown_color_still_raises(self):
        config = make_config()
        with self.assertRaises(ValueError):
            update_hsv_range(config, "blue", 0, (0, 179, 0, 255, 0, 255))


if __name__ == "__main__":
    unittest.main()
