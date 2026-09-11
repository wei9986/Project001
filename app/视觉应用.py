"""视觉模块应用入口。调试：运行中文模块仿真后查看 JSONL 动作日志。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from mission.任务状态机 import MissionAction, MissionEvent, MissionState, MissionStateMachine
from mission.扫码读取 import ScanResult, ScanType


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TASK_PATH = ROOT_DIR / "config" / "仿真任务.json"
DEFAULT_LOG_PATH = ROOT_DIR / "logs" / "application_simulation.jsonl"
DEFAULT_LIVE_CONFIG_PATH = ROOT_DIR / "config" / "实机配置.json"


@dataclass(frozen=True)
class ApplicationConfig:
    """不依赖具体硬件的运行参数。"""

    task_path: Path
    log_path: Path
    state_timeout_ms: float = 5000.0
    max_recovery_attempts: int = 2


class ActionLogger:
    """将状态机动作逐行写入 JSONL 日志（持有文件句柄，避免每帧重复打开）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def write(self, action: MissionAction, state: MissionState, now_ms: float) -> None:
        """追加一条动作日志，不覆盖旧记录。"""
        record = {
            "timestamp_ms": now_ms,
            "state": state.value,
            "action": action.name,
            "data": action.data,
        }
        self._file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        """关闭日志文件；重复调用是安全的。"""
        if not self._file.closed:
            self._file.close()


def load_simulation_scan(path: Path) -> ScanResult:
    """将仿真任务 JSON 转成统一扫码结果。"""

    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    return ScanResult(ScanType.TASK_QR, json.dumps(payload, ensure_ascii=False), True, "simulation")


def load_live_config(path: Path) -> Dict[str, Any]:
    """读取实机配置，并在启动前校验所有必填设备参数。"""
    with path.open("r", encoding="utf-8") as file:
        config = json.load(file)
    required_sections = ("camera", "scanner", "controller")
    missing = [name for name in required_sections if not isinstance(config.get(name), dict)]
    if missing:
        raise ValueError(f"实机配置缺少对象: {', '.join(missing)}")
    scanner_mode = config["scanner"].get("mode", "serial")
    if scanner_mode not in {"serial", "camera_qr"}:
        raise ValueError("scanner.mode 仅支持 serial 或 camera_qr")
    if scanner_mode == "serial" and not config["scanner"].get("port"):
        raise ValueError("实机配置缺少 scanner.port")
    if not config["controller"].get("port"):
        raise ValueError("实机配置缺少 controller.port")
    return config


class SimulationRunner:
    """用固定事件驱动真实任务状态机完成仿真。"""

    def __init__(self, config: ApplicationConfig, action_logger: ActionLogger) -> None:
        self.config = config
        self.action_logger = action_logger
        self.machine = MissionStateMachine(
            state_timeout_ms=config.state_timeout_ms,
            max_recovery_attempts=config.max_recovery_attempts,
        )
        self.now_ms = 0.0

    def _handle(self, event_name: str, data: Optional[Dict[str, Any]] = None) -> List[MissionAction]:
        """处理一个事件并记录状态机输出动作。"""
        actions = self.machine.handle(MissionEvent(event_name, data), self.now_ms)
        for action in actions:
            self.action_logger.write(action, self.machine.state, self.now_ms)
        return actions

    def run(self) -> MissionState:
        """执行完整仿真；流程卡住时直接报错。"""
        try:
            self._handle("start")
            self.now_ms += 1.0
            self._handle("self_test_ok")
            self.now_ms += 1.0
            pending_actions = self._handle("task_scanned", {"scan": load_simulation_scan(self.config.task_path)})

            # 根据状态机返回的动作生成确认事件，保证仿真流程与真实流程一致。
            event_by_action: Dict[str, str] = {
                "load_first_material": "material_loaded",
                "place_ring": "ring_placed",
                "load_second_material": "material_loaded",
                "assemble_material": "assembly_done",
                "return_home": "home_confirmed",
            }
            while self.machine.state not in (MissionState.COMPLETE, MissionState.FAILED):
                if not pending_actions:
                    # 状态机正在等待确认事件；查看最后一条动作日志即可定位卡点。
                    raise RuntimeError(f"仿真流程没有可确认的动作，当前状态：{self.machine.state.value}")
                next_event = event_by_action.get(pending_actions[-1].name)
                if next_event is None:
                    raise RuntimeError(f"仿真器不支持动作：{pending_actions[-1].name}")
                self.now_ms += 1.0
                pending_actions = self._handle(next_event)

            if self.machine.state != MissionState.COMPLETE:
                raise RuntimeError(f"仿真任务未完成，最终状态：{self.machine.state.value}")
            return self.machine.state
        finally:
            self.action_logger.close()


def build_parser() -> argparse.ArgumentParser:
    """定义命令行参数。"""
    parser = argparse.ArgumentParser(description="物流机器人视觉模块应用入口")
    parser.add_argument("--mode", choices=("simulation", "live"), default="simulation")
    parser.add_argument("--task", type=Path, default=DEFAULT_TASK_PATH)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--state-timeout-ms", type=float, default=5000.0)
    parser.add_argument("--max-recovery-attempts", type=int, default=2)
    parser.add_argument("--live-config", type=Path, default=DEFAULT_LIVE_CONFIG_PATH,
                        help="实机模式使用的 JSON 配置")
    parser.add_argument("--test-root", type=Path, default=ROOT_DIR / "test_logs",
                        help="实机测试会话输出目录")
    parser.add_argument("--session-name", default="live", help="实机测试会话名称")
    parser.add_argument("--max-runtime-s", type=float, default=None, help="实机模式最长运行秒数")
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    """解析参数并返回进程退出码。"""
    args = build_parser().parse_args(argv)
    config = ApplicationConfig(
        task_path=args.task,
        log_path=args.log,
        state_timeout_ms=args.state_timeout_ms,
        max_recovery_attempts=args.max_recovery_attempts,
    )
    if args.mode == "simulation":
        state = SimulationRunner(config, ActionLogger(config.log_path)).run()
        print(json.dumps({"mode": args.mode, "state": state.value, "log": str(config.log_path)}, ensure_ascii=False))
        return 0
    if args.max_runtime_s is not None and args.max_runtime_s <= 0:
        parser.error("--max-runtime-s 必须为正数")
    from app.实机运行 import LiveRuntime
    from app.测试会话 import TestSession

    live_config = load_live_config(args.live_config)
    session = TestSession(args.test_root, args.session_name, {
        "mode": "live", "config_path": str(args.live_config), "colors": live_config.get("colors", []),
    })
    runtime = None
    try:
        runtime = LiveRuntime(config, live_config, session)
        state = runtime.run(args.max_runtime_s)
        print(json.dumps({"mode": "live", "state": state.value, "session": str(session.directory)}, ensure_ascii=False))
        return 0 if state.value == "complete" else 1
    except Exception as exc:
        session.write("fatal_error", {"error": str(exc)})
        raise
    finally:
        if runtime is not None:
            session.close(runtime.machine.state.value, {"camera": runtime.camera.metrics(),
                                                        "controller_reconnects": runtime.controller.reconnects,
                                                        "scanner_mode": runtime.scanner_mode,
                                                        "scanner_reconnects": runtime.scanner.transport.reconnects
                                                        if runtime.scanner is not None else 0})
        else:
            session.close("startup_failed", {"camera": {}, "controller_reconnects": 0,
                                             "scanner_mode": live_config["scanner"].get("mode"),
                                             "scanner_reconnects": 0})


if __name__ == "__main__":
    raise SystemExit(main())
