# -*- coding: utf-8 -*-
"""批次 2 修复回归测试（F-05 / F-06 / F-08 / F-09）。

- F-05：TASK_QR 非空即 valid 的问题，扫码重试语义失真
- F-06：assemble_material 不校验物料批次归属
- F-08：仿真默认跳过安全拦截，导致仿真测不出装配拦截
- F-09：恢复/超时行为缺结构化观测，故障复盘靠猜
"""

import json
import tempfile
import unittest
from pathlib import Path

from app.测试会话 import TestSession
from app.运行循环 import create_simulation_runtime
from app.视觉应用 import ApplicationConfig
from mission.扫码读取 import ScanResult, ScanType, validate_scan
from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine


def _scan(payload: str) -> ScanResult:
    return ScanResult(ScanType.TASK_QR, payload, True, "test")


TWO_BATCH_TASK = json.dumps({
    "task_id": "T1",
    "first_batch": [{"material_id": "F1", "color": "red", "ring_code": "1"}],
    "second_batch": [{"material_id": "S1", "color": "blue", "ring_code": "2"}],
})


class TaskQrValidationTests(unittest.TestCase):
    """F-05：任务二维码必须做轻量预校验，避免脏数据被当成有效扫码。"""

    def test_task_qr_rejects_non_json_prefix(self):
        """非 JSON 前缀（任务码必然是 JSON）应判为无效。"""
        result = validate_scan(ScanType.TASK_QR, "!!!!not-json", "camera")
        self.assertFalse(result.valid, "非 JSON 开头的任务码不应被判为 valid")
        self.assertIsNotNone(result.error)

    def test_task_qr_accepts_json_object(self):
        """合法 JSON 对象仍应通过预校验。"""
        result = validate_scan(ScanType.TASK_QR, TWO_BATCH_TASK, "camera")
        self.assertTrue(result.valid, f"合法任务码应通过，error={result.error}")

    def test_task_qr_rejects_empty_and_oversized(self):
        """空串与超长串一律拒绝。"""
        self.assertFalse(validate_scan(ScanType.TASK_QR, "", "camera").valid)
        self.assertFalse(validate_scan(ScanType.TASK_QR, "{" + "a" * 5000, "camera").valid)

    def test_code128_validation_unchanged(self):
        """CODE128 原有白名单语义不得被破坏。"""
        self.assertTrue(validate_scan(ScanType.CODE128, "ABC-123", "external").valid)
        self.assertFalse(validate_scan(ScanType.CODE128, "含中文", "external").valid)


class BatchValidationTests(unittest.TestCase):
    """F-06：装配动作必须校验物料属于第二批。"""

    def _machine_in_s4(self) -> MissionStateMachine:
        """构造停在 S4（第二批装配）的状态机。"""
        machine = MissionStateMachine(state_timeout_ms=5000, max_recovery_attempts=2)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": _scan(TWO_BATCH_TASK)}), 2)
        # 第一批
        machine.handle(MissionEvent("material_loaded"), 3)
        machine.handle(MissionEvent("ring_placed"), 4)
        # 进入 S4 并取第二批
        self.assertEqual(machine.state, MissionState.S4_SECOND_BATCH_ASSEMBLY)
        machine.handle(MissionEvent("material_loaded"), 5)
        return machine

    def test_assemble_material_belongs_to_second_batch(self):
        """正常路径：S4 装配的必须是第二批物料。"""
        machine = self._machine_in_s4()
        record = machine._require_current(expected_batch="second")
        self.assertEqual(record.batch, "second")
        self.assertEqual(record.instruction.material_id, "S1")

    def test_require_current_rejects_wrong_batch(self):
        """批次不匹配时立即报错，不得静默装配错物料。"""
        machine = self._machine_in_s4()
        with self.assertRaises(RuntimeError):
            machine._require_current(expected_batch="first")

    def test_assemble_rejects_first_batch_material(self):
        """把 current 篡改成第一批物料后，S4 装配链必须拒绝。"""
        machine = self._machine_in_s4()
        machine.current_material_id = "F1"  # 故意指向第一批物料
        actions = machine.handle(MissionEvent("assembly_done"), 6)
        # 应进入恢复（异常被 handle 捕获），而不是完成装配
        self.assertNotEqual(machine.state, MissionState.S5_RETURN_HOME)
        self.assertTrue(
            any(action.name in ("recover", "safe_stop") for action in actions),
            f"批次不匹配应进入恢复/安全停止，实际动作 {[a.name for a in actions]}",
        )


