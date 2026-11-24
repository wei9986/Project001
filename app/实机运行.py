# -*- coding: utf-8 -*-
"""实机运行循环：连接相机、扫码器、颜色识别、任务状态机和 F407。"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import cv2

from app.实机设备 import DisplayPort, LiveSensors, OpenCVCamera, PiTelemetry, ReconnectingSerialTransport, SerialScanner
from app.安全监控 import SafetyMonitor
from app.测试会话 import TestSession
from app.任务牌 import TaskBoard
from app.视觉应用 import ActionLogger, ApplicationConfig
from app.启动锁存 import ClockStartHandoff, LatchState, RisingEdges, StartLatch
from app.无线日志 import WirelessLog
from mission.比赛时钟 import STOP_REASON, MatchClock
from mission.传感器门禁 import reports_stale, sources_ready
from mission.到站 import arm_allowed, arrived, ring_aligned
from mission.转盘 import grab_window_open
from mission.车道 import (
    LaneMap,
    body_left_lane,
    load_lane,
    qr_search_points,
    route_to_point,
    route_to_station,
    segment_clear_of,
)
from mission.任务状态机 import MissionAction, MissionEvent, MissionState, MissionStateMachine
from mission.扫码读取 import QRCodeReader, QrSearch, ScanType, validate_scan
from protocol.串口协议 import ACTION_TYPES, MessageType, ProtocolEndpoint, ZoneId, pack_action
from vision.障碍物 import assess, detect_cylinders
from vision.相机标定 import CameraCalibration
from vision.颜色追踪 import ColorTracker, DetectionLogger, load_config
from vision.站点定位 import AprilTagStationDetector
from vision.精定位 import FineLocalizer, load_fine_localization_config
from vision.配置加载 import project_root, resolve_path


# 任务级动作 -> 命令映射统一来自 protocol/串口协议.py（唯一权威来源），
# 与协议仿真保持一致，避免仿真与实机行为漂移。
_ZONE_STATION = {
    int(ZoneId.QR_VIEW): "qr_view",
    int(ZoneId.SOURCE): "source",
    int(ZoneId.COARSE): "coarse",
    int(ZoneId.STAGING): "staging",
    int(ZoneId.HOME_A): "home_a",
    int(ZoneId.HOME_B): "home_b",
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
        self,
        config: ApplicationConfig,
        live_config: Dict[str, Any],
        session: TestSession,
        *,
        camera: Optional[Any] = None,
        controller: Optional[Any] = None,
        scanner: Optional[Any] = None,
        gpio_reader: Optional[Any] = None,
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
            live_config.get(
                "fine_localization_before",
                ["PLACE_FROM_SLOT", "PICK_TO_SLOT"],
            )
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
        self.slot_mask = 0
        self.gripper: Optional[int] = None
        self.speed_mm_s: Optional[int] = None
        self._log_safety_status()
        loop_config = live_config.get("loop", {})
        self.loop_sleep_s = float(loop_config.get("sleep_s", 0.002))
        self.machine = MissionStateMachine(config.state_timeout_ms, config.max_recovery_attempts)
        self._apply_competition_options(live_config)
        self._status_serial = 0
        self._status_used = 0
        self.match_clock, self.clock_handoff = self._build_match_clock(live_config.get("match_clock"))
        self.waiting_turntable = False
        self.round_end_reason: Optional[str] = None
        self.latch = self._build_latch(live_config)
        self._start_edges: List[str] = []
        start_inputs = live_config.get("start_inputs") if isinstance(live_config.get("start_inputs"), dict) else {}
        self.start_inputs = start_inputs
        self.gpio_reader = gpio_reader
        self._button_level = {"clock": False, "robot": False}
        self._edges = RisingEdges(start_inputs.get("debounce_ms"))
        board_config = live_config.get("task_board") or {}
        self.display_required = bool(board_config.get("display_required", False))
        self.task_board = TaskBoard()
        self.display = DisplayPort.from_config(board_config)
        self._board_dirty = False
        self._qr_search = None
        self._qr_pose_sent = -1
        self.machine.require_display = self.display_required
        self.safety_monitor.require_arm_range = self.latch.competition
        if self.latch.competition and not sources_ready(
            live_config.get("sensor_source"), live_config.get("speed_source")
        ):
            raise ValueError("比赛模式缺少传感器来源或 SPEED 帧速度来源")
        wireless = live_config.get("wireless") or {}
        baud = wireless.get("baudrate")
        self.wireless = WirelessLog(
            downlink=bool(wireless.get("downlink", True)) and not self.latch.competition,
            address=None if wireless.get("address") in (None, "") else str(wireless.get("address")),
            baudrate=baud if isinstance(baud, int) and baud > 0 else None,
        )
        if self.latch.competition and not self.display_required:
            raise ValueError("比赛模式必须打开 task_board.display_required")
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

    def _apply_competition_options(self, live_config: Dict[str, Any]) -> None:
        """出发策略和终点只读配置。静止门槛为空时，到家也不假装已经停稳。"""
        policy = live_config.get("depart_policy") or "full_batch"
        station = live_config.get("home_station")
        if live_config.get("mode") == "competition":
            if station not in ("home_a", "home_b"):
                raise ValueError("home_station 必须写成 home_a 或 home_b")
        else:
            station = station or "home_a"
            if station not in ("home_a", "home_b"):
                raise ValueError("home_station 只能是 home_a 或 home_b")
        clock = live_config.get("match_clock") or {}
        still = clock.get("still_speed_mm_s")
        self.machine.depart_policy = str(policy)
        self.machine.home_zone = int(ZoneId.HOME_B if station == "home_b" else ZoneId.HOME_A)
        self.machine.still_speed_mm_s = None if still is None else float(still)
        threshold = live_config.get("arrival_threshold_mm")
        self.arrival_threshold_mm = None if threshold is None else float(threshold)
        obstacle = live_config.get("obstacle") or {}
        self.obstacle_min_confidence = obstacle.get("min_confidence")
        self.obstacle_width_tolerance_mm = obstacle.get("width_tolerance_mm")
        self.obstacle_clearance_mm = obstacle.get("clearance_mm")
        self._obstacle_can_plan = False
        self.obstacle_sights: List[Any] = []
        self._obstacle_previous: tuple = ()
        self._waypoint_rest: List[MissionAction] = []
        self._waypoint_final: Optional[MissionAction] = None
        self.odometry_xy = None
        self.odometry_yaw_deg = None
        self._position_seen_ms = None
        self._departed_homes = False
        self.turntable_stable = False
        self._turntable_stable_since_ms = None
        turntable_path = project_root() / "config" / "转盘.json"
        self.turntable = json.loads(turntable_path.read_text(encoding="utf-8")) if turntable_path.is_file() else {}
        self._tag_station: Optional[str] = None
        self._status_seen_ms: Optional[float] = None
        self._speed_seen_ms: Optional[float] = None
        self._running_since_ms: Optional[float] = None
        lane_path = resolve_path(live_config.get("lane_config", "config/车道.json"), project_root())
        self.lane = load_lane(lane_path) if lane_path is not None and lane_path.is_file() else LaneMap(False, (), {})

    def _dispatch_competition_done(self, action: MissionAction) -> None:
        """行驶完成不看槽位。抓放必须先有一条还没用过的 STATUS。"""
        if action.name in ("GOTO", "HOME"):
            if self.latch.competition and not self._arm_extension_allowed(action):
                self.session.write("arrival_hold", {"action": action.name, "zone": action.data.get("zone")})
                return
            self._dispatch("travel_done", {"speed_mm_s": self.speed_mm_s, "zone": action.data.get("zone")})
            return
        fresh = self._status_serial != self._status_used and self.gripper is not None
        if fresh:
            self._status_used = self._status_serial
            self._dispatch("action_done", {"slot_mask": self.slot_mask, "gripper": self.gripper})
            return
        self._dispatch("action_done", {"slot_mask": None, "gripper": None})

    @staticmethod
    def _build_match_clock(data: Optional[Dict[str, Any]]) -> tuple:
        """静止门槛或启动时限为空时不编造秒数，比赛时钟先不启用。"""
        values = data or {}
        still = values.get("still_speed_mm_s")
        window = values.get("start_window_s")
        if still is None:
            return None, None
        clock = MatchClock(still, None if window is None else window)
        return clock, ClockStartHandoff(clock)

    def _build_latch(self, live_config: Dict[str, Any]) -> StartLatch:
        match_clock = live_config.get("match_clock") or {}
        if "start_window_s" in live_config:
            window = live_config.get("start_window_s")
        else:
            window = match_clock.get("start_window_s")
        competition = live_config.get("mode") == "competition"
        return StartLatch(
            competition=competition,
            start_window_s=None if window is None else float(window),
            clock=self.match_clock,
        )

    def inject_start_edge(self, kind: str) -> None:
        """只接受发令、车上按钮、调试、就位和下一轮。键盘与无线不在这里。"""
        if kind not in {"clock", "robot", "debug", "stage", "next_round"}:
            raise ValueError(f"不能作为一键启动: {kind}")
        self._start_edges.append(kind)

    def _poll_start_edges(self) -> None:
        now = self._now_ms()
        while self._start_edges:
            kind = self._start_edges.pop(0)
            if kind == "debug":
                self.latch.debug_press()
            elif kind == "stage":
                self.latch.stage()
            elif kind == "clock":
                self.latch.clock_start(now)
            elif kind == "robot":
                if self.latch.robot_start(now):
                    self._arm_competition_read()
            elif kind == "next_round":
                self.begin_next_round()
        missed = self.latch.tick(now)
        if missed and self.round_end_reason is None:
            self.round_end_reason = missed
            self.machine.end_round(missed)
            self.task_board.set_round_end(missed)
            self.latch.end()

    def _arm_competition_read(self) -> None:
        """锁存已经进入运行时，从 S0 直接读码。调试按钮到不了这里。"""
        if not self.latch.competition or self.latch.state != LatchState.RUNNING:
            return
        if self.machine.state != MissionState.S0_INIT:
            return
        now = self._now_ms()
        self._execute(self.machine.begin_task_read(now), now)

    def begin_next_round(self) -> bool:
        """只在本轮已经结束后清状态。上电不会走到这里。"""
        if not self.latch.next_round():
            return False
        self.machine.reset()
        self.round_end_reason = None
        self.task_board = TaskBoard()
        if self.match_clock is not None:
            self.match_clock.clear_round()
        self._qr_search = None
        self._qr_pose_sent = -1
        self._departed_homes = False
        self._obstacle_previous = ()
        self._obstacle_can_plan = False
        self._waypoint_rest = []
        self._waypoint_final = None
        return True

    def _sample_start_inputs(self, now_ms: float) -> None:
        """GPIO 或按键上报先变成上升沿，再进入锁存。不改锁存的状态转移。"""
        for kind in ("clock", "robot"):
            spec = self.start_inputs.get(kind)
            if not isinstance(spec, dict):
                continue
            level = None
            if spec.get("source") == "gpio" and self.gpio_reader is not None and isinstance(spec.get("pin"), int):
                level = bool(self.gpio_reader(int(spec["pin"])))
            elif spec.get("source") == "button_frame":
                level = bool(self._button_level.get(kind))
            if level is None:
                continue
            if self._edges.update(kind, level, now_ms):
                self.inject_start_edge(kind)

    def note_clock_start(self, clock_start_ms: float) -> None:
        """裁判发令。时刻交给比赛时钟，不从一键启动重算。"""
        if self.clock_handoff is None:
            raise RuntimeError("静止速度门槛未配置，比赛时钟不能发令")
        self.clock_handoff.clock_start(clock_start_ms)

    def note_robot_start(self, now_ms: float) -> None:
        """车上的一键启动。晚按只减少剩余时间。"""
        if self.clock_handoff is None:
            raise RuntimeError("静止速度门槛未配置，比赛时钟不能启动")
        self.clock_handoff.robot_start(now_ms)

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
        if self.latch.competition and event in {"action_failed", "communication_lost", "safety_fault"}:
            self._stop_round(event)
            return
        now = self._now_ms()
        actions = self.machine.handle(MissionEvent(event, data), now)
        if self.latch.competition and self.round_end_reason is None and self.machine.round_end_reason:
            self._stop_round(self.machine.round_end_reason)
            return
        flow = getattr(self.machine, "flow", None)
        if flow is not None:
            for name in flow.take_events():
                before = (self.task_board.grab_count, self.task_board.place_count)
                self.task_board.apply_event(name)
                self._sync_task_board(before)
        if self.machine.state == MissionState.COMPLETE:
            self.task_board.mark_complete()
        if event == "task_scanned":
            self._bind_round_colors()
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
        # F-04：安全停止是最高优先级指令，必须能抢占任何悬挂命令。
        # 协议同一时刻只允许一条未完成命令，若此时 pending 阻塞（例如 F407
        # 已 ACK 但迟迟不 DONE），safe_stop 会被 send_command 拒绝，导致
        # F407 收不到任何停止指令。这里先清障再发。
        if action.name in ("safe_stop", "STOP"):
            self._clear_blocking_pending("safe_stop_priority")
        try:
            packet = self._send_packed_action(action, now_ms)
        except (RuntimeError, ValueError, TypeError) as exc:
            self.session.write("controller_error", {"action": action.name, "error": str(exc)})
            if action.name == "safe_stop":
                # 比赛复盘关键证据：安全停止未能下发必须显著记录，不得静默。
                self.session.write("safe_stop_failed", {"reason": str(exc), "stage": "send_command"})
            else:
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
            if action.name == "safe_stop":
                self.session.write("safe_stop_failed", {"reason": str(exc), "stage": "write"})
            else:
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

    @staticmethod
    def _wire_action_name(name: str) -> str:
        """旧状态机动作名只在发送前换成短命令，不进入 ACTION_TYPES。

        第 11 项改完状态机后，这里只剩同名透传。
        """
        legacy = {
            "self_test": "PING",
            "load_first_material": "PICK_TO_SLOT",
            "load_second_material": "PICK_TO_SLOT",
            "place_ring": "PLACE_FROM_SLOT",
            "assemble_material": "STACK_FROM_SLOT",
            "return_home": "HOME",
            "safe_stop": "STOP",
            "recover": "RECOVER",
        }
        return legacy.get(name, name)

    @staticmethod
    def _protocol_int(value: object) -> int:
        """协议字段只收整数。旧动作里的颜色名字在编号协议里记 0，不把字符串塞进帧。"""
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    def _wire_message_type(self, name: str) -> Optional[MessageType]:
        wire = self._wire_action_name(name)
        if wire == "RECOVER":
            return MessageType.RECOVER
        return ACTION_TYPES.get(wire)

    def _send_packed_action(self, action: MissionAction, now_ms: float) -> bytes:
        """动作只下发 pack_action 的 5 字节编号，不再把字典塞进 json_payload。"""
        data = action.data or {}
        if self._wire_action_name(action.name) == "RECOVER":
            return self.protocol.send_command(MessageType.RECOVER, pack_action(0, 0, 0, 0, 0), now_ms)
        packet = self.protocol.send_action(
            self._wire_action_name(action.name),
            now_ms,
            slot=self._protocol_int(data.get("slot", 0)),
            color=self._protocol_int(data.get("color", 0)),
            zone=self._protocol_int(data.get("zone", 0)),
            ring=self._protocol_int(data.get("ring", 0)),
            batch=self._protocol_int(data.get("batch", 0)),
            reason=self._protocol_int(data.get("reason_code", 0)),
            x_mm=self._protocol_int(data.get("x_mm", 0)),
            y_mm=self._protocol_int(data.get("y_mm", 0)),
            point_index=self._protocol_int(data.get("point_index", 0)),
        )
        if packet is None:
            raise ValueError(f"未知动作命令: {action.name}")
        return packet

    def _execute(self, actions: List[MissionAction], now_ms: float) -> None:
        for action in actions:
            self.action_logger.write(action, self.machine.state, now_ms)
            self.session.write("mission_action", {"action": action.name, "data": action.data})
            if action.name == "show_task_code":
                self._publish_task_board(str(action.data.get("task_code", "")))
                continue
            message_type = self._wire_message_type(action.name)
            if message_type is None:
                continue
            if self.latch.competition and action.name in ("GOTO", "HOME") and not self._route_allowed(action):
                self.session.write("goto_withheld", {"action": action.name, "zone": action.data.get("zone")})
                return
            if self.latch.competition and action.name in ("PICK_TO_SLOT", "PLACE_FROM_SLOT", "STACK_FROM_SLOT"):
                if self.sensors.tof_distance_mm is None:
                    self._stop_round("sensor_offline")
                    return
            # 状态超时产出的 recover：先清挂起命令，避免 P2-1 误判 communication_lost。
            if self.latch.competition and action.name == "recover":
                self._stop_round("recover")
                return
            if self.latch.competition and action.name == "STOP" and self.round_end_reason is None:
                self._stop_round(str(action.data.get("reason") or action.data.get("error") or "stop"))
                return
            if action.name in ("recover", "safe_stop", "STOP"):
                self._clear_blocking_pending("state_timeout" if action.name == "recover" else "safe_stop")
                self._fine_localizer = None
                self._fine_pending_action = None
                if action.name in ("recover",):
                    # F-09：恢复事件结构化记录，供复盘识别频繁抖动。
                    # 配额放宽（F-01）后，累积恢复次数是发现真问题的关键信号。
                    self.session.record_recovery(
                        attempt=int(action.data.get("attempt", 0)),
                        resume_state=self.machine.resume_state.value,
                        reason=str(action.data.get("reason", "")),
                    )
            elif action.name not in ("recover", "safe_stop", "STOP"):
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
                if self.use_fine_localization and self._wants_fine_localization(action):
                    calibration = self.tracker.calibration
                    if calibration is None or not calibration.has_plane_mapping:
                        if self.latch.competition:
                            self._stop_round("calibration_missing")
                        return
                    color_name = self._action_color_name(action)
                    if not color_name:
                        if self.latch.competition:
                            self._stop_round("color_unmeasured")
                        return
                    self._fine_pending_action = action
                    self._fine_localizer = self.create_fine_localizer(color=color_name)
                    self._fine_localizer.reset(now_ms)
                    self.session.write("fine_localization_started", {"action": action.name, "color": color_name})
                    continue
            if self.latch.competition and action.name in ("GOTO", "HOME"):
                route = self._station_route(action)
                if route is not None and len(route) >= 3:
                    self._waypoint_rest = [
                        MissionAction("WAYPOINT", {
                            "x_mm": int(round(point[0])),
                            "y_mm": int(round(point[1])),
                            "point_index": index,
                        })
                        for index, point in enumerate(route[1:-1])
                    ]
                    self._waypoint_final = action
                    action = self._waypoint_rest.pop(0)
                    message_type = self._wire_message_type(action.name)
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
            # 主动上报只刷新传感器和速度，不调用 send_command，也不占用动作序号。
            self._apply_reports()
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
            if action.name == "WAYPOINT":
                self._continue_waypoints(now_ms)
                return
            if self.machine.competition_mode and action.name in {
                "PICK_TO_SLOT", "PLACE_FROM_SLOT", "STACK_FROM_SLOT", "GOTO", "HOME",
            }:
                self._dispatch_competition_done(action)
            else:
                event = DONE_EVENTS.get(action.name)
                if event:
                    self._dispatch(event)
        elif pending.failed and self.current_action is not None:
            error = self.protocol.last_error or f"F407_FAIL_{pending.error_code}"
            self.current_action = None
            self.last_pending_id = None
            self._dispatch("action_failed", {"error": error})

    def _apply_reports(self) -> None:
        """把最近一次 STATUS / SPEED 写到运行时。没有新上报时不改已有读数。"""
        if not self.protocol.reports and self.protocol.last_status is None and self.protocol.last_speed_mm_s is None:
            return
        reports = self.protocol.take_reports()
        saw_status = any(frame.message_type == MessageType.STATUS for frame in reports)
        saw_speed = any(frame.message_type == MessageType.SPEED for frame in reports)
        if saw_status and self.protocol.last_status is not None:
            self._status_serial += 1
            self._status_seen_ms = time.monotonic() * 1000.0
            status = self.protocol.last_status
            self.slot_mask = int(status["slot_mask"])
            self.gripper = int(status["gripper"])
            self.sensors.update(
                tof_distance_mm=int(status["tof_mm"]),
                limit_triggered=bool(status["limit"]),
            )
        if saw_speed and self.protocol.last_speed_mm_s is not None:
            self.speed_mm_s = int(self.protocol.last_speed_mm_s)
            self._speed_seen_ms = time.monotonic() * 1000.0
        if any(frame.message_type == MessageType.POSITION for frame in reports) and self.protocol.last_position is not None:
            position = self.protocol.last_position
            self.odometry_xy = (int(position["x_mm"]), int(position["y_mm"]))
            self.odometry_yaw_deg = float(position["yaw_deg"])
            self._position_seen_ms = time.monotonic() * 1000.0
        if any(frame.message_type == MessageType.BUTTON for frame in reports) and self.protocol.last_buttons is not None:
            self._button_level = {
                "clock": bool(self.protocol.last_buttons["clock"]),
                "robot": bool(self.protocol.last_buttons["robot"]),
            }

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

    def step(self) -> None:
        now = self._now_ms()
        self._sample_start_inputs(now)
        self._poll_start_edges()
        self._retry_task_board()
        self._poll_controller(now)
        position, _yaw = self._fresh_pose(now)
        stop, departed = body_left_lane(position, self.lane, self._departed_homes)
        self._departed_homes = departed
        if self.latch.competition and stop:
            self._stop_round("left_lane")
        ok, frame = self.camera.read()
        if not ok or frame is None:
            self.session.record_frame(0.0, 0, dropped=True)
            self.session.write("camera_error", self.camera.metrics())
            # P1-4：丢帧仍推进状态超时/恢复，避免永久卡死。
            self._tick_mission(self._now_ms())
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
            self._tick_mission(self._now_ms())
            return
        self.session.record_frame(float(result["elapsed_ms"]), len(result["targets"]),
                                  colors=[str(target["color"]) for target in result["targets"]])
        self._log_station_detections(stations)
        if self.latch.state == LatchState.RUNNING:
            self.wireless.downlink = False
        flow = getattr(self.machine, "flow", None)
        self.waiting_turntable = bool(flow is not None and flow.awaiting_turntable)
        self._offer_turntable(now)
        self._note_tag_station(stations)
        self._offer_seen_color(result)
        if self.latch.competition:
            self.obstacle_sights = detect_cylinders(frame, getattr(self.tracker, "calibration", None), self.lane)
        self._review_obstacles()
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
        self._tick_mission(self._now_ms())

    def _bind_round_colors(self) -> None:
        """静态颜色列表只用于读码前预览。读码成功后改成本轮三种颜色。"""
        plan = self.machine.plan
        if plan is None:
            return
        names: List[str] = []
        for record in plan.records:
            color = record.instruction.color
            if color not in names:
                names.append(color)
        def configured(name: str) -> dict:
            return (self.tracker.config.get("colors") or {}).get(name) or {}

        def venue_ready(name: str) -> bool:
            color = configured(name)
            return color.get("venue_sampled") is True and bool(color.get("ranges"))

        unmeasured = [name for name in names if not venue_ready(name)]
        if self.latch.competition and unmeasured:
            self._stop_round("color_unmeasured")
            return
        known = [name for name in names if configured(name).get("ranges")]
        if known:
            self.tracker.use_colors(known)
        if self.latch.competition:
            self.tracker.require_measured_shape = True

    def _zone_station(self, action: MissionAction) -> Optional[str]:
        zone = (action.data or {}).get("zone")
        if zone is None:
            return None
        return _ZONE_STATION.get(int(zone))

    def _route_allowed(self, action: MissionAction) -> bool:
        if self._obstacle_previous and not self._obstacle_can_plan:
            return False
        if (
            self._qr_search is not None
            and action.name == "GOTO"
            and int((action.data or {}).get("zone") or 0) == int(ZoneId.QR_VIEW)
        ):
            path = route_to_point(self.lane, self.odometry_xy, self._qr_search.current())
            if path is None:
                return False
            return segment_clear_of(path[0], path[1], self._obstacle_previous, self.obstacle_clearance_mm)
        station = self._zone_station(action)
        if station is None:
            return False
        obstacles = self._obstacle_previous if self._obstacle_can_plan else ()
        return route_to_station(
            self.lane,
            self.odometry_xy,
            station,
            obstacles,
            self.obstacle_clearance_mm,
        ) is not None

    def _arm_extension_allowed(self, action: MissionAction) -> bool:
        station = self._zone_station(action)
        dock = self.lane.station(station) if station else None
        if self.latch.competition:
            position, yaw = self._fresh_pose(self._now_ms())
            odom_ok = arrived(
                position,
                dock,
                self.lane.arrival_threshold_mm if self.lane.arrival_threshold_mm is not None else self.arrival_threshold_mm,
                self.speed_mm_s,
                self.machine.still_speed_mm_s,
                yaw,
                self.lane.heading_deg(station) if station else None,
                self.lane.heading_tolerance_deg,
                require_heading=True,
            )
        else:
            odom_ok = arrived(
                self.odometry_xy,
                dock,
                self.arrival_threshold_mm,
                self.speed_mm_s,
                self.machine.still_speed_mm_s,
            )
        allowed, conflict = arm_allowed(odom_ok, self._tag_station, station)
        if conflict:
            self._stop_round(conflict)
        return allowed

    def _action_color_name(self, action: MissionAction) -> Optional[str]:
        names = {1: "red", 2: "yellow", 3: "blue", 4: "green", 5: "black", 6: "light_blue"}
        data = action.data or {}
        material = data.get("material")
        if isinstance(material, dict) and isinstance(material.get("color"), str) and material.get("color"):
            return material["color"]
        color = data.get("color")
        if isinstance(color, str) and color:
            return color
        if isinstance(color, int) and not isinstance(color, bool):
            return names.get(color)
        return None

    def _fresh_pose(self, now_ms: float):
        if self.odometry_xy is None or self._position_seen_ms is None:
            return None, None
        if now_ms - self._position_seen_ms > self.protocol.offline_timeout_ms:
            return None, None
        return self.odometry_xy, self.odometry_yaw_deg

    def _ring_ready(self, action: MissionAction, coordinate) -> bool:
        zone = int((action.data or {}).get("zone") or 0)
        if action.name == "PLACE_FROM_SLOT" and zone == int(ZoneId.COARSE):
            area = "coarse"
        elif action.name == "PLACE_FROM_SLOT" and zone == int(ZoneId.STAGING):
            area = "staging"
        elif action.name == "PICK_TO_SLOT" and zone == int(ZoneId.COARSE):
            area = "coarse"
        else:
            return True
        center = self.lane.ring_center(area, (action.data or {}).get("ring"))
        point = tuple(coordinate) if coordinate is not None else None
        return ring_aligned(point, center, self.lane.ring_align_tolerance_mm)

    def _offer_turntable(self, now_ms: float) -> None:
        flow = getattr(self.machine, "flow", None)
        if flow is None or not flow.awaiting_turntable or self.round_end_reason is not None:
            return
        if not self.turntable_stable:
            self._turntable_stable_since_ms = None
            elapsed = None
        else:
            if self._turntable_stable_since_ms is None:
                self._turntable_stable_since_ms = now_ms
            elapsed = now_ms - self._turntable_stable_since_ms
        window = grab_window_open(
            self.turntable.get("min_dwell_ms"),
            self.turntable.get("recognition_ms"),
            self.turntable.get("action_ms"),
            self.turntable.get("margin_ms"),
            elapsed,
        )
        self._dispatch("turntable_ready", {"stable": True if self.turntable_stable else False, "window_open": window})

    def _wants_fine_localization(self, action: MissionAction) -> bool:
        """平面放置和粗加工回取走精定位。原料区抓取不在这里等毫米坐标。"""
        if action.name == "PLACE_FROM_SLOT":
            return True
        if action.name == "PICK_TO_SLOT" and int((action.data or {}).get("zone") or 0) == int(ZoneId.COARSE):
            return True
        return action.name in self.fine_localization_before and action.name not in ("PICK_TO_SLOT", "PLACE_FROM_SLOT")

    def _station_route(self, action: MissionAction):
        """二维码搜索步不拆航点。其它行驶返回车道折线，直线只有两个点。"""
        if (
            self._qr_search is not None
            and action.name == "GOTO"
            and int((action.data or {}).get("zone") or 0) == int(ZoneId.QR_VIEW)
        ):
            return None
        station = self._zone_station(action)
        if station is None:
            return None
        obstacles = self._obstacle_previous if self._obstacle_can_plan else ()
        return route_to_station(
            self.lane,
            self.odometry_xy,
            station,
            obstacles,
            self.obstacle_clearance_mm,
        )

    def _continue_waypoints(self, now_ms: float) -> None:
        """中间点完成后发下一点。最后才发原来的区域命令。"""
        if self._waypoint_rest:
            action = self._waypoint_rest.pop(0)
        elif self._waypoint_final is not None:
            action = self._waypoint_final
            self._waypoint_final = None
        else:
            return
        message_type = self._wire_message_type(action.name)
        if message_type is None:
            self._dispatch("action_failed", {"error": "waypoint_missing"})
            return
        self._send_controller_packet(action, message_type, now_ms)

    def _stop_round(self, reason: str) -> None:
        if self.round_end_reason is not None:
            return
        self._waypoint_rest = []
        self._waypoint_final = None
        self.round_end_reason = reason
        self.machine.end_round(reason)
        self.latch.end()
        self.session.write("round_ended", {"reason": reason})
        self._execute([MissionAction("STOP", {"reason_code": 4})], self._now_ms())

    def _reports_are_offline(self, now_ms: float) -> bool:
        """比赛已经启动后，STATUS 或 SPEED 超过离线时间就停车。调试模式不套这条。"""
        if not self.latch.competition or self.latch.state != LatchState.RUNNING or self.round_end_reason is not None:
            return False
        if self._running_since_ms is None:
            self._running_since_ms = now_ms
        limit = self.protocol.offline_timeout_ms
        status_at = self._status_seen_ms
        speed_at = self._speed_seen_ms
        return reports_stale(now_ms, status_at, self._running_since_ms, limit) or reports_stale(
            now_ms, speed_at, self._running_since_ms, limit
        )

    def _note_tag_station(self, stations: List[Any]) -> None:
        """标签只作对照，不单独放行伸臂。"""
        self._tag_station = str(stations[0].station_id) if stations else None

    def _offer_seen_color(self, result: Dict[str, Any]) -> None:
        flow = getattr(self.machine, "flow", None)
        if flow is None or not flow.awaiting_color or self.round_end_reason is not None:
            return
        expected = flow.expected_color()
        confirmed = [target for target in result.get("targets", []) if target.get("valid") and target.get("color")]
        if any(target.get("color") == expected for target in confirmed):
            self._dispatch("target_color", {"color": expected})
            return
        if confirmed:
            self._dispatch("target_color", {"color": confirmed[0].get("color")})

    def _review_obstacles(self) -> None:
        if not self.latch.competition or not self.latch.mission_armed() or self.round_end_reason is not None:
            return
        kept, can_plan = assess(
            self.obstacle_sights,
            self.obstacle_min_confidence,
            self.obstacle_width_tolerance_mm,
            self._obstacle_previous,
            self.obstacle_clearance_mm,
        )
        self._obstacle_previous = kept
        self._obstacle_can_plan = can_plan

    def _tick_mission(self, now_ms: float) -> None:
        """先看比赛时钟。停滞、到时、未启动都停车，不再按步骤超时恢复。"""
        if self._reports_are_offline(now_ms):
            self._stop_round("sensor_offline")
            return
        self._apply_match_clock(now_ms)
        if self.round_end_reason is not None or not self.latch.mission_armed():
            return
        actions = self.machine.tick(now_ms)
        self._execute(actions, now_ms)

    def _apply_match_clock(self, now_ms: float) -> None:
        """把 SPEED 帧里的速度送进比赛时钟。速度不进动作命令，也不进心跳。"""
        if self.match_clock is None or self.round_end_reason is not None:
            return
        if self.machine.state == MissionState.COMPLETE:
            return
        outcome = self.match_clock.tick(
            now_ms,
            self.speed_mm_s,
            self.waiting_turntable,
            finished=False,
        )
        if outcome == "running":
            return
        self.round_end_reason = outcome
        self.machine.end_round(outcome)
        self.task_board.set_round_end(outcome)
        self.latch.end()
        self.session.write("round_ended", {"reason": outcome, "remaining_ms": self.match_clock.remaining_ms(now_ms)})
        self._execute([MissionAction("STOP", {"reason_code": STOP_REASON[outcome]})], now_ms)

    def _advance_fine_localization(self, vision_result: Dict[str, Any], now_ms: float) -> None:
        """抓取前精定位：STABLE 后下发原动作；超时/丢失则 action_failed。"""
        if self._fine_localizer is None or self._fine_pending_action is None:
            return
        from vision.精定位 import LocalizationStatus

        localization = self._fine_localizer.update(vision_result, now_ms)
        if localization.status == LocalizationStatus.STABLE:
            action = self._fine_pending_action
            coordinate = list(localization.coordinate_mm) if localization.coordinate_mm else None
            if not self._ring_ready(action, coordinate):
                self.session.write("ring_align_hold", {"action": action.name})
                return
            data = dict(action.data)
            data["coordinate_mm"] = coordinate
            enriched = MissionAction(action.name, data)
            self._fine_localizer = None
            self._fine_pending_action = None
            message_type = self._wire_message_type(enriched.name)
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

    def _publish_task_board(self, task_code: str) -> None:
        """把任务码送到上部显示。失败则留在读码，不进入原料区。"""
        payload = self.task_board.show_task(task_code)
        if self.display.send(payload):
            self._mirror_board(payload)
            if self.machine.display_pending:
                self._dispatch("task_displayed")
            return
        if not self.display_required:
            if self.machine.display_pending:
                self._dispatch("task_displayed")
            return
        self.session.write("display_failed", {"task_code": task_code})

    def _sync_task_board(self, before: tuple) -> None:
        """第一次显示成功之后，抓取或放置增加才重写。失败只记脏标记。"""
        after = (self.task_board.grab_count, self.task_board.place_count)
        if after == before or self.machine.display_pending or not self.task_board.task_code:
            return
        self._write_score_line()

    def _write_score_line(self) -> None:
        payload = self.task_board.payload()
        if self.display.send(payload):
            self._board_dirty = False
            self._mirror_board(payload)
            return
        self._board_dirty = True

    def _mirror_board(self, payload) -> None:
        """屏幕已经写下的同一行再上行。失败只记日志。"""
        if self.wireless.send(payload):
            return
        if self.wireless.address:
            self.session.write("wireless_uplink_failed", {"text": payload.text()})

    def note_downlink(self, payload: Dict[str, Any]) -> None:
        """下行关闭时丢掉速度命令，不写 speed_mm_s。"""
        accepted = self.wireless.receive(payload)
        if not isinstance(accepted, dict) or "speed_mm_s" not in accepted:
            return
        self.speed_mm_s = int(accepted["speed_mm_s"])

    def _retry_task_board(self) -> None:
        if self.machine.display_pending and self.task_board.task_code:
            payload = self.task_board.payload()
            if self.display.send(payload):
                self._mirror_board(payload)
                self._dispatch("task_displayed")
            return
        if self._board_dirty and self.task_board.task_code:
            self._write_score_line()

    def _search_settled(self, point: tuple) -> bool:
        threshold = self.lane.arrival_threshold_mm
        if threshold is None:
            threshold = self.arrival_threshold_mm
        return arrived(
            self.odometry_xy,
            point,
            threshold,
            self.speed_mm_s,
            self.machine.still_speed_mm_s,
        )

    def _send_search_step(self, point: tuple) -> bool:
        action = MissionAction("GOTO", {
            "slot": 0, "color": 0, "zone": int(ZoneId.QR_VIEW), "ring": 0, "batch": 0,
        })
        if route_to_point(self.lane, self.odometry_xy, point) is None:
            self._stop_round("qr_not_found")
            return False
        if not self._route_allowed(action):
            self.session.write("goto_withheld", {"action": action.name, "zone": action.data.get("zone")})
            return False
        self._execute([action], self._now_ms())
        return self.round_end_reason is None

    def _competition_qr_read(self, frame: Any) -> Optional[Any]:
        """比赛读码先到 qr_view，再按步长在板的允许区域内找完整四组码。"""
        if self.scanner_mode != "camera_qr":
            return self.scanner.read() if self.scanner is not None else None
        if self._qr_search is None:
            points = qr_search_points(self.lane)
            if not points:
                self._stop_round("qr_not_found")
                return None
            self._qr_search = QrSearch(points)
        search = self._qr_search
        point = search.current()
        if point is None:
            return None
        if self._qr_pose_sent != search.index:
            if not self._send_search_step(point):
                return None
            self._qr_pose_sent = search.index
            return None
        if not self._search_settled(point):
            return None
        reader = self.qr_reader
        results = reader.read(frame) if reader is not None else []
        if not results and reader is not None and reader.pending():
            return None
        text = results[0].raw_value if results else None
        outcome = search.observe(text)
        if outcome == "found":
            return validate_scan(ScanType.TASK_QR, search.found or "", "camera")
        if outcome == "exhausted":
            self._stop_round("qr_not_found")
        return None

    def _read_scan(self, frame: Any) -> Optional[Any]:
        """从所选扫码源获取一条结果；相机二维码仅在等待读取任务时解码。"""
        if self.machine.state != MissionState.S1_READ_TASK:
            return None
        if self.latch.competition:
            return self._competition_qr_read(frame)
        if self.scanner_mode == "camera_qr":
            results = self.qr_reader.read(frame) if self.qr_reader is not None else []
            return results[0] if results else None
        return self.scanner.read() if self.scanner is not None else None

    def run(self, max_runtime_s: Optional[float] = None) -> MissionState:
        if self.latch.competition:
            # 上电只等待发令和车上按钮，不进入自检。
            self.session.write("competition_hold", {"state": self.latch.state.value})
            started = time.monotonic()
            while not self.stop_requested and self.latch.state not in (LatchState.RUNNING, LatchState.ENDED):
                if max_runtime_s is not None and time.monotonic() - started >= max_runtime_s:
                    break
                self.step()
                time.sleep(self.loop_sleep_s)
            if self.latch.state != LatchState.RUNNING:
                return self.machine.state
            self._arm_competition_read()
        else:
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
        self.wireless.close()
        self.display.close()
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
