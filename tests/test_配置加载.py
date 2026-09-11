# -*- coding: utf-8 -*-
"""配置加载与参数化接线回归测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from vision.站点定位 import AprilTagStationDetector, load_station_config, normalize_station_config
from vision.精定位 import FineLocalizer, load_fine_localization_config
from vision.颜色追踪 import DEFAULT_CONFIG, load_config
from vision.配置加载 import deep_merge, project_root, resolve_path


class ConfigLoaderTests(unittest.TestCase):
    def test_resolve_path_joins_project_root(self):
        resolved = resolve_path("config/颜色追踪配置.json")
        self.assertEqual(resolved, project_root() / "config" / "颜色追踪配置.json")
        absolute = project_root() / "config" / "站点标签.json"
        self.assertEqual(resolve_path(absolute), absolute)

    def test_deep_merge_replaces_lists(self):
        merged = deep_merge(
            {"tracking": {"min_area": 500, "roi": None}, "colors": {"red": {"kernel_size": 5}}},
            {"tracking": {"min_area": 300}, "colors": {"red": {"ranges": [[[0, 1, 2], [3, 4, 5]]]}}},
        )
        self.assertEqual(merged["tracking"]["min_area"], 300)
        self.assertIsNone(merged["tracking"]["roi"])
        self.assertEqual(merged["colors"]["red"]["ranges"], [[[0, 1, 2], [3, 4, 5]]])
        self.assertEqual(merged["colors"]["red"]["kernel_size"], 5)

    def test_color_config_loads_disk_and_keeps_defaults(self):
        path = project_root() / "config" / "颜色追踪配置.json"
        config = load_config(str(path))
        self.assertIn("preprocess", config)
        self.assertIn("confidence", config)
        self.assertIn("roi", config["tracking"])
        self.assertEqual(config["preprocess"]["blur_ksize"], 5)
        with tempfile.TemporaryDirectory() as tmp:
            partial = Path(tmp) / "partial.json"
            partial.write_text(json.dumps({"tracking": {"min_area": 123}}), encoding="utf-8")
            merged = load_config(str(partial))
            self.assertEqual(merged["tracking"]["min_area"], 123)
            self.assertEqual(merged["tracking"]["confirm_frames"], DEFAULT_CONFIG["tracking"]["confirm_frames"])
            self.assertIn("red", merged["colors"])

    def test_color_config_rejects_null_colors(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"colors": None}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(str(bad))

    def test_color_config_rejects_invalid_kernel_and_hsv(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad_kernel = Path(tmp) / "bad_kernel.json"
            bad_kernel.write_text(json.dumps({"colors": {"red": {"kernel_size": 0}}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(str(bad_kernel))
            bad_hsv = Path(tmp) / "bad_hsv.json"
            bad_hsv.write_text(
                json.dumps({"colors": {"red": {"ranges": [[[180, 0, 0], [179, 255, 255]]]}}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(str(bad_hsv))

    def test_station_config_from_disk(self):
        path = project_root() / "config" / "站点标签.json"
        config = load_station_config(path)
        detector = AprilTagStationDetector.from_config(config)
        self.assertEqual(detector.station_mapping[1], "material_area")
        self.assertEqual(detector.station_mapping[2], "assembly_area")
        self.assertEqual(detector.tag_size_mm, 40.0)

    def test_station_mapping_replaces_wholly(self):
        config = normalize_station_config({"stations": {"1": "start"}})
        self.assertEqual(config["stations"], {"1": "start"})
        self.assertNotIn("2", config["stations"])
        detector = AprilTagStationDetector.from_config(config)
        self.assertEqual(detector.station_mapping, {1: "start"})

    def test_station_from_config_normalizes_partial_dict(self):
        detector = AprilTagStationDetector.from_config({"tag_size_mm": 50.0})
        self.assertEqual(detector.tag_size_mm, 50.0)
        self.assertEqual(detector.station_mapping[1], "material_area")

    def test_station_mapping_rejects_invalid_key(self):
        with self.assertRaises(ValueError):
            AprilTagStationDetector.from_config({"stations": {"A1": "start"}})

    def test_station_from_config_rejects_path_and_config(self):
        with self.assertRaises(ValueError):
            AprilTagStationDetector.from_config(
                {"stations": {"1": "a"}},
                path=project_root() / "config" / "站点标签.json",
            )

    def test_fine_localization_from_disk_and_overrides(self):
        path = project_root() / "config" / "精定位配置.json"
        config = load_fine_localization_config(path)
        self.assertEqual(config["required_samples"], 5)
        localizer = FineLocalizer.from_config(path=path, target_id=1, required_samples=2)
        self.assertEqual(localizer.required_samples, 2)
        self.assertEqual(localizer.max_spread_mm, config["max_spread_mm"])

    def test_fine_from_config_rejects_path_and_config(self):
        path = project_root() / "config" / "精定位配置.json"
        with self.assertRaises(ValueError):
            FineLocalizer.from_config({"required_samples": 3}, path=path, target_id=1)


if __name__ == "__main__":
    unittest.main()
