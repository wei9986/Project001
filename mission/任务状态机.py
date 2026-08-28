# -*- coding: utf-8 -*-
"""任务状态机：管理两批物料的 S0-S5 流程。调试时按"状态、事件、动作"查看日志。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from mission.扫码读取 import TaskInstruction, parse_task_qr
from mission.任务计划 import MaterialStatus, TaskPlan


class MissionState(str, Enum):
    S0_INIT = "S0_init"
    S1_READ_TASK = "S1_read_task"
    S2_FIRST_BATCH = "S2_first_batch"
    S3_PLACE_RING = "S3_place_ring"
    S4_SECOND_BATCH_ASSEMBLY = "S4_second_batch_assembly"
    S5_RETURN_HOME = "S5_return_home"
    COMPLETE = "complete"
    RECOVERY = "recovery"
    FAILED = "failed"


@dataclass(frozen=True)
class MissionEvent:
    name: str
    data: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class MissionAction:
    name: str
    data: Dict[str, Any]


class MissionStateMachine:
    """根据任务计划和物料状态推进流程。"""

    def __init__(self, state_timeout_ms: float = 5000.0, max_recovery_attempts: int = 2,
                 max_scan_retries: int = 3,
                 state_timeouts: Optional[Dict[str, float]] = None) -> None:
        if state_timeout_ms <= 0 or max_recovery_attempts < 0 or max_scan_retries < 0:
            raise ValueError("状态超时必须为正数，恢复次数和扫码重试次数不能为负数")
        self.state = MissionState.S0_INIT
        self.entered_ms = 0.0
        self.state_timeout_ms = state_timeout_ms
        self.state_timeouts: Dict[str, float] = dict(state_timeouts or {})
        for value, timeout in self.state_timeouts.items():
            if timeout <= 0:
                raise ValueError(f"状态 {value} 的超时必须是正数")
        self.max_recovery_attempts = max_recovery_attempts
        self.max_scan_retries = max_scan_retries
        self.recovery_attempts = 0
        self.scan_retries = 0
        self.plan: Optional[TaskPlan] = None
        self.current_material_id: Optional[str] = None
        self.resume_state = MissionState.S0_INIT
        self.in_flight: Optional[MissionAction] = None

    def _action(self, name: str, **data: Any) -> List[MissionAction]:
        return [MissionAction(name, data)]

    def _hardware_action(self, name: str, **data: Any) -> List[MissionAction]:
        action = MissionAction(name, data)
        self.in_flight = action
        return [action]

    def _transition(self, state: MissionState, now_ms: float) -> None:
        self.state = state
        self.entered_ms = now_ms

    def handle(self, event: MissionEvent, now_ms: float) -> List[MissionAction]:
        try:
            return self._handle_impl(event, now_ms)
        except (ValueError, KeyError, RuntimeError, TypeError, AttributeError) as exc:
            return self._recover(now_ms, f"状态机异常: {exc}")

    def _handle_impl(self, event: MissionEvent, now_ms: float) -> List[MissionAction]:
        data = event.data or {}
        if event.name == "reset":
            self.__init__(self.state_timeout_ms, self.max_recovery_attempts,
                          self.max_scan_retries, self.state_timeouts)
            return self._action("reset_outputs")
        if event.name in {"action_failed", "communication_lost", "safety_fault"}:
            return self._recover(now_ms, event.name)
        if self.state == MissionState.S0_INIT and event.name == "start":
            return self._hardware_action("self_test")
        if self.state == MissionState.S0_INIT and event.name == "self_test_ok":
            self._transition(MissionState.S1_READ_TASK, now_ms)
            self.in_flight = None
            return self._action("read_task_qr")
        if self.state == MissionState.S1_READ_TASK and event.name == "task_scanned":
            try:
                instruction = parse_task_qr(data["scan"])
                plan = TaskPlan(instruction)
            except (TypeError, ValueError, KeyError, AttributeError) as exc:
                self.scan_retries += 1
                if self.scan_retries >= self.max_scan_retries:
                    return self._recover(now_ms, f"任务码连续 {self.scan_retries} 次解析失败: {exc}")
                return []
            self.scan_retries = 0
            self.plan = plan
            self._transition(MissionState.S2_FIRST_BATCH, now_ms)
            return self._load_next_first(now_ms)
        if self.state == MissionState.S2_FIRST_BATCH and event.name == "material_loaded":
            record = self._require_current()
            self.plan.update(record.instruction.material_id, MaterialStatus.ON_ROBOT)
            self.in_flight = None
            self._transition(MissionState.S3_PLACE_RING, now_ms)
            return self._hardware_action("place_ring", material=record.instruction.__dict__)
        if self.state == MissionState.S3_PLACE_RING and event.name == "ring_placed":
            record = self._require_current()
            self.plan.update(record.instruction.material_id, MaterialStatus.RING_PLACED)
            self.in_flight = None
            if self.plan.next_pending("first") is not None:
                self._transition(MissionState.S2_FIRST_BATCH, now_ms)
                return self._load_next_first(now_ms)
            self._transition(MissionState.S4_SECOND_BATCH_ASSEMBLY, now_ms)
            return self._load_next_second(now_ms)
        if self.state == MissionState.S4_SECOND_BATCH_ASSEMBLY and event.name == "material_loaded":
            record = self._require_current()
            self.plan.update(record.instruction.material_id, MaterialStatus.ON_ROBOT)
            self.in_flight = None
            return self._hardware_action("assemble_material", material=record.instruction.__dict__)
        if self.state == MissionState.S4_SECOND_BATCH_ASSEMBLY and event.name == "assembly_done":
            record = self._require_current()
            self.plan.update(record.instruction.material_id, MaterialStatus.RING_PLACED)
            self.plan.update(record.instruction.material_id, MaterialStatus.ASSEMBLED)
            self.in_flight = None
            if self.plan.next_pending("second") is not None:
                return self._load_next_second(now_ms)
            self._transition(MissionState.S5_RETURN_HOME, now_ms)
            return self._hardware_action("return_home")
        if self.state == MissionState.S5_RETURN_HOME and event.name == "home_confirmed":
            self._transition(MissionState.COMPLETE, now_ms)
            self.in_flight = None
            return self._action("task_complete", task_id=self.plan.task_id if self.plan else None)
        if self.state == MissionState.RECOVERY and event.name == "recovery_done":
            self._transition(self.resume_state, now_ms)
            if self.resume_state == MissionState.S1_READ_TASK:
                self.scan_retries = 0
            if self.in_flight is not None:
                return [self.in_flight]
            if self.state == MissionState.S0_INIT:
                return self._hardware_action("self_test")
            return self._action("resume_state", state=self.state.value)
        if self.state == MissionState.RECOVERY and event.name == "recovery_failed":
            self._transition(MissionState.FAILED, now_ms)
            return self._action("safe_stop", reason="异常恢复失败")
        return []

    def _require_current(self):
        if self.plan is None or self.current_material_id is None:
            raise RuntimeError("当前没有可执行物料")
        return next(record for record in self.plan.records if record.instruction.material_id == self.current_material_id)

    def _load_next_first(self, now_ms: float) -> List[MissionAction]:
        record = self.plan.next_pending("first") if self.plan else None
        if record is None:
            self._transition(MissionState.S4_SECOND_BATCH_ASSEMBLY, now_ms)
            return self._load_next_second(now_ms)
        self.current_material_id = record.instruction.material_id
        return self._hardware_action("load_first_material", material=record.instruction.__dict__)

    def _load_next_second(self, now_ms: float) -> List[MissionAction]:
        record = self.plan.next_pending("second") if self.plan else None
        if record is None:
            self._transition(MissionState.S5_RETURN_HOME, now_ms)
            return self._hardware_action("return_home")
        self.current_material_id = record.instruction.material_id
        return self._hardware_action("load_second_material", material=record.instruction.__dict__)

    def _recover(self, now_ms: float, reason: str) -> List[MissionAction]:
        if self.state in (MissionState.COMPLETE, MissionState.FAILED):
            return []
        if self.recovery_attempts >= self.max_recovery_attempts:
            self._transition(MissionState.FAILED, now_ms)
            return self._action("safe_stop", reason=reason)
        self.recovery_attempts += 1
        if self.state != MissionState.RECOVERY:
            self.resume_state = self.state
            if self.state == MissionState.S1_READ_TASK:
                self.scan_retries = 0
        self._transition(MissionState.RECOVERY, now_ms)
        return self._action("recover", attempt=self.recovery_attempts, reason=reason)

    def tick(self, now_ms: float) -> List[MissionAction]:
        if self.state in (MissionState.S0_INIT, MissionState.COMPLETE, MissionState.FAILED):
            return []
        timeout_ms = self.state_timeouts.get(self.state.value, self.state_timeout_ms)
        if now_ms - self.entered_ms <= timeout_ms:
            return []
        return self._recover(now_ms, f"状态 {self.state.value} 超时")
