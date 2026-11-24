# -*- coding: utf-8 -*-
"""恢复配额语义回归测试（F-01 / F-03）。

背景：``recovery_attempts`` 原先只在 ``__init__`` 归零，任何成功推进都不重置，
导致 ``max_recovery_attempts`` 被实现成「整场比赛总配额」而非「连续失败配额」。
长任务里三次互不相关的瞬时故障（即便每次都恢复成功）就会把机器人永久停机。

覆盖：
- 成功推进状态后归还恢复配额（连续故障不再累积）
- 仅 recovery_done 不得归还配额（防止恢复刷屏绕过配额）
- 恢复态内连续失败仍能正常耗尽配额进入 FAILED
- 视觉任务状态机同样归还配额
- S0 自检阶段相机丢失可超时进入恢复（F-03）
"""

import json
import tempfile
import unittest
from pathlib import Path

from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine
from mission.扫码读取 import ScanResult, ScanType
from mission.视觉任务状态机 import TaskEvent, TaskState, TaskStateMachine
from vision.精定位 import FineLocalizer


def valid_scan() -> ScanResult:
    payload = (
        '{"task_id":"T1","first_batch":[{"material_id":"F1","color":"red","ring_code":"1"}],'
        '"second_batch":[{"material_id":"S1","color":"blue","ring_code":"2"}]}'
    )
    return ScanResult(ScanType.TASK_QR, payload, True, "test")


def _target(target_id: int, world_mm: list[float], color: str = "red") -> dict:
    """构造一条带世界坐标的有效视觉目标。"""
    return {
        "target_id": target_id,
        "color": color,
        "center": [100, 100],
        "valid": True,
        "world_mm": world_mm,
        "confidence": 0.9,
    }


def machine_in_s2() -> MissionStateMachine:
    """构造停在 S2、在途动作为 load_first_material 的状态机。"""
    machine = MissionStateMachine(state_timeout_ms=5000, max_recovery_attempts=2)
    machine.handle(MissionEvent("start"), 0)
    machine.handle(MissionEvent("self_test_ok"), 1)
    machine.handle(MissionEvent("task_scanned", {"scan": valid_scan()}), 2)
    return machine