class SimulationSafetyCoverageTests(unittest.TestCase):
    """F-08：仿真必须能覆盖安全拦截路径，否则安全门在仿真中得不到验证。"""

    def test_simulation_runtime_has_active_grasp_sensor(self):
        """仿真运行时不得默认跳过装配检查，否则永远测不出拦截。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ApplicationConfig(task_path=root / "task.json", log_path=root / "actions.jsonl")
            config.task_path.write_text(TWO_BATCH_TASK, encoding="utf-8")
            runtime = create_simulation_runtime(config)
            try:
                # 仿真应显式接管抓取确认信号（非 None），使安全门参与判定。
                self.assertIsNotNone(
                    runtime.sensors.grasp_confirmed,
                    "仿真传感器的 grasp_confirmed 不应为 None，否则安全门恒被跳过",
                )
            finally:
                runtime.close()

    def test_simulation_safety_gate_can_block(self):
        """仿真里把抓取信号置为未确认时，安全门必须能拦住装配。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = ApplicationConfig(task_path=root / "task.json", log_path=root / "actions.jsonl")
            config.task_path.write_text(TWO_BATCH_TASK, encoding="utf-8")
            runtime = create_simulation_runtime(config)
            try:
                runtime.sensors.update(grasp_confirmed=False)
                decision = runtime.safety_monitor.check("assemble_material", runtime.sensors)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.fault, "grasp_not_confirmed")
            finally:
                runtime.close()


class SessionObservabilityTests(unittest.TestCase):
    """F-09：恢复与状态耗时必须进 summary，供比赛复盘定位抖动。"""

    def test_summary_records_recovery_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            session = TestSession(Path(directory) / "session", "obs", {"mode": "live"})
            session.record_recovery(attempt=1, resume_state="S2_first_batch", reason="state_timeout")
            session.record_recovery(attempt=1, resume_state="S3_place_ring", reason="communication_lost")
            session.record_state("S2_first_batch", 120.0)
            session.record_state("S3_place_ring", 340.0)
            summary = session.close("complete", {})

            self.assertEqual(summary["recovery_events"], 2)
            self.assertEqual(summary["recovery_by_reason"]["state_timeout"], 1)
            self.assertEqual(summary["recovery_by_reason"]["communication_lost"], 1)
            self.assertIn("state_time_ms", summary)
            self.assertEqual(summary["state_time_ms"]["S2_first_batch"], 120.0)
            self.assertEqual(summary["state_time_ms"]["S3_place_ring"], 340.0)

    def test_summary_recovery_metrics_default_zero(self):
        """无恢复发生时指标应为 0 / 空，而不是缺失键。"""
        with tempfile.TemporaryDirectory() as directory:
            session = TestSession(Path(directory) / "session", "obs", {"mode": "live"})
            summary = session.close("complete", {})
            self.assertEqual(summary["recovery_events"], 0)
            self.assertEqual(summary["recovery_by_reason"], {})
            self.assertEqual(summary["state_time_ms"], {})

    def test_summary_is_json_serializable(self):
        with tempfile.TemporaryDirectory() as directory:
            session = TestSession(Path(directory) / "session", "obs", {"mode": "live"})
            session.record_recovery(attempt=2, resume_state="S1_read_task", reason="scan_failed")
            session.close("failed", {})
            payload = json.loads(session.summary_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["recovery_events"], 1)


if __name__ == "__main__":
    unittest.main()
