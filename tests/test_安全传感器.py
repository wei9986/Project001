# -*- coding: utf-8 -*-
"""P0-2 实机安全传感器回归测试。

核心语义：传感器「未接入（None）」不得被当成「已接入未确认（False）」。
- grasp_confirmed = None  → 跳过装配检查（不再一票否决实机装配）
- grasp_confirmed = False → 已接入但未确认，拦截装配
- grasp_confirmed = True  → 放行装配
另覆盖 update() 每帧刷新接口与 from_config 对 null 的保留。
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.实机设备 import LiveSensors
from app.安全监控 import SafetyMonitor


class SafetySensorTests(unittest.TestCase):
    def test_assemble_allowed_when_grasp_sensor_absent(self):
        """P0-2 核心：抓取传感器未接入（None）时装配不被一票否决。"""
        monitor = SafetyMonitor(minimum_tof_mm=80.0)
        sensors = LiveSensors(grasp_confirmed=None)
        self.assertTrue(monitor.check("assemble_material", sensors).allowed)

    def test_assemble_blocked_when_grasp_confirmed_false(self):
        """抓取传感器已接入但未确认（False）时装配被拦截。"""
        monitor = SafetyMonitor(minimum_tof_mm=80.0)
        sensors = LiveSensors(grasp_confirmed=False)
        decision = monitor.check("assemble_material", sensors)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault, "grasp_not_confirmed")

    def test_assemble_allowed_when_grasp_confirmed_true(self):
        monitor = SafetyMonitor(minimum_tof_mm=80.0)
        self.assertTrue(monitor.check("assemble_material", LiveSensors(grasp_confirmed=True)).allowed)

    def test_live_sensors_update_interface(self):
        """update() 按字段更新、未提供字段保持不变；非法字段/类型报错。"""
        sensors = LiveSensors()
        self.assertIsNone(sensors.grasp_confirmed)
        self.assertIsNone(sensors.tof_distance_mm)
        sensors.update(tof_distance_mm=100.0, grasp_confirmed=True, limit_triggered=False)
        self.assertEqual(sensors.tof_distance_mm, 100.0)
        self.assertTrue(sensors.grasp_confirmed)
        self.assertFalse(sensors.limit_triggered)
        # 未提供的字段保持不变
        sensors.update(tof_distance_mm=None)
        self.assertIsNone(sensors.tof_distance_mm)
        self.assertTrue(sensors.grasp_confirmed)
        # 非法输入立即报错
        with self.assertRaises(ValueError):
            sensors.update(grasp_confirmed="yes")
        with self.assertRaises(ValueError):
            sensors.update(unknown_field=1)

    def test_from_config_preserves_none_for_unconnected_sensors(self):
        """配置里 null/缺失 = 未接入，保持 None；显式 false 才是接入未确认。"""
        sensors = LiveSensors.from_config({"tof_distance_mm": None, "grasp_confirmed": None})
        self.assertIsNone(sensors.grasp_confirmed)
        self.assertIsNone(sensors.tof_distance_mm)

        sensors = LiveSensors.from_config({"grasp_confirmed": False})
        self.assertIs(sensors.grasp_confirmed, False)

        sensors = LiveSensors.from_config({"tof_distance_mm": 120.0})
        self.assertEqual(sensors.tof_distance_mm, 120.0)
        self.assertIsNone(sensors.grasp_confirmed)

        sensors = LiveSensors.from_config({})
        self.assertIsNone(sensors.grasp_confirmed)
        self.assertIsNone(sensors.tof_distance_mm)

    def test_from_config_rejects_invalid_types(self):
        with self.assertRaises(ValueError):
            LiveSensors.from_config({"grasp_confirmed": "yes"})
        with self.assertRaises(ValueError):
            LiveSensors.from_config({"tof_distance_mm": "near"})

    def test_live_runtime_logs_safety_warning_when_sensors_unconnected(self):
        """反向门：未接入传感器时实机运行时明确记录告警，不静默。"""
        from app.实机运行 import LiveRuntime
        from app.视觉应用 import ActionLogger, ApplicationConfig
        from app.测试会话 import TestSession

        live_config = {
            "camera": {"source": "0", "width": 320, "height": 240, "fps": 15, "reconnect_delay_s": 0.0},
            "scanner": {"mode": "camera_qr"},
            "controller": {"port": "unused", "baudrate": 115200, "reconnect_delay_s": 0.0},
            "protocol": {"heartbeat_interval_ms": 100, "offline_timeout_ms": 1000,
                         "ack_timeout_ms": 100, "max_retries": 0},
            "colors": ["red"],
            "color_config": "config/颜色追踪配置.json",
            "station_config": "config/站点标签.json",
            "fine_localization": "config/精定位配置.json",
            "calibration": None,
            "safety": {"minimum_tof_mm": 80.0},
            "sensors": {"tof_distance_mm": None, "limit_triggered": False, "grasp_confirmed": None},
            "loop": {"sleep_s": 0.0},
            "display": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ApplicationConfig(task_path=root / "task.json", log_path=root / "actions.jsonl")
            config.task_path.write_text(json.dumps({
                "task_id": "T1",
                "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
                "second_batch": [],
            }), encoding="utf-8")
            session = TestSession(root / "session", "safety", {"mode": "live"})
            runtime = LiveRuntime(config, live_config, session)
            events = session.events_path.read_text(encoding="utf-8").splitlines()
            joined = "\n".join(events)
            self.assertIn("safety_status", joined)
            self.assertIn("safety_warning", joined)
            runtime.close()

    def test_sensor_contract_four_scenarios(self):
        """传感器契约：tof 过近 / grasp False / grasp None / limit。"""
        monitor = SafetyMonitor(minimum_tof_mm=80.0)
        self.assertEqual(
            monitor.check("load_first_material", LiveSensors(tof_distance_mm=50.0)).fault,
            "tof_too_close",
        )
        self.assertEqual(
            monitor.check("assemble_material", LiveSensors(grasp_confirmed=False)).fault,
            "grasp_not_confirmed",
        )
        self.assertTrue(monitor.check("assemble_material", LiveSensors(grasp_confirmed=None)).allowed)
        self.assertEqual(
            monitor.check("place_ring", LiveSensors(limit_triggered=True)).fault,
            "limit_triggered",
        )


if __name__ == "__main__":
    unittest.main()
