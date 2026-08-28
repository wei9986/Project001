"""运行循环：连接相机、识别、安全检查和任务状态机。调试按"相机、目标、站点、动作、安全"顺序排查。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import cv2
import numpy as np

from app.虚拟设备 import VirtualCamera, VirtualScanner
from app.虚拟设备 import VirtualSensors
from app.安全监控 import SafetyMonitor
from app.视觉应用 import ActionLogger, ApplicationConfig, load_simulation_scan
from mission.任务状态机 import MissionAction, MissionEvent, MissionState, MissionStateMachine
from vision.颜色追踪 import ColorTracker, load_config
from vision.站点定位 import AprilTagStationDetector, StationDetection
from vision.配置加载 import project_root, resolve_path
from app.运行观测 import RuntimeSnapshotLogger


@dataclass(frozen=True)
class RuntimeSnapshot:
    """一轮运行的关键状态，便于写入日志。"""
    cycle: int
    timestamp_ms: float
    state: str
    target_count: int
    station_count: int
    scanner_value: Optional[str]
    actions: List[str]


def build_demo_frames(frame_count: int = 12) -> List[np.ndarray]:
    """生成固定的红色目标和 AprilTag 测试画面。"""

    if frame_count <= 0:
        raise ValueError("frame_count 必须为正数")
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, 1, 120)
    frames: List[np.ndarray] = []
    for index in range(frame_count):
        frame = np.full((480, 640, 3), 255, dtype=np.uint8)
        center_x = 180 + index % 4 * 4
        cv2.circle(frame, (center_x, 300), 32, (0, 0, 255), -1)
        frame[40:160, 460:580] = cv2.cvtColor(marker, cv2.COLOR_GRAY2BGR)
        frames.append(frame)
    return frames


class RuntimeLoop:
    """使用可替换设备运行一项完整任务。

    设备接口约定（duck-typing，真实硬件接入时实现以下方法即可）：
      camera.read()  -> (bool, np.ndarray|None)
      scanner.read() -> ScanResult|None
      sensors         -- 需提供 tof_distance_mm, grasp_confirmed 等属性
    """

    ACTION_EVENTS = {
        "load_first_material": "material_loaded",
        "place_ring": "ring_placed",
        "load_second_material": "material_loaded",
        "assemble_material": "assembly_done",
        "return_home": "home_confirmed",
        "recover": "recovery_done",
    }

    def __init__(
        self,
        config: ApplicationConfig,
        camera: Any,  # duck-typing: 需实现 read() -> (bool, np.ndarray|None)
        scanner: Any,  # duck-typing: 需实现 read() -> ScanResult|None
        action_logger: ActionLogger,
        color_config_path: Path,
        status_logger: Optional[RuntimeSnapshotLogger] = None,
        sensors: Any = None,  # duck-typing: 需提供 tof_distance_mm, grasp_confirmed 等属性
        safety_monitor: Optional[SafetyMonitor] = None,
        color_names: Optional[Sequence[str]] = None,
        station_config_path: Optional[Path] = None,
        step_ms: float = 20.0,
        minimum_tof_mm: float = 80.0,
    ) -> None:
        """连接配置、设备、识别器和日志，不主动打开硬件。

        真实硬件接入：传入实现了 read() 方法的 camera/scanner 对象即可，
        无需继承特定基类。默认使用 VirtualSensors 作为传感器占位。
        """
        self.config = config
        self.camera = camera
        self.scanner = scanner
        self.action_logger = action_logger
        self.status_logger = status_logger
        self.step_ms = float(step_ms)
        self.machine = MissionStateMachine(config.state_timeout_ms, config.max_recovery_attempts)
        colors = tuple(color_names) if color_names else ("red",)
        self.tracker = ColorTracker(colors, load_config(str(resolve_path(color_config_path))))
        station_path = resolve_path(
            station_config_path or (project_root() / "config" / "站点标签.json")
        )
        self.station_detector = AprilTagStationDetector.from_config(path=station_path)
        self.sensors = sensors or VirtualSensors(grasp_confirmed=None)
        self.safety_monitor = safety_monitor or SafetyMonitor(minimum_tof_mm=minimum_tof_mm)
        self.cycle = 0
        self.now_ms = 0.0
        self.pending_actions: List[MissionAction] = []
        self.snapshots: List[RuntimeSnapshot] = []
        self.camera_errors = 0

    def _dispatch(self, name: str, data: Optional[Dict[str, Any]] = None) -> List[MissionAction]:
        """向状态机发送事件并记录返回动作。"""
        actions = self.machine.handle(MissionEvent(name, data), self.now_ms)
        for action in actions:
            self.action_logger.write(action, self.machine.state, self.now_ms)
        return actions

    def start(self) -> None:
        """执行启动与自检握手。"""
        self.pending_actions = self._dispatch("start")
        self.now_ms += 1.0
        self.pending_actions = self._dispatch("self_test_ok")

    def step(self) -> RuntimeSnapshot:
        """执行一轮循环并返回状态快照。"""
        self.cycle += 1
        self.now_ms += self.step_ms
        ok, frame = self.camera.read()
        actions: List[MissionAction] = []
        target_count = 0
        station_count = 0
        scanner_value = None
        if not ok or frame is None:
            # P2-11：对齐实机——丢帧记错误并仍 tick，不硬崩整场循环。
            self.camera_errors += 1
            tick_actions = self.machine.tick(self.now_ms)
            for action in tick_actions:
                self.action_logger.write(action, self.machine.state, self.now_ms)
            if tick_actions:
                self.pending_actions = tick_actions
                actions.extend(tick_actions)
            snapshot = RuntimeSnapshot(
                cycle=self.cycle,
                timestamp_ms=self.now_ms,
                state=self.machine.state.value,
                target_count=0,
                station_count=0,
                scanner_value=None,
                actions=[action.name for action in actions],
            )
            self.snapshots.append(snapshot)
            if self.status_logger is not None:
                self.status_logger.write(snapshot)
            return snapshot
        visual_result: Dict[str, Any] = {"targets": []}
        stations: List[StationDetection] = []
        try:
            _, visual_result = self.tracker.track(frame)
            stations = self.station_detector.detect(frame)
        except Exception:  # noqa: BLE001 - 对齐 LiveRuntime：单帧视觉异常不得中断整场循环
            self.camera_errors += 1
            tick_actions = self.machine.tick(self.now_ms)
            for action in tick_actions:
                self.action_logger.write(action, self.machine.state, self.now_ms)
            if tick_actions:
                self.pending_actions = tick_actions
                actions.extend(tick_actions)
            snapshot = RuntimeSnapshot(
                cycle=self.cycle,
                timestamp_ms=self.now_ms,
                state=self.machine.state.value,
                target_count=0,
                station_count=0,
                scanner_value=None,
                actions=[action.name for action in actions],
            )
            self.snapshots.append(snapshot)
            if self.status_logger is not None:
                self.status_logger.write(snapshot)
            return snapshot
        scan = self.scanner.read() if self.machine.state == MissionState.S1_READ_TASK else None
        target_count = len(visual_result.get("targets", []))
        station_count = len(stations)
        scanner_value = scan.raw_value if scan is not None else None

        if scan is not None:
            self.pending_actions = self._dispatch("task_scanned", {"scan": scan})
            actions.extend(self.pending_actions)
        elif self.pending_actions and self.machine.state not in (MissionState.COMPLETE, MissionState.FAILED):
            # 排查顺序：画面 -> 目标 -> 站点 -> 待执行动作 -> 安全判定。
            target_ready = any(target.get("valid") for target in visual_result.get("targets", []))
            # 只有已映射为站点的标签才算"站点就绪"，未配置的干扰标签不得推进状态机。
            station_ready = any(detection.station_id is not None for detection in stations)
            if target_ready and station_ready:
                event_name = self.ACTION_EVENTS.get(self.pending_actions[-1].name)
                if event_name:
                    if self.pending_actions[-1].name == "recover":
                        # 恢复动作跳过安全门检查，避免恢复死锁
                        self.pending_actions = self._dispatch(event_name)
                    else:
                        decision = self.safety_monitor.check(self.pending_actions[-1].name, self.sensors)
                        if decision.allowed:
                            self.pending_actions = self._dispatch(event_name)
                        else:
                            self.pending_actions = self._dispatch(
                                "safety_fault", {"fault": decision.fault, "reason": decision.reason}
                            )
                    actions.extend(self.pending_actions)

        # 检查任务状态机超时，触发恢复流程
        tick_actions = self.machine.tick(self.now_ms)
        for action in tick_actions:
            self.action_logger.write(action, self.machine.state, self.now_ms)
        if tick_actions:
            self.pending_actions = tick_actions
            actions.extend(tick_actions)

        snapshot = RuntimeSnapshot(
            cycle=self.cycle,
            timestamp_ms=self.now_ms,
            state=self.machine.state.value,
            target_count=target_count,
            station_count=station_count,
            scanner_value=scanner_value,
            actions=[action.name for action in actions],
        )
        self.snapshots.append(snapshot)
        if self.status_logger is not None:
            self.status_logger.write(snapshot)
        return snapshot

    def run(self, max_cycles: int = 100) -> MissionState:
        """循环运行到完成；超出轮数时抛出超时错误。

        说明：本方法的上限是运行级保险（防止死循环吞掉比赛），与状态机 tick()
        的状态级超时恢复是两层语义——tick 负责单个状态超时后的恢复，run 的
        max_cycles 是任务整体兜底。调用方应根据需要捕获 TimeoutError 并决定
        重试或进入人工处理。
        """
        if max_cycles <= 0:
            raise ValueError("max_cycles 必须为正数")
        try:
            self.start()
            while self.machine.state not in (MissionState.COMPLETE, MissionState.FAILED):
                if self.cycle >= max_cycles:
                    raise TimeoutError(
                        f"运行时循环超过最大周期 {max_cycles}，状态：{self.machine.state.value}"
                        f"，恢复尝试：{self.machine.recovery_attempts}"
                    )
                self.step()
            if self.machine.state != MissionState.COMPLETE:
                raise RuntimeError(f"任务未完成，状态：{self.machine.state.value}")
            return self.machine.state
        finally:
            # 关闭日志文件句柄，避免临时目录清理或进程退出时句柄泄漏。
            self.close()

    def close(self) -> None:
        """关闭运行时持有的日志文件句柄；重复调用是安全的。"""
        self.action_logger.close()
        if self.status_logger is not None:
            self.status_logger.close()


def create_simulation_runtime(config: ApplicationConfig) -> RuntimeLoop:
    """创建无硬件、结果固定的集成仿真环境。"""
    root = project_root()
    scanner = VirtualScanner([load_simulation_scan(config.task_path)])
    camera = VirtualCamera(build_demo_frames())
    return RuntimeLoop(
        config=config,
        camera=camera,
        scanner=scanner,
        action_logger=ActionLogger(config.log_path),
        color_config_path=root / "config" / "颜色追踪配置.json",
        station_config_path=root / "config" / "站点标签.json",
        color_names=("red",),
        status_logger=RuntimeSnapshotLogger(config.log_path.with_suffix(".status.jsonl")),
        sensors=VirtualSensors(tof_distance_mm=120.0, grasp_confirmed=None),
    )
