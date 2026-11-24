# -*- coding: utf-8 -*-
"""第 3 项：大图能解出官方任务码，过小的图失败时是空，不是半截字符串。"""

import unittest

import cv2
import numpy as np

from mission.车道 import LaneMap, qr_search_points
from mission.扫码读取 import QRCodeReader, QrSearch, complete_task_text

OFFICIAL = "156+123+516+231"


def make_qr(text: str, size: int) -> np.ndarray:
    params = cv2.QRCodeEncoder_Params()
    params.mode = cv2.QRCodeEncoder_MODE_ALPHANUMERIC
    params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_H
    image = cv2.QRCodeEncoder_create(params).encode(text)
    image = cv2.copyMakeBorder(image, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
    return cv2.resize(image, (size, size), interpolation=cv2.INTER_NEAREST)


class QRScaleTests(unittest.TestCase):
    def test_large_official_code_decodes(self):
        reader = QRCodeReader(confirm_frames=1)
        results = reader.read(make_qr(OFFICIAL, 840))
        self.assertEqual([item.raw_value for item in results], [OFFICIAL])
        self.assertEqual(reader.read(make_qr(OFFICIAL, 840)), [])

    def test_confirmation_waits_for_repeated_frames(self):
        reader = QRCodeReader(confirm_frames=2)
        image = make_qr(OFFICIAL, 840)
        self.assertEqual(reader.read(image), [])
        self.assertEqual([item.raw_value for item in reader.read(image)], [OFFICIAL])
        self.assertEqual(reader.read(image), [])

    def test_small_image_is_empty_or_the_whole_string(self):
        reader = QRCodeReader(confirm_frames=1)
        results = reader.read(make_qr(OFFICIAL, 65))
        self.assertTrue(results == [] or [item.raw_value for item in results] == [OFFICIAL])
        for item in results:
            self.assertFalse(item.raw_value != OFFICIAL and OFFICIAL.startswith(item.raw_value))

    def test_partial_task_text_is_dropped(self):
        self.assertIsNone(complete_task_text("156+123+516"))
        self.assertIsNone(complete_task_text("156+123"))
        self.assertEqual(complete_task_text(OFFICIAL), OFFICIAL)

    def test_unmeasured_lane_has_no_search(self):
        lane = LaneMap(False, (), {"qr_view": [0, 0]}, qr_region_mm=((0, 0), (100, 0), (100, 100)), qr_search_step_mm=50)
        self.assertIsNone(qr_search_points(lane))

    def test_search_steps_inside_the_region_and_stops_when_the_code_is_complete(self):
        lane = LaneMap(
            True,
            ((0, 0), (120, 0), (120, 80), (0, 80)),
            {"qr_view": [20, 20]},
            qr_region_mm=((0, 0), (200, 0), (200, 80), (0, 80)),
            qr_search_step_mm=50,
        )
        points = qr_search_points(lane)
        self.assertEqual(points[0], (20.0, 20.0))
        self.assertIn((50.0, 50.0), points)
        self.assertNotIn((150.0, 50.0), points)
        search = QrSearch(((0, 0), (10, 0), (20, 0)))
        self.assertEqual(search.observe(None), "advance")
        self.assertEqual(search.observe("156+123"), "advance")
        self.assertEqual(search.observe(OFFICIAL), "found")
        self.assertEqual(search.found, OFFICIAL)
        self.assertIsNone(search.current())


if __name__ == "__main__":
    unittest.main()
