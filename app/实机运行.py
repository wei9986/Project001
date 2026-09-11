# -*- coding: utf-8 -*-
"""实机运行循环：连接相机、扫码器、颜色识别、任务状态机和 F407。"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

import cv2

from app.实机设备 import LiveSensors, OpenCVCamera, PiTelemetry, ReconnectingSerialTransport, SerialScanner
from app.安全监控 import SafetyMonitor
from app.测试会话 import TestSession
from app.视觉应用 import ActionLogger, ApplicationConfig
from mission.任务状态机 import MissionAction, MissionEvent, MissionState, MissionStateMachine
from mission.扫码读取 import QRCodeReader, ScanType
from protocol.串口协议 import ACTION_TYPES, MessageType, ProtocolEndpoint, json_payload
from vision.相机标定 import CameraCalibration
from vision.颜色追踪 import ColorTracker, DetectionLogger, load_config
from vision.站点定位 import AprilTagStationDetector
from vision.精定位 import FineLocalizer, load_fine_localization_config
from vision.配置加载 import project_root, resolve_path


# 任务级动作 -> 命令映射统一来自 protocol/串口协议.py（唯一权威来源），
# 与协议仿真保持一致，避免仿真与实机行为漂移。
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
        self,
        config: ApplicationConfig,
        live_config: Dict[str, Any],
        session: TestSession,
        *,
        camera: Optional[Any] = None,
        controller: Optional[Any] = None,
        scanner: Optional[Any] = None,
    ) -> None:
        """构建实机运行器。

        camera/controller/scanner 可选注入（测试用 duck-typing 替身）；
        未注入时按 live_config 创建真实适配器。
        """
        root = project_root()
        camera_config = live_config["camera"]
        controller_config = live_config["controller"]
        scanner_config = live_config["scanner"]
        self.config = config
        self.live_config = live_config
        self.session = session
        self.camera = camera or OpenCVCamera(
            source=camera_config.get("source", 0), width=camera_config.get("width"),
            height=camera_config.get("height"), fps=camera_config.get("fps"),
            reconnect_delay_s=float(camera_config.get("reconnect_delay_s", 2.0)),
        )
        self.scanner_mode = scanner_config.get("mode", "serial")
        self.scanner: Optional[Any] = None
        self.qr_reader: Optional[QRCodeReader] = None
        if scanner is not None:
            self.scanner = scanner
            # 注入扫码器时强制走 serial 读口，避免误走 camera_qr。
            self.scanner_mode = "serial"
        elif self.scanner_mode == "camera_qr":
            self.qr_reader = QRCodeReader()
        else:
            self.scanner = SerialScanner(
                scanner_config["port"], int(scanner_config.get("baudrate", 9600)),
                ScanType(scanner_config.get("scan_type", "task_qr")),
                reconnect_delay_s=float(scanner_config.get("reconnect_delay_s", 2.0)),
                max_line_length=int(scanner_config.get("max_line_length", 512)),
            )
        self.controller = controller or ReconnectingSerialTransport(
            controller_config["port"], int(controller_config.get("baudrate", 115200)),
            reconnect_delay_s=float(controller_config.get("reconnect_delay_s", 2.0)),
            write_timeout_s=(
                float(controller_config["write_timeout_s"])
                if controller_config.get("write_timeout_s") is not None
                else 0.5
            ),
        )
        protocol_config = live_config.get("protocol", {})
        self.protocol = ProtocolEndpoint(
            heartbeat_interval_ms=float(protocol_config.get("heartbeat_interval_ms", 100)),
            offline_timeout_ms=float(protocol_config.get("offline_timeout_ms", 1000)),
            ack_timeout_ms=float(protocol_config.get("ack_timeout_ms", 5000)),
            done_timeout_ms=float(protocol_config.get("done_timeout_ms", 15000)),
            max_retries=int(protocol_config.get("max_retries", 1)),
        )
        self._heartbeat_offline_latched = False
        # 精定位：默认关闭；标定落盘后由配置打开。
        self.use_fine_localization = bool(live_config.get("use_fine_localization", False))
        self.fine_localization_before = set(
            live_config.get("fine_localization_before", ["load_first_material", "load_second_material"])
        )
        self._fine_localizer: Optional[FineLocalizer] = None
        self._fine_pending_action: Optional[MissionAction] = None
        calibration_path = resolve_path(live_config.get("calibration"), root)
        calibration = CameraCalibration.from_file(str(calibration_path)) if calibration_path else None
        colors = tuple(live_config.get("colors", ["red", "green", "blue"]))
        if not colors:
            raise ValueError("live_config.colors 不能为空")
        color_config_path = resolve_path(
            live_config.get("color_config", "config/颜色追踪配置.json"), root
        )
        color_config = load_config(str(color_config_path) if color_config_path else None)
        self.tracker = ColorTracker(colors, color_config, logger=DetectionLogger(str(session.directory / "vision.jsonl")),
                                    calibration=calibration)
        station_config_path = resolve_path(
            live_config.get("station_config", "config/站点标签.json"), root
        )
        camera_matrix = calibration.camera_matrix if calibration is not None else None
        distortion = calibration.dist_coeffs if calibration is not None else None
        self.station_detector = AprilTagStationDetector.from_config(
            path=station_config_path, camera_matrix=camera_matrix, distortion=distortion
        )
        fine_path = live_config.get("fine_localization", "config/精定位配置.json")
        if isinstance(fine_path, dict):
            self.fine_localization_config = load_fine_localization_config(None, overrides=fine_path)
        else:
            self.fine_localization_config = load_fine_localization_config(
                resolve_path(fine_path, root)
            )
        safety_config = live_config.get("safety", {})
        self.minimum_tof_mm = float(safety_config.get("minimum_tof_mm", 80.0))
        if self.minimum_tof_mm <= 0:
            raise ValueError("safety.minimum_tof_mm 必须为正数")
        self.safety_monitor = SafetyMonitor(minimum_tof_mm=self.minimum_tof_mm)
        self.sensors = LiveSensors.from_config(live_config.get("sensors"))
        # 传感器数据源注入点：未来由 F407 遥测 / GPIO / 视觉确认填充，当前为空表示未接入。
        self.sensor_provider: Optional[Any] = None
        self._log_safety_status()
        loop_config = live_config.get("loop", {})
        self.loop_sleep_s = float(loop_config.get("sleep_s", 0.002))
        self.machine = MissionStateMachine(config.state_timeout_ms, config.max_recovery_attempts)
        self.action_logger = ActionLogger(session.directory / "actions.jsonl")
        self.telemetry = PiTelemetry()
        self.current_action: Optional[MissionAction] = None
        self.last_pending_id: Optional[int] = None
        self.video: Optional[cv2.VideoWriter] = None
        self.show_window = bool(live_config.get("display", False))
        self.stop_requested = False
        self.error_frame_index = 0
        self._last_station_signature: Optional[tuple] = None
        self._station_log_counter = 0
        self.session.write(
            "vision_config_loaded",
            {
                "fine_localization": self.fine_localization_config,
                "minimum_tof_mm": self.minimum_tof_mm,
                "sensors": self.sensors.safety_snapshot(),
                "station_dictionary": self.station_detector.dictionary_name,
                "station_count": len(self.station_detector.station_mapping),
            },
        )

    def create_fine_localizer(
        self, *, target_id: Optional[int] = None, color: Optional[str] = None
    ) -> FineLocalizer:
        """用已加载的精定位配置构造实例，供任务层在需要时调用。"""
        return FineLocalizer.from_config(
            self.fine_localization_config, target_id=target_id, color=color
        )

    def _now_ms(self) -> float:
        return time.monotonic() * 1000.0

    def _dispatch(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        now = self._now_ms()
        actions = self.machine.handle(MissionEvent(event, data), now)
        self.session.write("mission_event", {"event": event, "state": self.machine.state.value})
        self._execute(actions, now)

    def _clear_blocking_pending(self, reason: str) -> None:
        """状态超时或写失败前清掉挂起命令，保证 recover/safe_stop 可下发。"""
        if self.protocol.fail_pending(reason):
            self.session.write("pending_failed", {"reason": reason})
            self.current_action = None
            self.last_pending_id = None

    def _send_controller_packet(self, action: MissionAction, message_type: MessageType, now_ms: float) -> bool:
        """编码并写入控制器；写失败时 fail_pending 后返回 False。"""
        try:
            packet = self.protocol.send_command(message_type, json_payload(action.data), now_ms)
        except (RuntimeError, ValueError, TypeError) as exc:
            self.session.write("controller_error", {"action": action.name, "error": str(exc)})
            if action.name != "safe_stop":
                self._dispatch("communication_lost", {"error": str(exc)})
            return False
        try:
            self.controller.write(packet)
        except RuntimeError as exc:
            # P1-1：写失败立刻失效 pending，否则 recover 发不出去。
            self.protocol.fail_pending(f"write_failed:{exc}")
            self.session.write("controller_error", {"action": action.name, "error": str(exc)})
            self.current_action = None
            self.last_pending_id = None
            if action.name != "safe_stop":
                self._dispatch("communication_lost", {"error": str(exc)})
            return False
        self.current_action = action
        self.last_pending_id = id(self.protocol.pending)
        self.session.write(
            "controller_sent",
            {
                "action": action.name,
                "message_type": message_type.name,
                "sequence": self.protocol.pending.sequence if self.protocol.pending else None,
            },
        )
        return True

    def _execute(self, actions: List[MissionAction], now_ms: float) -> None:
        for action in actions:
            self.action_logger.write(action, self.machine.state, now_ms)
            self.session.write("mission_action", {"action": action.name, "data": action.data})
            message_type = ACTION_TYPES.get(action.name)
            if message_type is None:
                continue
            # 状态超时产出的 recover：先清挂起命令，避免 P2-1 误判 communication_lost。
            if action.name in ("recover", "safe_stop"):
                self._clear_blocking_pending("state_timeout" if action.name == "recover" else "safe_stop")
                self._fine_localizer = None
                self._fine_pending_action = None
            elif action.name not in ("recover", "safe_stop"):
                decision = self.safety_monitor.check(action.name, self.sensors)
                if not decision.allowed:
                    self.session.write(
                        "safety_fault",
                        {
                            "action": action.name,
                            "fault": decision.fault,
                            "reason": decision.reason,
                            "sensors": self.sensors.safety_snapshot(),
                        },
                    )
                    self._dispatch("safety_fault", {"fault": decision.fault, "reason": decision.reason})
                    return
                # S3：抓取前可选精定位（需标定且开关打开）。
                if (
                    self.use_fine_localization
                    and action.name in self.fine_localization_before
                    and self.tracker.calibration is not None
                    and self.tracker.calibration.has_plane_mapping
                ):
                    self._fine_pending_action = action
                    material = action.data.get("material") or {}
                    self._fine_localizer = self.create_fine_localizer(
                        color=material.get("color") if isinstance(material, dict) else None
                    )
                    self._fine_localizer.reset(now_ms)
                    self.session.write("fine_localization_started", {"action": action.name})
                    continue
            if not self._send_controller_packet(action, message_type, now_ms):
                return

    def _check_heartbeat_offline(self, now_ms: float) -> None:
        """P1-7：曾在线后心跳超时则触发 communication_lost，同一离线窗口只触发一次。"""
        if self.protocol.last_received_ms is None:
            return
        online = self.protocol.is_online(now_ms)
        if online:
            self._heartbeat_offline_latched = False
            return
        if self._heartbeat_offline_latched:
            return
        self._heartbeat_offline_latched = True
        self._clear_blocking_pending("heartbeat_offline")
        self.session.write("heartbeat_offline", {"offline_timeout_ms": self.protocol.offline_timeout_ms})
        self._dispatch("communication_lost", {"error": "heartbeat_offline"})

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
                # 心跳/重发包写失败时，若有挂起命令则失效，便于后续 recover。
                if self.protocol.pending is not None and not self.protocol.pending.failed:
                    self.protocol.fail_pending(f"poll_write_failed:{exc}")
        self._check_heartbeat_offline(now_ms)
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
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*"mp4v"),
                float(self.live_config["camera"].get("fps", 30)),
                (width, height),
            )
            if not writer.isOpened():
                # P3-19：树莓派上 mp4v 可能静默失败，明确记日志后放弃录像。
                self.session.write("video_writer_error", {"path": str(path), "codec": "mp4v"})
                self.live_config["record_video"] = None
                return
            self.video = writer
        self.video.write(frame)

    def _archive_error_frame(self, frame: Any, result: Dict[str, Any]) -> None:
        """按固定间隔归档无目标帧，供离线调阈值而不淹没磁盘。"""
        if not self.live_config.get("archive_error_samples", True) or result["targets"]:
            return
        # interval 必须 >= 1；配置为 0 或负数时回退默认 30，避免“每帧落盘”淹没磁盘。
        interval = int(self.live_config.get("error_frame_interval", 30))
        if interval < 1:
            interval = 30
        self.error_frame_index += 1
        if self.error_frame_index % interval:
            return
        output = self.session.directory / "error_samples"
        output.mkdir(exist_ok=True)
        path = output / f"no_target_{self.error_frame_index:06d}.jpg"
        cv2.imwrite(str(path), frame)
        self.session.write("error_sample", {"reason": "no_target", "path": str(path.name)})

    def _log_station_detections(self, stations: List[Any]) -> None:
        """站点结果变化时立即记录；稳定时降采样，避免 events.jsonl 被每帧刷爆。"""
        signature = tuple((detection.tag_id, detection.station_id) for detection in stations)
        self._station_log_counter += 1
        interval = int(self.live_config.get("station_log_interval", 15))
        if signature == self._last_station_signature and self._station_log_counter < max(interval, 1):
            return
        self._last_station_signature = signature
        self._station_log_counter = 0
        self.session.write(
            "station_detections",
            {
                "count": len(stations),
                "stations": [
                    {
                        "tag_id": detection.tag_id,
                        "station_id": detection.station_id,
                        "center_px": list(detection.center_px),
                    }
                    for detection in stations
                ],
            },
        )

    def _log_safety_status(self) -> None:
        """记录安全门各检查项是否真正生效；未接入的传感器明确告警，不静默（P0-2 反向门）。"""
        tof_active = self.sensors.tof_distance_mm is not None
        grasp_active = self.sensors.grasp_confirmed is not None
        self.session.write("safety_status", {
            "tof_check_active": tof_active,
            "limit_check_active": True,  # 限位信号恒参与判定（默认无触发即安全）
            "grasp_check_active": grasp_active,
        })
        if not tof_active:
            self.session.write("safety_warning", {
                "sensor": "tof",
                "message": "ToF 未接入，距离保护未生效",
            })
        if not grasp_active:
            self.session.write("safety_warning", {
                "sensor": "grasp",
                "message": "抓取确认未接入，装配保护未生效",
            })

    def _refresh_sensors(self) -> None:
        """每帧从数据源刷新传感器读数；未配置数据源时不动作。

        真实数据源接入后，把读取逻辑放到 ``sensor_provider`` 中返回
        ``{tof_distance_mm / limit_triggered / grasp_confirmed}`` 即可，
        无需改动本循环与安全门。
        """
        if self.sensor_provider is not None:
            self.sensors.update(**self.sensor_provider())

    def step(self) -> None:
        now = self._now_ms()
        self._poll_controller(now)
        self._refresh_sensors()
        ok, frame = self.camera.read()
        if not ok or frame is None:
            self.session.record_frame(0.0, 0, dropped=True)
            self.session.write("camera_error", self.camera.metrics())
            # P1-4：丢帧仍推进状态超时/恢复，避免永久卡死。
            tick_actions = self.machine.tick(self._now_ms())
            self._execute(tick_actions, self._now_ms())
            return
        annotated = frame
        result: Dict[str, Any] = {"targets": [], "elapsed_ms": 0.0}
        stations: List[Any] = []
        try:
            annotated, result = self.tracker.track(frame)
            stations = self.station_detector.detect(frame)
        except Exception as exc:  # noqa: BLE001 - 单帧视觉异常不得中断整场比赛循环
            self.session.write("vision_error", {"error": str(exc)})
            self.session.record_frame(0.0, 0, dropped=True)
            # P2-2 缓解：视觉异常仍 tick。
            tick_actions = self.machine.tick(self._now_ms())
            self._execute(tick_actions, self._now_ms())
            return
        self.session.record_frame(float(result["elapsed_ms"]), len(result["targets"]),
                                  colors=[str(target["color"]) for target in result["targets"]])
        self._log_station_detections(stations)
        self.session.write(
            "telemetry",
            {
                **self.telemetry.snapshot(),
                "camera": self.camera.metrics(),
                "vision_ms": result["elapsed_ms"],
                "targets": len(result["targets"]),
                "stations": len(stations),
                "minimum_tof_mm": self.minimum_tof_mm,
                "sensors": self.sensors.safety_snapshot(),
            },
        )
        scan = self._read_scan(frame)
        if scan is not None:
            self.session.record_scan(scan.scan_type.value, scan.raw_value, scan.valid, scan.source, scan.error)
            if self.machine.state == MissionState.S1_READ_TASK:
                self._dispatch("task_scanned", {"scan": scan})
        self._advance_fine_localization(result, now)
        self._record_video(annotated)
        self._archive_error_frame(annotated, result)
        if self.show_window:
            cv2.imshow("物流机器人视觉实机", annotated)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                self.stop_requested = True
        tick_actions = self.machine.tick(self._now_ms())
        self._execute(tick_actions, self._now_ms())

    def _advance_fine_localization(self, vision_result: Dict[str, Any], now_ms: float) -> None:
        """抓取前精定位：STABLE 后下发原动作；超时/丢失则 action_failed。"""
        if self._fine_localizer is None or self._fine_pending_action is None:
            return
        from vision.精定位 import LocalizationStatus

        localization = self._fine_localizer.update(vision_result, now_ms)
        if localization.status == LocalizationStatus.STABLE:
            action = self._fine_pending_action
            data = dict(action.data)
            data["coordinate_mm"] = list(localization.coordinate_mm) if localization.coordinate_mm else None
            enriched = MissionAction(action.name, data)
            self._fine_localizer = None
            self._fine_pending_action = None
            message_type = ACTION_TYPES.get(enriched.name)
            if message_type is not None:
                self.session.write("fine_localization_stable", {"action": enriched.name, "coordinate_mm": data["coordinate_mm"]})
                self._send_controller_packet(enriched, message_type, now_ms)
            return
        if localization.status == LocalizationStatus.JUMP_REJECTED:
            # P1-9：跳变非致命，继续采样。
            self.session.write("fine_localization_progress", localization.to_dict())
            return
        if localization.status in (
            LocalizationStatus.TIMEOUT,
            LocalizationStatus.LOST,
            LocalizationStatus.NO_COORDINATE,
        ):
            reason = localization.reason or localization.status.value
            self._fine_localizer = None
            self._fine_pending_action = None
            self.session.write("fine_localization_failed", {"reason": reason})
            self._dispatch("action_failed", {"error": f"fine_localization:{reason}"})
            return
        self.session.write("fine_localization_progress", localization.to_dict())

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
                time.sleep(self.loop_sleep_s)
            return self.machine.state
        finally:
            self.close()

    def close(self) -> None:
        if self.video is not None:
            self.video.release()
        self.tracker.logger.close()
        self.action_logger.close()
        close_camera = getattr(self.camera, "close", None)
        if callable(close_camera):
            close_camera()
        if self.scanner is not None:
            close_scanner = getattr(self.scanner, "close", None)
            if callable(close_scanner):
                close_scanner()
        close_controller = getattr(self.controller, "close", None)
        if callable(close_controller):
            close_controller()
        if self.show_window:
            cv2.destroyAllWindows()
