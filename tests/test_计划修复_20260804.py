# -*- coding: utf-8 -*-
"""2026-08-04 审查修复计划回归测试（S1~S3 关键项）。"""

from __future__ import annotations

import unittest

from mission.扫码读取 import ExternalScannerParser, ScanResult, ScanType, parse_task_qr
from mission.任务计划 import TaskPlan
from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine
from mission.视觉任务状态机 import TaskEvent, TaskState, TaskStateMachine
from protocol.串口协议 import Frame, FrameParser, MessageType, ProtocolEndpoint
from vision.精定位 import FineLocalizer, LocalizationStatus


class PlanFix20260804Tests(unittest.TestCase):
    def test_fail_pending_allows_next_command(self):
        endpoint = ProtocolEndpoint(ack_timeout_ms=1000, done_timeout_ms=2000, max_retries=0)
        endpoint.send_command(MessageType.PICK, b"{}", 0.0)
        self.assertTrue(endpoint.fail_pending("write_failed"))
        # 失效后可立即下发下一条（含 recover）
        packet = endpoint.send_command(MessageType.RECOVER, b"{}", 1.0)
        self.assertEqual(len(packet), 10)
        self.assertEqual(FrameParser().feed(packet)[0].message_type, MessageType.RECOVER)
        self.assertEqual(endpoint.pending.message_type, MessageType.RECOVER)

    def test_ack_then_timeout_does_not_resend(self):
        endpoint = ProtocolEndpoint(
            heartbeat_interval_ms=10_000,
            ack_timeout_ms=100,
            done_timeout_ms=200,
            max_retries=1,
        )
        endpoint.last_heartbeat_sent_ms = 0.0  # 抑制首包心跳
        endpoint.send_command(MessageType.PICK, b"{}", 0.0)
        seq = endpoint.pending.sequence
        endpoint.receive(Frame(MessageType.ACK, sequence=seq).encode(), 10.0)
        self.assertTrue(endpoint.pending.acknowledged)
        out = endpoint.poll(300.0)
        self.assertTrue(endpoint.pending.failed)
        self.assertEqual(endpoint.last_error, "action_timeout")
        self.assertEqual(out, [])

    def test_scanner_accepts_long_task_line(self):
        payload = (
            '{"task_id":"T-LONG","first_batch":['
            '{"material_id":"F1","color":"red","ring_code":"1"},'
            '{"material_id":"F2","color":"green","ring_code":"2"}],'
            '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"3"}]}'
        )
        self.assertGreater(len(payload.encode("utf-8")), 128)
        parser = ExternalScannerParser(ScanType.TASK_QR, max_line_length=512)
        results = parser.feed(payload.encode("utf-8") + b"\n")
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].valid)

    def test_ring_code_must_be_1_2_3(self):
        payload = (
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"9"}],'
            '"second_batch":[]}'
        )
        scan = ScanResult(ScanType.TASK_QR, payload, True, "test")
        with self.assertRaises(ValueError):
            TaskPlan(parse_task_qr(scan))

    def test_scan_retries_reset_on_recovery_to_s1(self):
        machine = MissionStateMachine(max_scan_retries=2, max_recovery_attempts=2)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        invalid = ScanResult(ScanType.TASK_QR, "{}", False, "test")
        machine.handle(MissionEvent("task_scanned", {"scan": invalid}), 2)
        self.assertEqual(machine.scan_retries, 1)
        machine.handle(MissionEvent("task_scanned", {"scan": invalid}), 3)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        machine.handle(MissionEvent("recovery_done"), 4)
        self.assertEqual(machine.state, MissionState.S1_READ_TASK)
        self.assertEqual(machine.scan_retries, 0)

    def test_duplicate_material_loaded_recovers_instead_of_crash(self):
        machine = MissionStateMachine()
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        payload = (
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],'
            '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}'
        )
        scan = ScanResult(ScanType.TASK_QR, payload, True, "test")
        machine.handle(MissionEvent("task_scanned", {"scan": scan}), 2)
        machine.handle(MissionEvent("material_loaded"), 3)
        machine.handle(MissionEvent("ring_placed"), 4)
        # 进入第二批 S4：第一次 material_loaded 后仍停留在 S4
        machine.handle(MissionEvent("material_loaded"), 5)
        self.assertEqual(machine.state, MissionState.S4_SECOND_BATCH_ASSEMBLY)
        actions = machine.handle(MissionEvent("material_loaded"), 6)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        self.assertTrue(any(a.name == "recover" for a in actions))

    def test_s0_recovery_without_inflight_restarts_self_test(self):
        machine = MissionStateMachine()
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("action_failed"), 1)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        machine.in_flight = None
        machine.resume_state = MissionState.S0_INIT
        actions = machine.handle(MissionEvent("recovery_done"), 2)
        self.assertEqual(machine.state, MissionState.S0_INIT)
        self.assertTrue(any(a.name == "self_test" for a in actions))

    def test_jump_rejected_does_not_recover_vision_sm(self):
        localizer = FineLocalizer(
            required_samples=3, max_spread_mm=5, max_step_mm=30, timeout_ms=5000, color="red"
        )
        machine = TaskStateMachine(localizer)
        machine.handle(TaskEvent("start"), 0)
        target = {
            "valid": True,
            "world_mm": [10.0, 20.0],
            "confidence": 0.9,
            "target_id": 1,
            "color": "red",
        }
        machine.handle(TaskEvent("vision_update", {"targets": [target]}), 1)
        machine.handle(TaskEvent("arrived_near_target"), 2)
        self.assertEqual(machine.state, TaskState.FINE_LOCALIZING)
        machine.handle(
            TaskEvent("vision_update", {"targets": [target]}),
            3,
        )
        jumped = {
            "valid": True,
            "world_mm": [80.0, 20.0],
            "confidence": 0.9,
            "target_id": 1,
            "color": "red",
        }
        actions = machine.handle(TaskEvent("vision_update", {"targets": [jumped]}), 4)
        self.assertEqual(machine.state, TaskState.FINE_LOCALIZING)
        self.assertFalse(any(a.name == "recover" for a in actions))
        self.assertTrue(any(a.name == "fine_localization_progress" for a in actions))
        # 确认底层确实给出跳变拒绝语义
        status = localizer.update({"targets": [jumped]}, 5)
        self.assertIn(status.status, (LocalizationStatus.JUMP_REJECTED, LocalizationStatus.COLLECTING, LocalizationStatus.WAITING))


if __name__ == "__main__":
    unittest.main()
