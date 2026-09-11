# -*- coding: utf-8 -*-
"""审查修复回归测试：覆盖 2026-08-02 代码审查报告中的关键修复点。

覆盖项：
- P1-1 无效扫码在 S1 内重试、超限进入恢复，不再抛异常崩溃
- P1-3 两批物料允许使用相同色环码，仅批次内唯一
- P1-4 动作->命令映射收敛到 protocol/串口协议.py 单一权威来源
- P2-1 心跳使用独立序号域，不与命令序号碰撞
- P2-2 坏帧后能重新同步并继续解析后续有效帧
"""

import unittest
from pathlib import Path

from mission.扫码读取 import ScanResult, ScanType, parse_task_qr
from mission.任务计划 import TaskPlan
from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine
from protocol.串口协议 import ACTION_TYPES, Frame, FrameParser, MessageType, ProtocolEndpoint
from protocol.协议仿真 import ProtocolActionBridge


class ReviewFixTests(unittest.TestCase):
    def invalid_scan(self) -> ScanResult:
        return ScanResult(ScanType.TASK_QR, "{}", False, "test")

    def valid_scan(self) -> ScanResult:
        payload = (
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],'
            '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}'
        )
        return ScanResult(ScanType.TASK_QR, payload, True, "test")

    # ---- P1-1 无效扫码重试 ----

    def test_invalid_scan_retries_within_s1_without_crashing(self):
        machine = MissionStateMachine(max_scan_retries=3)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        # 前两次无效码：保持 S1 并返回空动作（等待重扫）
        for _ in range(2):
            actions = machine.handle(MissionEvent("task_scanned", {"scan": self.invalid_scan()}), 2)
            self.assertEqual(actions, [])
            self.assertEqual(machine.state, MissionState.S1_READ_TASK)
            self.assertEqual(machine.scan_retries, 1 if _ == 0 else 2)
        # 第三次达到上限：进入恢复流程而非崩溃
        actions = machine.handle(MissionEvent("task_scanned", {"scan": self.invalid_scan()}), 3)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        self.assertTrue(any(action.name == "recover" for action in actions))

    def test_valid_scan_after_retries_resets_counter(self):
        machine = MissionStateMachine(max_scan_retries=3)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": self.invalid_scan()}), 2)
        machine.handle(MissionEvent("task_scanned", {"scan": self.valid_scan()}), 3)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual(machine.scan_retries, 0)

    # ---- P1-3 跨批同色环码 ----

    def test_task_plan_allows_same_ring_code_across_batches(self):
        payload = (
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],'
            '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"1"}]}'
        )
        scan = ScanResult(ScanType.TASK_QR, payload, True, "test")
        plan = TaskPlan(parse_task_qr(scan))
        self.assertEqual(len(plan.records), 2)

    def test_task_plan_rejects_duplicate_ring_code_within_batch(self):
        payload = (
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"},'
            '{"material_id":"F2","color":"green","ring_code":"1"}],'
            '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}'
        )
        scan = ScanResult(ScanType.TASK_QR, payload, True, "test")
        with self.assertRaises(ValueError):
            TaskPlan(parse_task_qr(scan))

    # ---- P1-4 映射收敛 ----

    def test_action_mapping_is_single_source_of_truth(self):
        from app.实机运行 import ACTION_TYPES as live_action_types

        self.assertEqual(ACTION_TYPES["load_first_material"], MessageType.PICK)
        self.assertEqual(live_action_types["load_first_material"], MessageType.PICK)
        self.assertEqual(ProtocolActionBridge.ACTION_TYPES["load_first_material"], MessageType.PICK)
        # 三处映射必须完全一致
        self.assertEqual(live_action_types, ACTION_TYPES)
        self.assertEqual(ProtocolActionBridge.ACTION_TYPES, ACTION_TYPES)

    # ---- P2-1 心跳序号隔离 ----

    def test_heartbeat_uses_dedicated_sequence_space(self):
        endpoint = ProtocolEndpoint()
        heartbeat = FrameParser().feed(endpoint.poll(0)[0])[0]
        # 心跳序号落在独立域 251..255，命令序号落在 1..250
        self.assertGreaterEqual(heartbeat.sequence, 251)
        command = FrameParser().feed(endpoint.send_command(MessageType.PING, b"", 0))[0]
        self.assertLessEqual(command.sequence, 250)

    def test_heartbeat_sequence_never_matches_pending_command(self):
        endpoint = ProtocolEndpoint()
        for _ in range(300):
            endpoint.poll(0)
        pending_packet = endpoint.send_command(MessageType.PING, b"", 0)
        pending_frame = FrameParser().feed(pending_packet)[0]
        heartbeat_frame = FrameParser().feed(endpoint.poll(100)[0])[0]
        self.assertNotEqual(pending_frame.sequence, heartbeat_frame.sequence)

    # ---- P2-2 坏帧重同步 ----

    def test_parser_resyncs_after_corrupt_frame(self):
        good = Frame(MessageType.GRASP, b"ok").encode()
        corrupt = bytearray(Frame(MessageType.GRASP, b"bad").encode())
        corrupt[4] ^= 0x01  # 破坏 message_type 字节（CRC 将不匹配）
        parser = FrameParser()
        # 坏帧后紧跟一个好帧：坏帧被丢弃，好帧应被正确解析
        frames = parser.feed(bytes(corrupt) + good)
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0].message_type, MessageType.GRASP)
        self.assertEqual(frames[0].payload, b"ok")
        self.assertGreaterEqual(parser.invalid_frames, 1)