class RecoveryQuotaTests(unittest.TestCase):
    def test_successful_progress_returns_recovery_quota(self):
        """连续三次瞬时故障且每次都恢复成功，任务不得被判定为 FAILED。

        旧实现下第三次故障必然进入 FAILED——这正是比赛场上「跑着跑着自己
        停死」的根因。

        注意配额归还时机：``recovery_done`` 只把状态机送回 resume_state 并
        **重发**被中断的硬件动作；直到该动作被确认（material_loaded）才算
        真正推进，此时才归还配额。
        """
        machine = machine_in_s2()

        for attempt in range(1, 4):
            machine.handle(
                MissionEvent("communication_lost", {"error": f"transient_{attempt}"}), 10 * attempt
            )
            self.assertEqual(machine.state, MissionState.RECOVERY)

            machine.handle(MissionEvent("recovery_done"), 10 * attempt + 1)
            self.assertEqual(
                machine.state,
                MissionState.S2_FIRST_BATCH,
                f"第 {attempt} 次故障恢复后应回到 S2，实际 {machine.state.value}",
            )

            # 重发的 load_first_material 被确认：这才算实质推进，归还配额。
            machine.handle(MissionEvent("material_loaded"), 10 * attempt + 2)
            self.assertEqual(
                machine.recovery_attempts,
                0,
                f"第 {attempt} 次成功推进后 recovery_attempts 应归零，实际 {machine.recovery_attempts}",
            )
            # 推进到 S3 后又派发 place_ring；为下一轮迭代复位到 S2 场景。
            self.assertEqual(machine.state, MissionState.S3_PLACE_RING)
            machine.handle(MissionEvent("ring_placed"), 10 * attempt + 3)
            machine.handle(MissionEvent("material_loaded"), 10 * attempt + 4)
            machine.handle(MissionEvent("assembly_done"), 10 * attempt + 5)
            machine.handle(MissionEvent("home_confirmed"), 10 * attempt + 6)
            self.assertEqual(machine.state, MissionState.COMPLETE)
            # 重置回 S2 继续验证下一轮瞬时故障。
            machine = machine_in_s2()

    def test_quota_not_returned_by_recovery_done_alone(self):
        """仅 recovery_done（重发动作尚未确认）不得归还配额。"""
        machine = machine_in_s2()
        machine.handle(MissionEvent("communication_lost", {"error": "x"}), 3)
        self.assertEqual(machine.recovery_attempts, 1)

        machine.handle(MissionEvent("recovery_done"), 4)
        self.assertEqual(
            machine.recovery_attempts,
            1,
            "重发动作未确认前不得归还配额，否则恢复刷屏可绕过次数限制",
        )

    def test_recovery_done_alone_does_not_return_quota(self):
        """恢复态内反复 recovery_done 不得刷掉配额，否则恢复次数限制形同虚设。"""
        machine = MissionStateMachine(max_recovery_attempts=1, state_timeout_ms=10_000)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": valid_scan()}), 2)

        machine.handle(MissionEvent("communication_lost", {"error": "first"}), 3)
        self.assertEqual(machine.recovery_attempts, 1)

        # 配额已用满：再次故障必须直接 FAILED，不能被 recovery_done 洗掉。
        actions = machine.handle(MissionEvent("action_failed", {"error": "second"}), 4)
        self.assertEqual(machine.state, MissionState.FAILED)
        self.assertTrue(any(action.name == "safe_stop" for action in actions))

    def test_consecutive_failures_exhaust_quota_without_success(self):
        """没有任何成功推进时，配额仍必须能正常耗尽（保留原有保护语义）。"""
        machine = MissionStateMachine(max_recovery_attempts=2, state_timeout_ms=5000)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        machine.handle(MissionEvent("task_scanned", {"scan": valid_scan()}), 2)

        machine.handle(MissionEvent("action_failed"), 3)
        self.assertEqual(machine.recovery_attempts, 1)
        # 恢复态内再失败：不覆盖 resume_state，且继续消耗配额。
        machine.handle(MissionEvent("action_failed"), 4)
        self.assertEqual(machine.state, MissionState.RECOVERY)
        self.assertEqual(machine.resume_state, MissionState.S2_FIRST_BATCH)

        machine.handle(MissionEvent("action_failed"), 5)
        self.assertEqual(machine.state, MissionState.FAILED)

    def test_quota_resets_across_full_task(self):
        """走完整流程并在中途插入故障，最终仍应 complete。"""
        machine = machine_in_s2()

        machine.handle(MissionEvent("safety_fault", {"fault": "tof"}), 3)
        machine.handle(MissionEvent("recovery_done"), 4)
        machine.handle(MissionEvent("material_loaded"), 5)

        machine.handle(MissionEvent("communication_lost", {"error": "x"}), 6)
        machine.handle(MissionEvent("recovery_done"), 7)
        machine.handle(MissionEvent("ring_placed"), 8)

        machine.handle(MissionEvent("material_loaded"), 9)
        machine.handle(MissionEvent("assembly_done"), 10)
        machine.handle(MissionEvent("home_confirmed"), 11)
        self.assertEqual(machine.state, MissionState.COMPLETE)


