# -*- coding: utf-8 -*-
"""LiveRuntime 端到端回归：内存串口 + 虚拟相机，无真实硬件。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import patch

import numpy as np

from app.运行循环 import build_demo_frames
from app.测试会话 import TestSession
from app.实机运行 import LiveRuntime
from app.虚拟设备 import VirtualCamera, VirtualScanner
from app.视觉应用 import ApplicationConfig
from mission.扫码读取 import ScanResult, ScanType
from mission.任务状态机 import MissionState
from protocol.传输通道 import create_memory_link
from protocol.协议仿真 import ControllerBehavior, VirtualStm32
from protocol.串口协议 import FrameParser, MessageType
from vision.相机标定 import CameraCalibration


class SimulatedController:
    """MemoryTransport + VirtualStm32：写入即应答 ACK/DONE。"""

    def __init__(self, behavior: ControllerBehavior = ControllerBehavior.NORMAL) -> None:
        self.pi, self.stm = create_memory_link()
        self.stm32 = VirtualStm32(behavior)
        self.write_failures = 0
        self.fail_next_writes = 0
        self.respond = True
        self.written_packets: List[bytes] = []

    def write(self, data: bytes) -> int:
        if self.fail_next_writes > 0:
            self.fail_next_writes -= 1
            self.write_failures += 1
            raise RuntimeError("simulated write failure")
        self.written_packets.append(bytes(data))
        if not self.respond:
            return len(data)
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


def _task_payload() -> dict:
    return {
        "task_id": "E2E-001",
        "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
        "second_batch": [{"material_id": "S1", "color": "blue", "ring_code": "3"}],
    }


def _live_config(**overrides: Any) -> dict:
    base = {
        "camera": {"source": "0", "width": 640, "height": 480, "fps": 30, "reconnect_delay_s": 0.0},
        "scanner": {"mode": "serial", "port": "unused", "scan_type": "task_qr"},
        "controller": {"port": "unused", "baudrate": 115200, "reconnect_delay_s": 0.0},
        "protocol": {
            "heartbeat_interval_ms": 10_000,
            "offline_timeout_ms": 50,
            "ack_timeout_ms": 200,
            "done_timeout_ms": 500,
            "max_retries": 0,
        },
        "colors": ["red"],
        "color_config": "config/颜色追踪配置.json",
        "station_config": "config/站点标签.json",
        "fine_localization": "config/精定位配置.json",
        "fine_localization_before": ["load_first_material", "load_second_material"],
        "use_fine_localization": False,
        "calibration": None,
        "safety": {"minimum_tof_mm": 80.0},
        "sensors": {"tof_distance_mm": 120.0, "limit_triggered": False, "grasp_confirmed": True},
        "loop": {"sleep_s": 0.0},
        "display": False,
        "record_video": None,
        "archive_error_samples": False,
    }
    base.update(overrides)
    return base


def _build_runtime(
    root: Path,
    *,
    camera: Any,
    controller: Any,
    scanner: Any,
    live_overrides: Optional[dict] = None,
) -> LiveRuntime:
    config = ApplicationConfig(task_path=root / "task.json", log_path=root / "actions.jsonl")
    config.task_path.write_text(json.dumps(_task_payload()), encoding="utf-8")
    session = TestSession(root / "session", "e2e", {"mode": "live"})
    runtime = LiveRuntime(
        config,
        _live_config(**(live_overrides or {})),
        session,
        camera=camera,
        controller=controller,
        scanner=scanner,
    )
    return runtime


def _session_events(runtime: LiveRuntime) -> List[dict]:
    path = runtime.session.events_path
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


class LiveRuntimeE2ETests(unittest.TestCase):
    def test_complete_task_over_memory_serial(self):
        frames = build_demo_frames(40)
        scan = ScanResult(ScanType.TASK_QR, json.dumps(_task_payload()), True, "virtual")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = SimulatedController()
            runtime = _build_runtime(
                root,
                camera=VirtualCamera(frames, loop=True),
                controller=controller,
                scanner=VirtualScanner([scan]),
            )
            state = runtime.run(max_runtime_s=2.0)
            self.assertEqual(state, MissionState.COMPLETE)

    def test_camera_read_failure_still_ticks(self):
        class FailingCamera:
            def read(self):
                return False, None

            def metrics(self):
                return {"opened": False, "read_failures": 1}

            def close(self):
                return None

        scan = ScanResult(ScanType.TASK_QR, json.dumps(_task_payload()), True, "virtual")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = SimulatedController()
            runtime = _build_runtime(
                root,
                camera=FailingCamera(),
                controller=controller,
                scanner=VirtualScanner([scan]),
                live_overrides={
                    "protocol": {
                        "heartbeat_interval_ms": 10_000,
                        "offline_timeout_ms": 10_000,
                        "ack_timeout_ms": 200,
                        "done_timeout_ms": 500,
                        "max_retries": 0,
                    }
                },
            )
            runtime._dispatch("start")
            runtime.machine.state_timeout_ms = 5.0
            for _ in range(8):
                runtime.step()
            self.assertNotEqual(runtime.machine.state, MissionState.COMPLETE)
            events = _session_events(runtime)
            self.assertTrue(any(e.get("event") == "camera_error" for e in events))
            runtime.close()

    def test_heartbeat_offline_triggers_communication_lost(self):
        frames = build_demo_frames(8)
        scan = ScanResult(ScanType.TASK_QR, json.dumps(_task_payload()), True, "virtual")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = SimulatedController()
            runtime = _build_runtime(
                root,
                camera=VirtualCamera(frames, loop=True),
                controller=controller,
                scanner=VirtualScanner([scan]),
                live_overrides={
                    "protocol": {
                        "heartbeat_interval_ms": 10_000,
                        "offline_timeout_ms": 30,
                        "ack_timeout_ms": 200,
                        "done_timeout_ms": 500,
                        "max_retries": 0,
                    }
                },
            )
            runtime.protocol.last_received_ms = 1_000.0
            runtime._heartbeat_offline_latched = False
            with patch.object(runtime, "_now_ms", return_value=1_100.0):
                runtime._check_heartbeat_offline(1_100.0)
            events = _session_events(runtime)
            self.assertTrue(any(e.get("event") == "heartbeat_offline" for e in events))
            runtime.close()

    def test_controller_write_failure_fails_pending(self):
        frames = build_demo_frames(8)
        scan = ScanResult(ScanType.TASK_QR, json.dumps(_task_payload()), True, "virtual")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = SimulatedController()
            runtime = _build_runtime(
                root,
                camera=VirtualCamera(frames, loop=True),
                controller=controller,
                scanner=VirtualScanner([scan]),
            )
            controller.fail_next_writes = 1
            runtime._dispatch("start")
            self.assertGreaterEqual(controller.write_failures, 1)
            events = _session_events(runtime)
            self.assertTrue(any(e.get("event") == "controller_error" for e in events))
            if runtime.protocol.pending is not None:
                self.assertNotEqual(runtime.protocol.pending.message_type, MessageType.PING)
            runtime.close()

    def test_fine_localization_starts_when_enabled(self):
        frames = build_demo_frames(20)
        scan = ScanResult(ScanType.TASK_QR, json.dumps(_task_payload()), True, "virtual")
        calibration = CameraCalibration(
            image_size=(640, 480),
            camera_matrix=np.array([[500.0, 0.0, 320.0], [0.0, 500.0, 240.0], [0.0, 0.0, 1.0]]),
            dist_coeffs=np.zeros((1, 5)),
            reprojection_error_px=0.1,
            homography=np.eye(3),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calib_path = root / "calib.json"
            calibration.save(str(calib_path))
            controller = SimulatedController()
            runtime = _build_runtime(
                root,
                camera=VirtualCamera(frames, loop=True),
                controller=controller,
                scanner=VirtualScanner([scan]),
                live_overrides={
                    "use_fine_localization": True,
                    "calibration": str(calib_path),
                    "sensors": {"tof_distance_mm": 120.0, "limit_triggered": False, "grasp_confirmed": True},
                },
            )
            original_track = runtime.tracker.track

            def track_with_world(frame):
                annotated, result = original_track(frame)
                for target in result.get("targets", []):
                    target["world_mm"] = [float(target["center"][0]), float(target["center"][1])]
                    target["valid"] = True
                return annotated, result

            runtime.tracker.track = track_with_world  # type: ignore[method-assign]
            runtime.machine.state = MissionState.S1_READ_TASK
            runtime._dispatch("task_scanned", {"scan": scan})
            events = _session_events(runtime)
            self.assertTrue(
                any(e.get("event") == "fine_localization_started" for e in events),
                events[-15:] if events else events,
            )
            runtime.close()

    def test_tof_safety_blocks_pick_without_controller_sent(self):
        frames = build_demo_frames(12)
        scan = ScanResult(ScanType.TASK_QR, json.dumps(_task_payload()), True, "virtual")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            controller = SimulatedController()
            runtime = _build_runtime(
                root,
                camera=VirtualCamera(frames, loop=True),
                controller=controller,
                scanner=VirtualScanner([scan]),
                live_overrides={
                    "sensors": {"tof_distance_mm": 120.0, "limit_triggered": False, "grasp_confirmed": True},
                },
            )
            runtime.sensors.update(tof_distance_mm=50.0)
            runtime.machine.state = MissionState.S1_READ_TASK
            written_before = len(controller.written_packets)
            runtime._dispatch("task_scanned", {"scan": scan})
            self.assertEqual(runtime.machine.state, MissionState.RECOVERY)
            events = _session_events(runtime)
            self.assertTrue(any(e.get("event") == "safety_fault" for e in events))
            new_packets = controller.written_packets[written_before:]
            parser = FrameParser()
            types = {frame.message_type for packet in new_packets for frame in parser.feed(packet)}
            self.assertNotIn(MessageType.PICK, types)
            runtime.close()


if __name__ == "__main__":
    unittest.main()
