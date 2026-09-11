# -*- coding: utf-8 -*-
"""仿真 RuntimeLoop 与实机 LiveRuntime 异常行为成对回归。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from app.运行循环 import RuntimeLoop
from app.测试会话 import TestSession
from app.实机运行 import LiveRuntime
from app.虚拟设备 import VirtualCamera, VirtualScanner, VirtualSensors
from app.视觉应用 import ActionLogger, ApplicationConfig
from mission.扫码读取 import ScanResult, ScanType
from protocol.传输通道 import create_memory_link
from protocol.协议仿真 import VirtualStm32, ControllerBehavior


class SimulatedController:
    def __init__(self) -> None:
        self.pi, self.stm = create_memory_link()
        self.stm32 = VirtualStm32(ControllerBehavior.NORMAL)

    def write(self, data: bytes) -> int:
        self.pi.write(data)
        inbound = self.stm.read_available()
        if inbound:
            self.stm.write(self.stm32.process(inbound))
        return len(data)

    def read_available(self) -> bytes:
        return self.pi.read_available()

    def close(self) -> None:
        self.pi.close()
        self.stm.close()


class ExceptionAlignmentTests(unittest.TestCase):
    def test_simulation_camera_drop_still_ticks(self):
        class FailCamera:
            def read(self):
                return False, None

            def close(self):
                return None

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ApplicationConfig(root / "task.json", root / "actions.jsonl")
            config.task_path.write_text(
                json.dumps({
                    "task_id": "T1",
                    "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
                    "second_batch": [],
                }),
                encoding="utf-8",
            )
            logger = ActionLogger(config.log_path)
            runtime = RuntimeLoop(
                config=config,
                camera=FailCamera(),
                scanner=VirtualScanner([]),
                action_logger=logger,
                color_config_path=Path(__file__).resolve().parents[1] / "config" / "颜色追踪配置.json",
                sensors=VirtualSensors(grasp_confirmed=True),
                step_ms=20.0,
            )
            try:
                runtime.start()
                runtime.machine.state_timeout_ms = 5.0
                for _ in range(6):
                    snapshot = runtime.step()
                self.assertGreaterEqual(runtime.camera_errors, 1)
                self.assertIsInstance(snapshot.state, str)
            finally:
                logger.close()

    def test_simulation_vision_exception_still_ticks(self):
        frame = np.full((48, 64, 3), 255, dtype=np.uint8)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ApplicationConfig(root / "task.json", root / "actions.jsonl")
            config.task_path.write_text(
                json.dumps({
                    "task_id": "T1",
                    "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
                    "second_batch": [],
                }),
                encoding="utf-8",
            )
            logger = ActionLogger(config.log_path)
            runtime = RuntimeLoop(
                config=config,
                camera=VirtualCamera([frame], loop=True),
                scanner=VirtualScanner([]),
                action_logger=logger,
                color_config_path=Path(__file__).resolve().parents[1] / "config" / "颜色追踪配置.json",
                sensors=VirtualSensors(grasp_confirmed=True),
                step_ms=20.0,
            )
            try:
                runtime.start()
                runtime.tracker.track = MagicMock(side_effect=RuntimeError("undistort boom"))
                snapshot = runtime.step()
                self.assertGreaterEqual(runtime.camera_errors, 1)
                self.assertEqual(snapshot.target_count, 0)
            finally:
                logger.close()

    def test_live_and_simulation_both_survive_camera_drop(self):
        class FailCamera:
            def read(self):
                return False, None

            def metrics(self):
                return {"opened": False}

            def close(self):
                return None

        scan = ScanResult(
            ScanType.TASK_QR,
            json.dumps({
                "task_id": "T1",
                "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
                "second_batch": [],
            }),
            True,
            "virtual",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ApplicationConfig(root / "task.json", root / "actions.jsonl")
            config.task_path.write_text(scan.raw_value, encoding="utf-8")
            sim_logger = ActionLogger(root / "sim.actions.jsonl")
            sim = RuntimeLoop(
                config=config,
                camera=FailCamera(),
                scanner=VirtualScanner([scan]),
                action_logger=sim_logger,
                color_config_path=Path(__file__).resolve().parents[1] / "config" / "颜色追踪配置.json",
                sensors=VirtualSensors(grasp_confirmed=True),
                step_ms=20.0,
            )
            session = TestSession(root / "session", "pair", {"mode": "live"})
            live = LiveRuntime(
                config,
                {
                    "camera": {"source": "0", "reconnect_delay_s": 0.0},
                    "scanner": {"mode": "serial", "port": "unused"},
                    "controller": {"port": "unused", "baudrate": 115200, "reconnect_delay_s": 0.0},
                    "protocol": {
                        "heartbeat_interval_ms": 10_000,
                        "offline_timeout_ms": 10_000,
                        "ack_timeout_ms": 200,
                        "done_timeout_ms": 500,
                        "max_retries": 0,
                    },
                    "colors": ["red"],
                    "color_config": "config/颜色追踪配置.json",
                    "station_config": "config/站点标签.json",
                    "fine_localization": "config/精定位配置.json",
                    "calibration": None,
                    "safety": {"minimum_tof_mm": 80.0},
                    "sensors": {"tof_distance_mm": 120.0, "grasp_confirmed": True},
                    "loop": {"sleep_s": 0.0},
                    "display": False,
                    "record_video": None,
                    "archive_error_samples": False,
                },
                session,
                camera=FailCamera(),
                controller=SimulatedController(),
                scanner=VirtualScanner([scan]),
            )
            try:
                sim.start()
                for _ in range(3):
                    sim.step()
                live._dispatch("start")
                for _ in range(3):
                    live.step()
                self.assertGreaterEqual(sim.camera_errors, 1)
                events = session.events_path.read_text(encoding="utf-8")
                self.assertIn("camera_error", events)
            finally:
                sim_logger.close()
                live.close()


if __name__ == "__main__":
    unittest.main()
