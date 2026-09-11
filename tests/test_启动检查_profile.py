# -*- coding: utf-8 -*-
"""启动检查 profile 门禁回归。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.启动检查 import (
    REQUIRED_FILES,
    ROOT_DIR,
    _apply_profile_gates,
    run_preflight,
)


class PreflightProfileTests(unittest.TestCase):
    def test_simulation_accepts_current_project(self):
        report = run_preflight(profile="simulation")
        self.assertTrue(report["passed"], report["errors"])
        self.assertEqual(report["profile"], "simulation")
        self.assertTrue(any("站点坐标为 0" in w for w in report["warnings"]))

    def test_live_fails_on_zero_stations_and_missing_calibration(self):
        report = run_preflight(profile="live")
        self.assertFalse(report["passed"])
        self.assertEqual(report["profile"], "live")
        joined = "\n".join(report["errors"])
        self.assertIn("站点坐标为 0", joined)
        self.assertIn("calibration", joined)

    def test_competition_requires_fine_localization_and_sensors(self):
        report = run_preflight(profile="competition")
        self.assertFalse(report["passed"])
        joined = "\n".join(report["errors"])
        self.assertIn("use_fine_localization=true", joined)
        self.assertIn("ToF 与抓取确认均未接入", joined)

    def test_profile_gates_promote_zero_stations_to_error(self):
        errors: list[str] = []
        warnings = ["config/任务配置.json: 全部站点坐标为 0，尚未实测冻结；实机运行前必须填写真实坐标"]
        data = {
            "stations": {
                "start": {"x_mm": 0.0, "y_mm": 0.0, "yaw_deg": 0.0},
            }
        }
        _apply_profile_gates("live", "任务配置.json", data, "config/任务配置.json", errors, warnings)
        self.assertTrue(any("站点坐标为 0" in e for e in errors))
        self.assertFalse(any("站点坐标为 0" in w for w in warnings))

    def test_live_passes_with_ready_configs(self):
        """用临时目录构造满足 live 门禁的最小配置集。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()

            color = {
                "colors": {
                    "red": {
                        "ranges": [[[0, 100, 100], [10, 255, 255]]],
                        "kernel_size": 5,
                    }
                },
                "tracking": {"min_area": 100, "max_area": 0},
            }
            (config_dir / "颜色追踪配置.json").write_text(json.dumps(color), encoding="utf-8")
            (config_dir / "站点标签.json").write_text(
                json.dumps({"dictionary": "DICT_APRILTAG_36h11", "tag_size_mm": 40.0, "stations": {"1": "a"}}),
                encoding="utf-8",
            )
            (config_dir / "精定位配置.json").write_text(
                json.dumps({
                    "required_samples": 5,
                    "max_spread_mm": 5.0,
                    "max_step_mm": 30.0,
                    "timeout_ms": 1500.0,
                    "lost_timeout_ms": 300.0,
                }),
                encoding="utf-8",
            )
            (config_dir / "仿真任务.json").write_text(
                json.dumps({
                    "task_id": "T1",
                    "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
                    "second_batch": [],
                }),
                encoding="utf-8",
            )
            (config_dir / "任务配置.json").write_text(
                json.dumps({
                    "barcode_mapping": {"1": [0.0, 0.0]},
                    "stations": {
                        "start": {"x_mm": 10.0, "y_mm": 20.0, "yaw_deg": 0.0},
                        "temporary": {"x_mm": 100.0, "y_mm": 0.0, "yaw_deg": 0.0},
                        "processing": {"x_mm": 200.0, "y_mm": 0.0, "yaw_deg": 90.0},
                        "finished": {"x_mm": 300.0, "y_mm": 0.0, "yaw_deg": 180.0},
                    },
                }),
                encoding="utf-8",
            )
            calib_path = config_dir / "camera_calibration.json"
            calib_path.write_text(
                json.dumps({
                    "image_size": [640, 480],
                    "camera_matrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                    "dist_coeffs": [[0, 0, 0, 0, 0]],
                    "reprojection_error_px": 0.1,
                    "homography": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
                    "world_unit": "mm",
                }),
                encoding="utf-8",
            )
            (config_dir / "实机配置.json").write_text(
                json.dumps({
                    "camera": {"source": 0},
                    "scanner": {"mode": "camera_qr", "max_line_length": 512},
                    "controller": {"port": "/dev/ttyUSB1"},
                    "color_config": "config/颜色追踪配置.json",
                    "station_config": "config/站点标签.json",
                    "fine_localization": "config/精定位配置.json",
                    "calibration": "config/camera_calibration.json",
                    "use_fine_localization": False,
                    "sensors": {"tof_distance_mm": None, "grasp_confirmed": None},
                    "safety": {"minimum_tof_mm": 80.0},
                }),
                encoding="utf-8",
            )

            files = tuple(config_dir / name for name in (
                "颜色追踪配置.json",
                "任务配置.json",
                "仿真任务.json",
                "站点标签.json",
                "精定位配置.json",
                "实机配置.json",
            ))
            with patch("tools.启动检查.ROOT_DIR", root), patch("tools.启动检查.REQUIRED_FILES", files):
                # 覆盖 _resolve_config_path 使用的 ROOT_DIR
                report = run_preflight(profile="live")
            self.assertTrue(report["passed"], report["errors"])
            self.assertTrue(any("ToF 与抓取确认均未接入" in w for w in report["warnings"]))

            with patch("tools.启动检查.ROOT_DIR", root), patch("tools.启动检查.REQUIRED_FILES", files):
                competition = run_preflight(profile="competition")
            self.assertFalse(competition["passed"])
            joined = "\n".join(competition["errors"])
            self.assertIn("use_fine_localization=true", joined)
            self.assertIn("ToF 与抓取确认均未接入", joined)


if __name__ == "__main__":
    unittest.main()
