# -*- coding: utf-8 -*-
"""视觉任务状态机：管理搜索、靠近、精定位、抓取和放置。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from vision.精定位 import FineLocalizer, LocalizationStatus


class TaskState(str, Enum):
    IDLE = "idle"
    SEARCHING = "searching"
    APPROACHING = "approaching"
    FINE_LOCALIZING = "fine_localizing"
    GRASPING = "grasping"
    VERIFYING_GRASP = "verifying_grasp"
    PLACING = "placing"
    VERIFYING_PLACE = "verifying_place"
    COMPLETE = "complete"
    RECOVERY = "recovery"
    FAILED = "failed"


@dataclass(frozen=True)
class TaskEvent:
    name: str
    data: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class TaskAction:
    name: str
    data: Dict[str, Any]


class TaskStateMachine:
    """只消费事件并输出动作，不直接控制硬件。"""

    def __init__(self, fine_localizer: FineLocalizer, state_timeout_ms: float = 5000.0,
                 max_recovery_attempts: int = 2) -> None:
        if state_timeout_ms <= 0 or max_recovery_attempts < 0:
            raise ValueError("状态超时必须为正数，恢复次数不能为负数")
        self.fine_localizer = fine_localizer
        self.state = TaskState.IDLE
        self.entered_ms = 0.0
        self.state_timeout_ms = state_timeout_ms
        self.max_recovery_attempts = max_recovery_attempts
        self.recovery_attempts = 0
        self.selected_target: Optional[Dict[str, Any]] = None

    def _action(self, name: str, **data: Any) -> List[TaskAction]:
        return [TaskAction(name, data)]

    def _transition(self, state: TaskState, now_ms: float) -> None:
        self.state = state
        self.entered_ms = now_ms

    def _find_target(self, vision_result: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """从有效且有世界坐标的目标中选取置信度最高者。"""
        targets = [target for target in vision_result.get("targets", [])
                   if target.get("valid") and target.get("world_mm")]
        return max(targets, key=lambda target: target.get("confidence", 0.0), default=None)

    def handle(self, event: TaskEvent, now_ms: float) -> List[TaskAction]:
        """仅在收到预期确认事件后推进一步。"""
        data = event.data or {}
        if event.name == "reset":
            self._transition(TaskState.IDLE, now_ms)
            self.recovery_attempts = 0
            self.selected_target = None
            return self._action("reset_outputs")
        if self.state == TaskState.IDLE and event.name == "start":
            self._transition(TaskState.SEARCHING, now_ms)
            return self._action("search_target")
        if self.state == TaskState.SEARCHING and event.name == "vision_update":
            target = self._find_target(data)
            if target is None:
                return []
            self.selected_target = target
            self._transition(TaskState.APPROACHING, now_ms)
            return self._action("approach_target", target=target)
        if self.state == TaskState.APPROACHING and event.name == "arrived_near_target":
            if self.selected_target is None:
                return self._recover(now_ms, "没有已选目标")
            target_id = self.selected_target.get("target_id")
            self.fine_localizer.target_id = target_id
            self.fine_localizer.reset(now_ms)
            self._transition(TaskState.FINE_LOCALIZING, now_ms)
            return self._action("request_fine_localization", target_id=target_id)
        if self.state == TaskState.FINE_LOCALIZING and event.name == "vision_update":
            localization = self.fine_localizer.update(data, now_ms)
            if localization.status == LocalizationStatus.STABLE:
                self._transition(TaskState.GRASPING, now_ms)
                return self._action("grasp", coordinate_mm=localization.coordinate_mm)
            if localization.status == LocalizationStatus.JUMP_REJECTED:
                # P1-9：坐标跳变非终止，继续采集；与 FineLocalizer 语义一致。
                return [TaskAction("fine_localization_progress", {"localization": localization.to_dict()})]
            if localization.status in (
                LocalizationStatus.TIMEOUT,
                LocalizationStatus.LOST,
                LocalizationStatus.NO_COORDINATE,
            ):
                # 目标无世界坐标（如未完成平面标定）时继续等待无意义，进入恢复流程。
                return self._recover(now_ms, localization.reason)
            return [TaskAction("fine_localization_progress", {"localization": localization.to_dict()})]
        if self.state == TaskState.GRASPING and event.name == "grasp_done":
            self._transition(TaskState.VERIFYING_GRASP, now_ms)
            return self._action("verify_grasp")
        if self.state == TaskState.VERIFYING_GRASP:
            if event.name == "grasp_verified":
                self._transition(TaskState.PLACING, now_ms)
                return self._action("place_target", target=self.selected_target)
            if event.name == "grasp_failed":
                return self._recover(now_ms, "抓取确认失败")
        if self.state == TaskState.PLACING and event.name == "place_done":
            self._transition(TaskState.VERIFYING_PLACE, now_ms)
            return self._action("verify_place")
        if self.state == TaskState.VERIFYING_PLACE:
            if event.name == "place_verified":
                self._transition(TaskState.COMPLETE, now_ms)
                return self._action("task_complete")
            if event.name == "place_failed":
                return self._recover(now_ms, "放置确认失败")
        if self.state == TaskState.RECOVERY and event.name == "recovery_done":
            self._transition(TaskState.SEARCHING, now_ms)
            return self._action("search_target")
        if self.state == TaskState.RECOVERY and event.name == "recovery_failed":
            self._transition(TaskState.FAILED, now_ms)
            return self._action("safe_stop", reason="异常恢复失败")
        return []

    def _recover(self, now_ms: float, reason: str) -> List[TaskAction]:
        """执行有限次数恢复，超限后安全停止。"""
        if self.recovery_attempts >= self.max_recovery_attempts:
            self._transition(TaskState.FAILED, now_ms)
            return self._action("safe_stop", reason=reason)
        self.recovery_attempts += 1
        self._transition(TaskState.RECOVERY, now_ms)
        return self._action("recover", attempt=self.recovery_attempts, reason=reason)

    def tick(self, now_ms: float) -> List[TaskAction]:
        """根据运行时钟检查操作超时。"""
        if self.state in (TaskState.IDLE, TaskState.COMPLETE, TaskState.FAILED):
            return []
        if now_ms - self.entered_ms <= self.state_timeout_ms:
            return []
        return self._recover(now_ms, f"状态 {self.state.value} 超时")