class VisionRecoveryQuotaTests(unittest.TestCase):
    """视觉任务状态机通过状态超时进入恢复（无 action_failed 入口）。"""

    @staticmethod
    def machine_ready_to_recover(max_attempts: int = 2) -> TaskStateMachine:
        """构造停在 SEARCHING、等待目标的视觉状态机。"""
        machine = TaskStateMachine(
            FineLocalizer(color="red"), state_timeout_ms=100, max_recovery_attempts=max_attempts
        )
        machine.handle(TaskEvent("start"), 0)
        self_assert = machine.state
        assert self_assert == TaskState.SEARCHING, self_assert
        return machine

    def test_vision_machine_returns_quota_after_progress(self):
        """连续多次超时恢复成功后，配额不得累积（两套状态机此前各错一遍）。

        视觉状态机 ``recovery_done`` 后回到 SEARCHING 并重发 ``search_target``；
        直到 ``vision_update`` 找到目标才算实质推进，此时归还配额。
        """
        machine = self.machine_ready_to_recover()

        for attempt in range(1, 4):
            actions = machine.tick(200 * attempt)
            self.assertEqual(
                machine.state,
                TaskState.RECOVERY,
                f"第 {attempt} 次超时应进入 RECOVERY，实际 {machine.state.value}",
            )
            self.assertTrue(any(action.name == "recover" for action in actions))

            machine.handle(TaskEvent("recovery_done"), 200 * attempt + 1)
            self.assertEqual(machine.state, TaskState.SEARCHING)

            # 实质推进：搜索到目标 -> 归还配额。
            machine.handle(
                TaskEvent("vision_update", {"targets": [_target(1, [100.0, 200.0])]}),
                200 * attempt + 2,
            )
            self.assertEqual(machine.state, TaskState.APPROACHING)
            self.assertEqual(
                machine.recovery_attempts,
                0,
                f"第 {attempt} 次成功推进后应归零，实际 {machine.recovery_attempts}",
            )
            # 复位到 SEARCHING 继续验证下一轮超时。
            machine = self.machine_ready_to_recover()

    def test_vision_consecutive_failures_still_exhaust(self):
        """恢复态内无成功推进时，配额仍能耗尽进入 FAILED。"""
        machine = self.machine_ready_to_recover(max_attempts=1)

        machine.tick(200)
        self.assertEqual(machine.state, TaskState.RECOVERY)
        self.assertEqual(machine.recovery_attempts, 1)

        # RECOVERY 态再次超时：配额耗尽 → FAILED + safe_stop。
        actions = machine.tick(400)
        self.assertEqual(machine.state, TaskState.FAILED)
        self.assertTrue(any(action.name == "safe_stop" for action in actions))


class S0CameraLossTests(unittest.TestCase):
    """F-03：S0 自检阶段不得豁免超时，否则相机丢失会永久空转。"""

    def test_s0_tick_reports_timeout_and_recovers(self):
        machine = MissionStateMachine(state_timeout_ms=1000, max_recovery_attempts=2)
        machine.handle(MissionEvent("start"), 0)
        self.assertEqual(machine.state, MissionState.S0_INIT)

        # 远超超时时间仍停在 S0（自检 DONE 从未到达）。
        actions = machine.tick(100_000)
        self.assertEqual(
            machine.state,
            MissionState.RECOVERY,
            "S0 超时应进入恢复流程，而不是被 tick 永久忽略",
        )
        self.assertTrue(any(action.name == "recover" for action in actions))

    def test_s0_timeout_uses_dedicated_budget(self):
        """S0 自检握手应在秒级完成，使用独立短超时而非全局默认。"""
        machine = MissionStateMachine(state_timeout_ms=100_000, max_recovery_attempts=2)
        machine.handle(MissionEvent("start"), 0)

        # 全局超时是 100s，但 S0 有独立短超时，10s 内即应触发恢复。
        actions = machine.tick(10_000)
        self.assertTrue(
            any(action.name == "recover" for action in actions),
            "S0 应使用独立短超时（默认 3s），不应沿用 100s 全局值",
        )

    def test_s0_recovery_reissues_self_test(self):
        """S0 进入恢复并成功后可重发自检，不卡死。"""
        machine = MissionStateMachine(state_timeout_ms=1000, max_recovery_attempts=2)
        machine.handle(MissionEvent("start"), 0)
        machine.tick(100_000)
        self.assertEqual(machine.state, MissionState.RECOVERY)

        actions = machine.handle(MissionEvent("recovery_done"), 100_001)
        self.assertEqual(machine.state, MissionState.S0_INIT)
        self.assertTrue(any(action.name == "self_test" for action in actions))


