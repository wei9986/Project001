# -*- coding: utf-8 -*-
"""标定验收门禁回归。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from tools.标定验收 import evaluate_calibration_gate
from vision.配置加载 import project_root


def _write_boards(directory: Path, count: int, *, board_w: int = 9, board_h: int = 6, sq: int = 40) -> list[str]:
    paths: list[str] = []
    width, height = board_w * sq + 120, board_h * sq + 120
    for index in range(count):
        image = np.full((height, width, 3), 255, dtype=np.uint8)
        for y in range(board_h + 1):
            for x in range(board_w + 1):
                color = 0 if (x + y) % 2 == 0 else 255
                cv2.rectangle(
                    image,
                    (60 + x * sq, 60 + y * sq),
                    (60 + (x + 1) * sq, 60 + (y + 1) * sq),
                    (color, color, color),
                    -1,
                )
        matrix = np.float32([[1, 0.02 * index, index * 2], [0.01 * index, 1, index * 1.5]])
        warped = cv2.warpAffine(image, matrix, (width, height), borderValue=(255, 255, 255))
        path = directory / f"board_{index:02d}.jpg"
        cv2.imwrite(str(path), warped)
        paths.append(str(path))
    return paths


class CalibrationGateTests(unittest.TestCase):
    def test_too_few_images_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_boards(Path(directory), 3)
            report = evaluate_calibration_gate(
                image_paths=paths,
                board_width=9,
                board_height=6,
                square_size=25.0,
                min_valid_images=6,
            )
            self.assertFalse(report["passed"])
            self.assertTrue(any("匹配图像数" in error or "有效棋盘格" in error for error in report["errors"]))

    def test_rms_gate_fails_when_threshold_too_strict(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = _write_boards(Path(directory), 8)
            report = evaluate_calibration_gate(
                image_paths=paths,
                board_width=9,
                board_height=6,
                square_size=25.0,
                min_valid_images=6,
                max_rms_px=1e-12,
            )
            self.assertFalse(report["passed"])
            self.assertTrue(any("重投影 RMS" in error for error in report["errors"]))

    def test_reference_or_synthetic_passes_default_gates(self):
        root = project_root()
        reference = sorted(str(path) for path in (root / "data" / "calibration" / "reference").glob("*.jpg"))
        synthetic = sorted(str(path) for path in (root / "data" / "calibration" / "synthetic").glob("*.jpg"))
        paths = reference if len(reference) >= 6 else synthetic
        if len(paths) < 6:
            self.skipTest("缺少 data/calibration/reference 或 synthetic 棋盘格图")
        report = evaluate_calibration_gate(
            image_paths=paths,
            board_width=9,
            board_height=6,
            square_size=25.0,
            min_valid_images=6,
            max_rms_px=1.0,
        )
        self.assertTrue(report["passed"], report["errors"])
        self.assertGreaterEqual(report["valid_images"] or 0, 6)


if __name__ == "__main__":
    unittest.main()
