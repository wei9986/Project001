# -*- coding: utf-8 -*-
"""P0-1 恢复机制回归测试：恢复成功后重发被中断的硬件动作。

覆盖：
- 恢复成功后重发在途动作（load_first_material / place_ring）
- 恢复中再次失败不覆盖 resume_state（P1-2）
- RECOVERY 态超时后耗尽恢复次数进入 FAILED
- 无在途动作的恢复（如 S1 扫码失败）只回 resume 标记
- 重发动作确认后任务继续推进到完成
"""

import unittest

from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine
from mission.扫码读取 import ScanResult, ScanType


class RecoveryReissueTests(unittest.TestCase):
    def valid_scan(self) -> ScanResult:
        payload = (
            '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],'
            '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}'
        )
        return ScanResult(ScanType.TASK_QR, payload, True, "test")

    def machine_in_s2(self) -> MissionStateMachine:
        """构造一个停在 S2_FIRST_BATCH、在途动作为 load_first_material 的机器。"""
        machine = MissionStateMachine()
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": self.valid_scan()}), 2)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        return machine

    def test_recovery_reissues_interrupted_load_first_material(self):
        machine = self.machine_in_s2()
        self.assertEqual(machine.in_flight.name, "load_first_material")

        # 安全故障进入恢复
        actions = machine.handle(MissionEvent("safety_fault", {"fault": "x"}), 3)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        self.assertTrue(any(action.name == "recover" for action in actions))

        # 恢复成功：重发被中断的动作
        actions = machine.handle(MissionEvent("recovery_done"), 4)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)
        self.assertEqual([action.name for action in actions], ["load_first_material"])

        # 重发动作确认后正常推进
        machine.handle(MissionEvent("material_loaded"), 5)
        self.assertEqual(machine.state, MissionState.S3_PLACE_RING)
        self.assertEqual(machine.in_flight.name, "place_ring")

    def test_recovery_second_failure_does_not_override_resume_state(self):
        machine = self.machine_in_s2()
        machine.handle(MissionEvent("safety_fault", {"fault": "x"}), 3)
        self.assertEqual(machine.resume_state, MissionState.S2_FIRST_BATCH)

        # 恢复中再次失败：resume_state 不得被覆盖为 RECOVERY
        actions = machine.handle(MissionEvent("action_failed", {"error": "y"}), 4)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        self.assertEqual(machine.resume_state, MissionState.S2_FIRST_BATCH)
        self.assertTrue(any(action.name == "recover" for action in actions))

        # 恢复成功回到 S2（而不是卡死在 RECOVERY）
        machine.handle(MissionEvent("recovery_done"), 5)
        self.assertEqual(machine.state, MissionState.S2_FIRST_BATCH)

    def test_recovery_state_times_out_and_exhausts(self):
        # 现有实现未区分 recovery_timeout_ms：RECOVERY 态复用 state_timeout_ms。
        machine = MissionStateMachine(max_recovery_attempts=1, state_timeout_ms=50)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": self.valid_scan()}), 2)
        machine.handle(MissionEvent("safety_fault", {"fault": "x"}), 3)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        self.assertEqual(machine.recovery_attempts, 1)

        # RECOVERY 超过 state_timeout_ms：尝试耗尽 → FAILED + safe_stop
        actions = machine.tick(200)
        self.assertEqual(machine.state, MissionState.FAILED)
        self.assertTrue(any(action.name == "safe_stop" for action in actions))

    def test_recovery_without_in_flight_returns_marker(self):
        machine = MissionStateMachine(max_scan_retries=1)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        self.assertIsNone(machine.in_flight)

        # S1 扫码连续失败进入恢复（此时无在途硬件动作）
        machine.handle(MissionEvent("task_scanned", {
            "scan": ScanResult(ScanType.TASK_QR, "{}", False, "test")
        }), 2)
        self.assertEqual(machine.state, MissionState.RECOVERY)

        actions = machine.handle(MissionEvent("recovery_done"), 3)
        self.assertEqual(machine.state, MissionState.S1_READ_TASK)
        self.assertEqual([action.name for action in actions], ["resume_state"])

    def test_recovery_reissue_continues_to_completion(self):
        machine = MissionStateMachine()
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": self.valid_scan()}), 2)
        machine.handle(MissionEvent("material_loaded"), 3)
        self.assertEqual(machine.state, MissionState.S3_PLACE_RING)
        self.assertEqual(machine.in_flight.name, "place_ring")

        # 下发 place_ring 时通信丢失
        machine.handle(MissionEvent("communication_lost", {"error": "x"}), 4)
        self.assertEqual(machine.state, MissionState.RECOVERY)

        actions = machine.handle(MissionEvent("recovery_done"), 5)
        self.assertEqual(machine.state, MissionState.S3_PLACE_RING)
        self.assertEqual([action.name for action in actions], ["place_ring"])

        # 重发 place_ring 确认后任务继续推进到完成
        machine.handle(MissionEvent("ring_placed"), 6)
        self.assertEqual(machine.state, MissionState.S4_SECOND_BATCH_ASSEMBLY)
        machine.handle(MissionEvent("material_loaded"), 7)
        machine.handle(MissionEvent("assembly_done"), 8)
        machine.handle(MissionEvent("home_confirmed"), 9)
        self.assertEqual(machine.state, MissionState.COMPLETE)


if __name__ == "__main__":
    unittest.main()
