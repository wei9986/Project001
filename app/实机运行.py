# -*- coding: utf-8 -*-
"""实机运行循环：连接相机、扫码器、颜色识别、任务状态机和 F407。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2

from app.实机设备 import OpenCVCamera, PiTelemetry, ReconnectingSerialTransport, SerialScanner
from app.测试会话 import TestSession
from app.视觉应用 import ActionLogger, ApplicationConfig
from mission.任务状态机 import MissionAction, MissionEvent, MissionState, MissionStateMachine
from mission.扫码读取 import QRCodeReader, ScanType
from protocol.串口协议 import MessageType, ProtocolEndpoint, json_payload
from vision.相机标定 import CameraCalibration
from vision.颜色追踪 import ColorTracker, DetectionLogger, load_config


ACTION_TYPES = {
    "self_test": MessageType.PING,
    "load_first_material": MessageType.PICK,
    "load_second_material": MessageType.PICK,
    "place_ring": MessageType.PLACE_RING,
    "assemble_material": MessageType.ASSEMBLE,
    "return_home": MessageType.HOME,
    "recover": MessageType.RECOVER,
    "safe_stop": MessageType.STOP,
}
DONE_EVENTS = {
    "self_test": "self_test_ok",
    "load_first_material": "material_loaded",
    "load_second_material": "material_loaded",
    "place_ring": "ring_placed",
    "assemble_material": "assembly_done",
    "return_home": "home_confirmed",
    "recover": "recovery_done",
}


class LiveRuntime:
    """真实设备运行器。F407 的 DONE 才会推进任务状态，ACK 仅表示已收到。"""

    def __init__(
        self, config: ApplicationConfig, live_config: Dict[str, Any], session: TestSession,
    ) -> None:
        root = Path(__file__).resolve().parents[1]
        camera_config = live_config["camera"]
        controller_config = live_config["controller"]
        scanner_config = live_config["scanner"]
        self.config = config
        self.live_config = live_config
        self.session = session
        self.camera = OpenCVCamera(
            source=camera_config.get("source", 0), width=camera_config.get("width"),
            height=camera_config.get("height"), fps=camera_config.get("fps"),
            reconnect_delay_s=float(camera_config.get("reconnect_delay_s", 2.0)),
        )
        self.scanner_mode = scanner_config.get("mode", "serial")
        self.scanner: Optional[SerialScanner] = None
        self.qr_reader: Optional[QRCodeReader] = None
        if self.scanner_mode == "camera_qr":
            self.qr_reader = QRCodeReader()
        else:
            self.scanner = SerialScanner(
                scanner_config["port"], int(scanner_config.get("baudrate", 9600)),
                ScanType(scanner_config.get("scan_type", "task_qr")),
                reconnect_delay_s=float(scanner_config.get("reconnect_delay_s", 2.0)),
            )
        self.controller = ReconnectingSerialTransport(
            controller_config["port"], int(controller_config.get("baudrate", 115200)),
            reconnect_delay_s=float(controller_config.get("reconnect_delay_s", 2.0)),
        )
        protocol_config = live_config.get("protocol", {})
        self.protocol = ProtocolEndpoint(
            heartbeat_interval_ms=float(protocol_config.get("heartbeat_interval_ms", 100)),
            offline_timeout_ms=float(protocol_config.get("offline_timeout_ms", 1000)),
            ack_timeout_ms=float(protocol_config.get("ack_timeout_ms", 1500)),
            max_retries=int(protocol_config.get("max_retries", 1)),
        )
        calibration_path = live_config.get("calibration")
        calibration = CameraCalibration.from_file(calibration_path) if calibration_path else None
        colors = tuple(live_config.get("colors", ["red", "green", "blue"]))
        color_config = load_config(str(root / live_config.get("color_config", "config/颜色追踪配置.json")))
        self.tracker = ColorTracker(colors, color_config, logger=DetectionLogger(str(session.directory / "vision.jsonl")),
                                    calibration=calibration)
        self.machine = MissionStateMachine(config.state_timeout_ms, config.max_recovery_attempts)
        self.action_logger = ActionLogger(session.directory / "actions.jsonl")
        self.telemetry = PiTelemetry()
        self.current_action: Optional[MissionAction] = None
        self.last_pending_id: Optional[int] = None
        self.video: Optional[cv2.VideoWriter] = None
        self.show_window = bool(live_config.get("display", False))
        self.stop_requested = False
        self.error_frame_index = 0

    def _now_ms(self) -> float:
        return time.monotonic() * 1000.0

    def _dispatch(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        now = self._now_ms()
        actions = self.machine.handle(MissionEvent(event, data), now)
        self.session.write("mission_event", {"event": event, "state": self.machine.state.value})
        self._execute(actions, now)

    def _execute(self, actions: List[MissionAction], now_ms: float) -> None:
        for action in actions:
            self.action_logger.write(action, self.machine.state, now_ms)
            self.session.write("mission_action", {"action": action.name, "data": action.data})
            message_type = ACTION_TYPES.get(action.name)
            if message_type is None:
                continue
            try:
                packet = self.protocol.send_command(message_type, json_payload(action.data), now_ms)
                self.controller.write(packet)
            except (RuntimeError, ValueError) as exc:
                self.session.write("controller_error", {"action": action.name, "error": str(exc)})
                if action.name != "safe_stop":
                    self._dispatch("communication_lost", {"error": str(exc)})
                continue
            self.current_action = action
            self.last_pending_id = id(self.protocol.pending)
            self.session.write("controller_sent", {"action": action.name, "message_type": message_type.name,
                                                     "sequence": self.protocol.pending.sequence if self.protocol.pending else None})

    def _poll_controller(self, now_ms: float) -> None:
        received = self.controller.read_available()
        if received:
            frames = self.protocol.receive(received, now_ms)
            self.session.write("controller_received", {"frames": [frame.message_type.name for frame in frames]})
        for packet in self.protocol.poll(now_ms):
            try:
                self.controller.write(packet)
            except RuntimeError as exc:
                self.session.write("controller_error", {"error": str(exc)})
        pending = self.protocol.pending
        if pending is None or id(pending) != self.last_pending_id:
            return
        if pending.completed and self.current_action is not None:
            action = self.current_action
            self.current_action = None
            self.last_pending_id = None
            event = DONE_EVENTS.get(action.name)
            if event:
                self._dispatch(event)
        elif pending.failed and self.current_action is not None:
            error = self.protocol.last_error or f"F407_FAIL_{pending.error_code}"
            self.current_action = None
            self.last_pending_id = None
            self._dispatch("action_failed", {"error": error})

    def _record_video(self, frame: Any) -> None:
        video_name = self.live_config.get("record_video")
        if not video_name:
            return
        if self.video is None:
            path = self.session.directory / video_name
            height, width = frame.shape[:2]
            self.video = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"),
                                         float(self.live_config["camera"].get("fps", 30)), (width, height))
        self.video.write(frame)

    def _archive_error_frame(self, frame: Any, result: Dict[str, Any]) -> None:
        """按固定间隔归档无目标帧，供离线调阈值而不淹没磁盘。"""
        if not self.live_config.get("archive_error_samples", True) or result["targets"]:
            return
        interval = int(self.live_config.get("error_frame_interval", 30))
        self.error_frame_index += 1
        if self.error_frame_index % max(interval, 1):
            return
        output = self.session.directory / "error_samples"
        output.mkdir(exist_ok=True)
        path = output / f"no_target_{self.error_frame_index:06d}.jpg"
        cv2.imwrite(str(path), frame)
        self.session.write("error_sample", {"reason": "no_target", "path": str(path.name)})

    def step(self) -> None:
        now = self._now_ms()
        self._poll_controller(now)
        ok, frame = self.camera.read()
        if not ok or frame is None:
            self.session.record_frame(0.0, 0, dropped=True)
            self.session.write("camera_error", self.camera.metrics())
            return
        annotated, result = self.tracker.track(frame)
        self.session.record_frame(float(result["elapsed_ms"]), len(result["targets"]),
                                  colors=[str(target["color"]) for target in result["targets"]])
        self.session.write("telemetry", {**self.telemetry.snapshot(), "camera": self.camera.metrics(),
                                          "vision_ms": result["elapsed_ms"], "targets": len(result["targets"])})
        scan = self._read_scan(frame)
        if scan is not None:
            self.session.record_scan(scan.scan_type.value, scan.raw_value, scan.valid, scan.source, scan.error)
            if self.machine.state == MissionState.S1_READ_TASK:
                self._dispatch("task_scanned", {"scan": scan})
        self._record_video(annotated)
        self._archive_error_frame(annotated, result)
        if self.show_window:
            cv2.imshow("物流机器人视觉实机", annotated)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                self.stop_requested = True
        tick_actions = self.machine.tick(self._now_ms())
        self._execute(tick_actions, self._now_ms())

    def _read_scan(self, frame: Any) -> Optional[Any]:
        """从所选扫码源获取一条结果；相机二维码仅在等待读取任务时解码。"""
        if self.machine.state != MissionState.S1_READ_TASK:
            return None
        if self.scanner_mode == "camera_qr":
            results = self.qr_reader.read(frame) if self.qr_reader is not None else []
            return results[0] if results else None
        return self.scanner.read() if self.scanner is not None else None

    def run(self, max_runtime_s: Optional[float] = None) -> MissionState:
        self._dispatch("start")
        started = time.monotonic()
        try:
            while not self.stop_requested and self.machine.state not in (MissionState.COMPLETE, MissionState.FAILED):
                if max_runtime_s is not None and time.monotonic() - started >= max_runtime_s:
                    self._dispatch("action_failed", {"error": "live_runtime_timeout"})
                    break
                self.step()
                time.sleep(0.002)
            return self.machine.state
        finally:
            self.close()

    def close(self) -> None:
        if self.video is not None:
            self.video.release()
        self.tracker.logger.close()
        self.camera.close()
        if self.scanner is not None:
            self.scanner.close()
        self.controller.close()
        if self.show_window:
            cv2.destroyAllWindows()
