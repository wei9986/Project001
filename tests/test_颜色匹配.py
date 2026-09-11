# -*- coding: utf-8 -*-
"""P1-5 颜色匹配组合爆炸回归测试。

默认 max_targets_per_color=8 使候选恒 ≤8，若贪心兜底条件写成
min(candidate, track) > 8，则 8×8 必走指数穷举（实测 ~500ms/帧），
超过 50ms 预算会把后续颜色每帧判超时。修复后按 candidate×track > 16 切贪心。
"""

import unittest
from unittest.mock import patch

import cv2
import numpy as np

from vision.颜色追踪 import ColorTracker, Target, TrackState


def make_target(center, target_id=0):
    return Target(
        target_id=target_id, color="red", center=center, area=100.0, radius=10.0,
        angle_deg=0.0, width=20.0, height=20.0, aspect_ratio=1.0,
        circularity=0.9, solidity=0.9, confidence=0.9,
        status="candidate", valid=False,
    )


class MatchRoutingTests(unittest.TestCase):
    def test_dense_matching_uses_greedy(self):
        """8×8 候选/轨迹必须走贪心路径，避免组合爆炸（P1-5 核心）。"""
        tracker = ColorTracker(["red"])
        tracks = [TrackState(target_id=i + 1, color="red", center=(i * 10, 0)) for i in range(8)]
        candidates = [make_target((i * 10, 0), target_id=i) for i in range(8)]
        with patch.object(tracker, "_greedy_track_matches", wraps=tracker._greedy_track_matches) as greedy:
            matches = tracker._best_track_matches(tracks, candidates, 100.0)
            greedy.assert_called_once()
        self.assertEqual(len(matches), 8)

    def test_dense_matching_under_50ms(self):
        """开发机上 8×8 匹配应远低于生产 50ms 预算。"""
        import time

        tracker = ColorTracker(["red"])
        tracks = [TrackState(target_id=i + 1, color="red", center=(i * 10, 0)) for i in range(8)]
        candidates = [make_target((i * 10, 0), target_id=i) for i in range(8)]
        started = time.perf_counter()
        for _ in range(20):
            tracker._best_track_matches(tracks, candidates, 100.0)
        elapsed_ms = (time.perf_counter() - started) * 1000.0 / 20.0
        self.assertLess(elapsed_ms, 50.0, f"8x8 matching avg {elapsed_ms:.2f}ms")

    def test_small_matching_stays_exact(self):
        """4×4 仍保持精确匹配，不因修复而过早切贪心。"""
        tracker = ColorTracker(["red"])
        tracks = [TrackState(target_id=i + 1, color="red", center=(i * 10, 0)) for i in range(4)]
        candidates = [make_target((i * 10, 0), target_id=i) for i in range(4)]
        with patch.object(tracker, "_greedy_track_matches", wraps=tracker._greedy_track_matches) as greedy:
            matches = tracker._best_track_matches(tracks, candidates, 100.0)
            greedy.assert_not_called()
        self.assertEqual(len(matches), 4)

    def test_dense_frame_does_not_timeout_other_colors(self):
        """实机多目标：8 个同色候选不应拖到后续颜色被判超时（P1-5）。

        注意 tracker.track() 会原地在帧上绘制标注，必须每帧重建新帧，
        否则上一帧的文字/标记会把相邻红块连成一个大轮廓、候选数骤降。
        """

        def make_dense_frame():
            frame = np.full((480, 640, 3), 255, dtype=np.uint8)
            for i in range(8):
                cv2.circle(frame, (40 + i * 70, 240), 22, (0, 0, 255), -1)
            return frame

        config = {"tracking": {"max_targets_per_color": 8, "confirm_frames": 1, "timeout_ms": 50}}
        tracker = ColorTracker(["red", "blue"], config=config)
        for _ in range(3):
            _, result = tracker.track(make_dense_frame())
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["timed_out_colors"], [])
        self.assertEqual(len(result["colors"]["red"]["valid_targets"]), 8)
        self.assertFalse(result["colors"]["blue"]["timed_out"])


if __name__ == "__main__":
    unittest.main()
