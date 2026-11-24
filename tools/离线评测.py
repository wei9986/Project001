"""离线评测：无硬件测试正常流程和常见故障场景。"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List

import numpy as np

from app.虚拟设备 import VirtualCamera, VirtualScanner
from app.运行观测 import summarize_snapshots
from app.运行循环 import RuntimeLoop, build_demo_frames
from app.视觉应用 import ActionLogger, ApplicationConfig, load_simulation_scan
from mission.扫码读取 import ScanResult, ScanType
from protocol.串口协议 import ProtocolEndpoint
from protocol.协议仿真 import ProtocolActionBridge
from protocol.传输通道 import create_memory_link
from vision.配置加载 import project_root


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    passed: bool
    expected: str
    observed: str
    metrics: Dict[str, object]


def _runtime(config: ApplicationConfig, frames: List[np.ndarray], scanner: VirtualScanner) -> RuntimeLoop:
    """使用指定画面和扫码器创建可重复运行环境。"""
    root = project_root()
    return RuntimeLoop(
        config=config,
        camera=VirtualCamera(frames, loop=True),
        scanner=scanner,
        action_logger=ActionLogger(config.log_path),
        color_config_path=root / "config" / "颜色追踪配置.json",
        station_config_path=root / "config" / "站点标签.json",
        color_names=("red",),
    )


def evaluate(config: ApplicationConfig) -> List[ScenarioResult]:
    """执行正常、丢目标、丢标签、错扫码和断串口场景。"""
    task_scan = load_simulation_scan(config.task_path)
    results: List[ScenarioResult] = []

    normal = _runtime(config, build_demo_frames(), VirtualScanner([task_scan]))
    normal.run()
    results.append(ScenarioResult("normal", normal.machine.state.value == "complete", "complete", normal.machine.state.value, summarize_snapshots(normal.snapshots)))

    blank = np.full((480, 640, 3), 255, dtype=np.uint8)
    lost_target = _runtime(config, [blank], VirtualScanner([task_scan]))
    try:
        lost_target.run(max_cycles=8)
        observed = lost_target.machine.state.value
    except TimeoutError:
        observed = "timeout"
    results.append(ScenarioResult("target_lost", observed == "timeout", "timeout", observed, {"cycles": lost_target.cycle}))

    no_tag_frames = build_demo_frames()
    for frame in no_tag_frames:
        frame[40:160, 460:580] = 255
    tag_lost = _runtime(config, no_tag_frames, VirtualScanner([task_scan]))
    try:
        tag_lost.run(max_cycles=8)
        observed = tag_lost.machine.state.value
    except TimeoutError:
        observed = "timeout"
    results.append(ScenarioResult("apriltag_lost", observed == "timeout", "timeout", observed, {"cycles": tag_lost.cycle}))

    # 无效扫码不再抛异常：状态机在 S1 内重试（默认上限 3 次），超限后进入恢复。
    # 连续给 3 条无效码，验证系统不崩溃、重试计数正确累积。
    invalid_scan = _runtime(
        config,
        build_demo_frames(),
        VirtualScanner([
            ScanResult(ScanType.TASK_QR, "{}", False, "simulation"),
            ScanResult(ScanType.TASK_QR, "{}", False, "simulation"),
            ScanResult(ScanType.TASK_QR, "{}", False, "simulation"),
        ]),
    )
    try:
        invalid_scan.run(max_cycles=12)
        observed = invalid_scan.machine.state.value
    except TimeoutError:
        observed = invalid_scan.machine.state.value
    results.append(ScenarioResult(
        "invalid_scan",
        # 恢复后会清零 scan_retries（P2-4）；以曾进入恢复或仍停在 S1/failed 判定不崩溃。
        observed in {"S1_read_task", "recovery", "failed"}
        and (invalid_scan.machine.recovery_attempts >= 1 or invalid_scan.machine.scan_retries >= 3),
        "retry_without_crash",
        observed,
        {"scan_retries": invalid_scan.machine.scan_retries, "recovery_attempts": invalid_scan.machine.recovery_attempts},
    ))

    left, right = create_memory_link()
    left.close()
    endpoint = ProtocolEndpoint()
    bridge = ProtocolActionBridge(endpoint, left, right)
    try:
        bridge.send_action("HOME", 0.0, zone=5)
        observed = "sent"
    except RuntimeError:
        observed = "transport_closed"
    results.append(ScenarioResult("serial_disconnect", observed == "transport_closed", "transport_closed", observed, {}))

    return results


def main() -> int:
    """写入评测报告；任一场景失败则返回非零退出码。"""
    parser = argparse.ArgumentParser(description="离线场景评测")
    parser.add_argument("--task", type=Path, default=Path("config/仿真任务.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/offline_evaluation.json"))
    args = parser.parse_args()
    config = ApplicationConfig(args.task, args.output.with_suffix(".actions.jsonl"))
    results = evaluate(config)
    report = {"passed": all(item.passed for item in results), "scenarios": [asdict(item) for item in results]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