class RecoveryReissueTests(unittest.TestCase):
    """P0-1 恢复机制：恢复成功后重发被中断的硬件动作，恢复态不再自锁。"""

    def valid_scan(self) -> ScanResult:
        payload = (
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],'
            '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}'
        )
        return ScanResult(ScanType.TASK_QR, payload, True, "test")

    def to_s2(self, machine: MissionStateMachine) -> None:
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": self.valid_scan()}), 2)

    def test_recovery_reissues_interrupted_action(self):
        machine = MissionStateMachine()
        self.to_s2(machine)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual(machine.in_flight.name, "load_first_material")
        # 动作在执行前被中断（安全门拦截/通信丢失）进入恢复
        machine.handle(MissionEvent("communication_lost"), 3)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        # 恢复成功必须重发被中断的动作，而非输出无映射的 resume_state
        actions = machine.handle(MissionEvent("recovery_done"), 4)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual([a.name for a in actions], ["load_first_material"])
        # 重发后正常确认可推进
        machine.handle(MissionEvent("material_loaded"), 5)
        self.assertEqual(machine.state, MissionState.S3_PLACE_RING)
        self.assertEqual(machine.in_flight.name, "place_ring")

    def test_place_ring_is_reissued_after_interruption(self):
        machine = MissionStateMachine()
        self.to_s2(machine)
        machine.handle(MissionEvent("material_loaded"), 3)
        self.assertEqual(machine.state, MissionState.S3_PLACE_RING)
        self.assertEqual(machine.in_flight.name, "place_ring")
        machine.handle(MissionEvent("safety_fault"), 4)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        actions = machine.handle(MissionEvent("recovery_done"), 5)
        self.assertEqual([a.name for a in actions], ["place_ring"])
        machine.handle(MissionEvent("ring_placed"), 6)
        self.assertEqual(machine.state, MissionState.S4_SECOND_BATCH_ASSEMBLY)

    def test_return_home_is_reissued_after_interruption(self):
        machine = MissionStateMachine()
        self.to_s2(machine)
        machine.handle(MissionEvent("material_loaded"), 3)
        machine.handle(MissionEvent("ring_placed"), 4)
        self.assertEqual(machine.state, MissionState.S4_SECOND_BATCH_ASSEMBLY)
        machine.handle(MissionEvent("material_loaded"), 5)
        machine.handle(MissionEvent("assembly_done"), 6)
        self.assertEqual(machine.state, MissionState.S5_RETURN_HOME)
        self.assertEqual(machine.in_flight.name, "return_home")
        machine.handle(MissionEvent("communication_lost"), 7)
        actions = machine.handle(MissionEvent("recovery_done"), 8)
        self.assertEqual(machine.state, MissionState.S5_RETURN_HOME)
        self.assertEqual([a.name for a in actions], ["return_home"])

    def test_recovery_second_failure_does_not_override_resume_state(self):
        machine = MissionStateMachine()
        self.to_s2(machine)
        machine.handle(MissionEvent("communication_lost"), 3)
        self.assertEqual(machine.resume_state, MissionState.S2_FIRST_BATCH)
        # 在 RECOVERY 态再次失败：不得把 resume_state 覆盖为 RECOVERY 自身
        machine.handle(MissionEvent("action_failed", {"error": "recover_failed"}), 4)
        self.assertEqual(machine.resume_state, MissionState.S2_FIRST_BATCH)
        actions = machine.handle(MissionEvent("recovery_done"), 5)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual([a.name for a in actions], ["load_first_material"])

    def test_recovery_state_times_out_to_failed(self):
        machine = MissionStateMachine(state_timeout_ms=50, max_recovery_attempts=2)
        self.to_s2(machine)
        machine.handle(MissionEvent("communication_lost"), 100)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        # RECOVERY 超时：继续重试恢复（不再排除在超时判定之外）
        actions = machine.tick(160)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        self.assertEqual(actions[0].name, "recover")
        # 第二次超时：恢复次数耗尽 → FAILED
        machine.tick(230)
        self.assertEqual(machine.state, MissionState.FAILED)

    def test_s1_scan_recovery_has_no_in_flight_reissue(self):
        machine = MissionStateMachine(max_scan_retries=3)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        for _ in range(3):
            machine.handle(MissionEvent("task_scanned", {"scan": ScanResult(ScanType.TASK_QR, "{}", False, "test")}), 2)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        actions = machine.handle(MissionEvent("recovery_done"), 3)
        self.assertEqual(machine.state, MissionState.S1_READ_TASK)
        # S1 没有待重发的硬件动作：退化为 resume_state 动作（无映射，仅日志）
        self.assertEqual([a.name for a in actions], ["resume_state"])


if __name__ == "__main__":
    unittest.main()