class SafeStopPreemptionTests(unittest.TestCase):
    """F-04：safe_stop 必须能抢占悬挂命令，且失败必须留痕。

    协议同一时刻只允许一条未完成命令。若 F407 已 ACK 但迟迟不 DONE，
    pending 会一直悬挂，此时 safe_stop 会被 send_command 拒绝 —— 结果是
    F407 收不到任何停止指令。这是比赛现场拔线场景的关键路径。
    """

    def _runtime(self, tmp: Path, *, fail_write: bool):
        from app.实机运行 import LiveRuntime
        from app.测试会话 import TestSession
        from app.虚拟设备 import VirtualCamera, VirtualScanner
        from app.视觉应用 import ApplicationConfig
        from protocol.串口协议 import MessageType

        class StubController:
            """应答 ACK 但永不应答 DONE，制造悬挂 pending。"""

            def __init__(self) -> None:
                self.written: list[bytes] = []
                self.fail_next = fail_write

            def write(self, data: bytes) -> int:
                if self.fail_next:
                    self.fail_next = False
                    raise RuntimeError("simulated write failure")
                self.written.append(bytes(data))
                return len(data)

            def read_available(self) -> bytes:
                return b""

            def close(self) -> None:
                pass

        config = ApplicationConfig(task_path=tmp / "task.json", log_path=tmp / "actions.jsonl")
        controller = StubController()
        runtime = LiveRuntime(
            config,
            {
                "camera": {"source": "0", "reconnect_delay_s": 0.0},
                "scanner": {"mode": "serial", "port": "unused"},
                "controller": {"port": "unused", "baudrate": 115200, "reconnect_delay_s": 0.0},
                "protocol": {
                    "heartbeat_interval_ms": 99_999,
                    "offline_timeout_ms": 99_999,
                    "ack_timeout_ms": 99_999,
                    "done_timeout_ms": 99_999,
                    "max_retries": 0,
                },
                "colors": ["red"],
                "color_config": "config/颜色追踪配置.json",
                "station_config": "config/站点标签.json",
                "fine_localization": "config/精定位配置.json",
                "use_fine_localization": False,
                "calibration": None,
                "sensors": {"tof_distance_mm": 120.0, "limit_triggered": False, "grasp_confirmed": True},
                "loop": {"sleep_s": 0.0},
                "display": False,
                "record_video": None,
                "archive_error_samples": False,
            },
            TestSession(tmp / "session", "safestop", {"mode": "live"}),
            camera=VirtualCamera([__import__("numpy").zeros((8, 8, 3), dtype="uint8")]),
            controller=controller,
            scanner=VirtualScanner(),
        )
        return runtime, controller, MessageType

    def test_safe_stop_preempts_pending_command(self):
        """已有悬挂 pending 时，safe_stop 仍必须下发成功。"""
        from mission.任务状态机 import MissionAction

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            runtime, controller, message_type = self._runtime(tmp, fail_write=False)
            try:
                # 先用一条普通命令占住 pending（stub 从不应答 DONE）。
                runtime.protocol.send_command(message_type.PING, b"{}", 0.0)
                self.assertIsNotNone(runtime.protocol.pending)
                self.assertFalse(runtime.protocol.pending.completed)

                ok = runtime._send_controller_packet(
                    MissionAction("safe_stop", {"reason": "test"}), message_type.STOP, 1.0
                )
                self.assertTrue(ok, "safe_stop 应抢占悬挂 pending 后成功下发")
                from protocol.串口协议 import FrameParser

                frames = []
                parser = FrameParser()
                for pkt in controller.written:
                    frames.extend(parser.feed(pkt))
                self.assertTrue(
                    any(frame.message_type == message_type.STOP for frame in frames),
                    "控制器应实际收到 STOP 帧",
                )
            finally:
                runtime.close()

    def test_safe_stop_failure_is_logged(self):
        """safe_stop 写失败必须落盘 safe_stop_failed，不得静默。"""
        from mission.任务状态机 import MissionAction

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            runtime, controller, message_type = self._runtime(tmp, fail_write=True)
            try:
                ok = runtime._send_controller_packet(
                    MissionAction("safe_stop", {"reason": "test"}), message_type.STOP, 1.0
                )
                self.assertFalse(ok)
                events = [
                    json.loads(line)
                    for line in runtime.session.events_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                kinds = [event.get("event") for event in events]
                self.assertIn(
                    "safe_stop_failed",
                    kinds,
                    f"safe_stop 失败必须记录 safe_stop_failed，实际事件: {kinds}",
                )
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()