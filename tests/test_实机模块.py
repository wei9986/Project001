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
        self.assertEqual(ACTION_TYPES["load_first_material"], MessageType.PICK)
        self.assertEqual(ACTION_TYPES["return_home"], MessageType.HOME)
        self.assertEqual(DONE_EVENTS["assemble_material"], "assembly_done")


if __name__ == "__main__":
    unittest.main()
