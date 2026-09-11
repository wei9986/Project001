# -*- coding: utf-8 -*-
"""精定位离线四场景：stable / jump / timeout / no_calib。"""

from __future__ import annotations

import unittest

from mission.视觉任务状态机 import TaskEvent, TaskState, TaskStateMachine
from vision.精定位 import FineLocalizer, LocalizationStatus


def _vision(target_id: int, world_mm, *, valid: bool = True, confidence: float = 0.9) -> dict:
    return {
        "targets": [
            {
                "target_id": target_id,
                "valid": valid,
                "world_mm": world_mm,
                "confidence": confidence,
                "color": "red",
                "center": [100.0, 100.0],
            }
        ]
    }


class FineLocalizationScenarioTests(unittest.TestCase):
    def _enter_fine(self, machine: TaskStateMachine, target_id: int = 1) -> None:
        machine.handle(TaskEvent("start"), 0.0)
        machine.handle(TaskEvent("vision_update", _vision(target_id, [10.0, 20.0])), 1.0)
        machine.handle(TaskEvent("arrived_near_target"), 2.0)
        self.assertEqual(machine.state, TaskState.FINE_LOCALIZING)

    def test_fine_stable(self):
        localizer = FineLocalizer(
            required_samples=3,
            max_spread_mm=5.0,
            max_step_mm=30.0,
            timeout_ms=1500.0,
            lost_timeout_ms=300.0,
            target_id=1,
        )
        machine = TaskStateMachine(localizer, state_timeout_ms=5000.0)
        self._enter_fine(machine)
        for index, now in enumerate((10.0, 20.0, 30.0), start=1):
            actions = machine.handle(
                TaskEvent("vision_update", _vision(1, [10.0 + 0.1 * index, 20.0])),
                now,
            )
        self.assertEqual(machine.state, TaskState.GRASPING)
        self.assertTrue(any(action.name == "grasp" for action in actions))

    def test_fine_jump_does_not_recover(self):
        localizer = FineLocalizer(
            required_samples=5,
            max_spread_mm=5.0,
            max_step_mm=5.0,
            timeout_ms=5000.0,
            lost_timeout_ms=300.0,
            target_id=1,
        )
        machine = TaskStateMachine(localizer, state_timeout_ms=5000.0, max_recovery_attempts=2)
        self._enter_fine(machine)
        machine.handle(TaskEvent("vision_update", _vision(1, [0.0, 0.0])), 10.0)
        actions = machine.handle(TaskEvent("vision_update", _vision(1, [100.0, 0.0])), 20.0)
        self.assertEqual(machine.state, TaskState.FINE_LOCALIZING)
        self.assertEqual(machine.recovery_attempts, 0)
        self.assertTrue(any(action.name == "fine_localization_progress" for action in actions))
        localization = actions[0].data["localization"]
        self.assertEqual(localization["status"], LocalizationStatus.JUMP_REJECTED.value)

    def test_fine_timeout_recovers(self):
        localizer = FineLocalizer(
            required_samples=5,
            max_spread_mm=5.0,
            max_step_mm=30.0,
            timeout_ms=50.0,
            lost_timeout_ms=300.0,
            target_id=1,
        )
        machine = TaskStateMachine(localizer, state_timeout_ms=5000.0, max_recovery_attempts=2)
        self._enter_fine(machine)
        actions = machine.handle(TaskEvent("vision_update", _vision(1, [10.0, 20.0])), 10.0)
        actions = machine.handle(TaskEvent("vision_update", _vision(1, [10.1, 20.0])), 80.0)
        self.assertEqual(machine.state, TaskState.RECOVERY)
        self.assertTrue(any(action.name == "recover" for action in actions))

    def test_fine_no_calib_recovers(self):
        """无 world_mm（未标定）时精定位返回 NO_COORDINATE，状态机进入恢复。"""
        localizer = FineLocalizer(
            required_samples=3,
            max_spread_mm=5.0,
            max_step_mm=30.0,
            timeout_ms=1500.0,
            lost_timeout_ms=300.0,
            target_id=1,
        )
        machine = TaskStateMachine(localizer, state_timeout_ms=5000.0, max_recovery_attempts=2)
        # 搜索阶段需要 world_mm 才能选中目标；此处直接切入精定位状态
        machine.state = TaskState.FINE_LOCALIZING
        machine.entered_ms = 0.0
        machine.selected_target = {"target_id": 1, "valid": True, "world_mm": [1.0, 2.0]}
        localizer.reset(0.0)
        actions = machine.handle(
            TaskEvent(
                "vision_update",
                {"targets": [{"target_id": 1, "valid": True, "world_mm": None, "confidence": 0.9}]},
            ),
            10.0,
        )
        self.assertEqual(machine.state, TaskState.RECOVERY)
        self.assertTrue(any(action.name == "recover" for action in actions))


if __name__ == "__main__":
    unittest.main()
