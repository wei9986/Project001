# -*- coding: utf-8 -*-
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.实机设备 import OpenCVCamera, SerialScanner
from app.测试会话 import TestSession
from app.视觉应用 import load_live_config
from mission.扫码读取 import ScanType
from protocol.串口协议 import MessageType


class LiveModuleTests(unittest.TestCase):
    def test_live_config_rejects_missing_serial_port(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.json"
            path.write_text(json.dumps({"camera": {}, "scanner": {}, "controller": {}}), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_live_config(path)

    def test_camera_qr_mode_does_not_require_a_scanner_port(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "live.json"
            path.write_text(json.dumps({
                "camera": {"source": 0}, "scanner": {"mode": "camera_qr"},
                "controller": {"port": "/dev/ttyUSB0"},
            }), encoding="utf-8")
            self.assertEqual(load_live_config(path)["scanner"]["mode"], "camera_qr")

    def test_camera_releases_after_a_read_failure(self):
        with patch("app.实机设备.cv2.VideoCapture") as factory:
            capture = factory.return_value
            capture.isOpened.return_value = True
            capture.read.return_value = (False, None)
            camera = OpenCVCamera(0, reconnect_delay_s=0)
            self.assertEqual(camera.read(), (False, None))
            self.assertEqual(camera.read_failures, 1)
            capture.release.assert_called()

    def test_serial_scanner_parses_fragmented_device_data(self):
        scanner = SerialScanner("unused", scan_type=ScanType.CODE128)
        scanner.transport.read_available = lambda: b"ABC"
        self.assertIsNone(scanner.read())
        scanner.transport.read_available = lambda: b"-01\r\n"
        result = scanner.read()
        self.assertEqual(result.raw_value, "ABC-01")
        self.assertEqual(result.scan_type, ScanType.CODE128)

    def test_test_session_writes_a_machine_readable_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            session = TestSession(Path(directory), "camera", {"camera": "test"})
            session.record_frame(10.0, 1)
            session.record_frame(0.0, 0, dropped=True)
            session.record_scan("task_qr", "T1", True, "scanner", None)
            summary = session.close("complete")
            self.assertEqual(summary["frames"], 2)
            self.assertEqual(summary["dropped_frames"], 1)
            self.assertEqual(summary["scan_success_rate"], 1.0)
            self.assertTrue(session.scans_path.exists())

    def test_live_action_command_mapping_is_explicit(self):
        from app.实机运行 import ACTION_TYPES, DONE_EVENTS
        self.assertEqual(ACTION_TYPES["PICK_TO_SLOT"], MessageType.PICK_TO_SLOT)
        self.assertEqual(ACTION_TYPES["HOME"], MessageType.HOME)
        self.assertNotIn("load_first_material", ACTION_TYPES)
        self.assertEqual(DONE_EVENTS["assemble_material"], "assembly_done")

    def test_safety_monitor_blocks_assembly_without_grasp_confirmation(self):
        """P0-2 回归：装配动作必须满足抓取确认，否则安全门拦截。"""
        from app.安全监控 import SafetyMonitor
        from app.实机设备 import LiveSensors

        monitor = SafetyMonitor(minimum_tof_mm=80.0)
        sensors = LiveSensors(tof_distance_mm=120.0, limit_triggered=False, grasp_confirmed=False)
        decision = monitor.check("assemble_material", sensors)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.fault, "grasp_not_confirmed")
        # 满足确认后放行
        sensors.grasp_confirmed = True
        self.assertTrue(monitor.check("assemble_material", sensors).allowed)

    def test_safety_monitor_blocks_too_close_and_limit(self):
        """P0-2 回归：ToF 距离与限位触发都应拦截动作。"""
        from app.安全监控 import SafetyMonitor
        from app.实机设备 import LiveSensors

        monitor = SafetyMonitor(minimum_tof_mm=80.0)
        self.assertFalse(monitor.check("load_first_material", LiveSensors(tof_distance_mm=50.0)).allowed)
        self.assertFalse(monitor.check("load_first_material", LiveSensors(limit_triggered=True)).allowed)
        self.assertTrue(monitor.check("load_first_material", LiveSensors(tof_distance_mm=120.0)).allowed)

    def test_live_runtime_safety_fault_enters_recovery_without_sending_command(self):
        """P0-2 回归：实机运行时安全故障应进入恢复，且不向控制器下发命令。"""
        import tempfile

        from app.实机运行 import LiveRuntime
        from app.视觉应用 import ActionLogger, ApplicationConfig

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
            "sensors": {"tof_distance_mm": 30.0, "limit_triggered": False, "grasp_confirmed": True},
            "loop": {"sleep_s": 0.0},
            "display": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ApplicationConfig(
                task_path=root / "task.json",
                log_path=root / "actions.jsonl",
            )
            config.task_path.write_text(json.dumps({
                "task_id": "T1",
                "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
                "second_batch": [],
            }), encoding="utf-8")
            session = TestSession(root / "session", "safety", {"mode": "live"})
            runtime = LiveRuntime(config, live_config, session)
            # 自检动作应被放行；把后续动作置于 ToF 过近的安全故障下
            with patch.object(runtime, "controller") as controller:
                runtime._dispatch("start")
                runtime._dispatch("self_test_ok")
                # 记录安全故障前已下发的命令（PING 等自检命令属于正常下发）
                written_before = len(controller.write.call_args_list)
                # 扫描有效任务码会触发 load_first_material，而 ToF=30 < 80 应被拦截
                from mission.扫码读取 import ScanResult, ScanType
                scan = ScanResult(ScanType.TASK_QR, json.dumps({
                    "task_id": "T1",
                    "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
                    "second_batch": [],
                }), True, "test")
                runtime._dispatch("task_scanned", {"scan": scan})
                # 安全故障导致进入 RECOVERY；此后不得再下发 PICK（0x24）
                self.assertEqual(runtime.machine.state.value, "recovery")
                written_after = controller.write.call_args_list
                new_packets = [call.args[0] for call in written_after[written_before:]]
                from protocol.串口协议 import FrameParser, MessageType
                parser = FrameParser()
                new_types = {frame.message_type for packet in new_packets for frame in parser.feed(packet)}
                # PICK 是危险动作，必须被安全门拦截；恢复指令可能因前序命令 pending
                # 未完成而被协议层暂缓，这不属于安全门职责范围。
                self.assertNotIn(MessageType.PICK, new_types)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
