"""第 11–15 项：官方任务码的装车、放置、回抓、码垛和回终点。

调试用的 JSON 任务不走这里。转盘停留时间仍未测量，本模块不编造等待毫秒。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from mission.任务计划 import MaterialRecord, MaterialStatus, TaskPlan
from mission.行驶许可 import can_depart_source, can_leave_with_gripper, pick_landed, place_landed
from protocol.串口协议 import (
    COLOR_BLACK,
    COLOR_BLUE,
    COLOR_GREEN,
    COLOR_LIGHT_BLUE,
    COLOR_RED,
    COLOR_YELLOW,
    GRIPPER_OPEN,
    ZoneId,
)

COLOR_ID = {
    "red": COLOR_RED,
    "yellow": COLOR_YELLOW,
    "blue": COLOR_BLUE,
    "green": COLOR_GREEN,
    "black": COLOR_BLACK,
    "light_blue": COLOR_LIGHT_BLUE,
}


@dataclass
class FlowStep:
    name: str
    data: Dict[str, int]


class CompetitionFlow:
    """一批三件：原料进槽、粗加工放下再抓回、暂存放下或码垛，然后回家。"""

    def __init__(
        self,
        plan: TaskPlan,
        depart_policy: str = "full_batch",
        home_zone: int = int(ZoneId.HOME_A),
        still_speed_mm_s: Optional[float] = None,
    ) -> None:
        self.plan = plan
        self.depart_policy = depart_policy
        self.home_zone = int(home_zone)
        self.still_speed_mm_s = still_speed_mm_s
        self.batch = 1
        self.phase = "load_source"
        self.cursor = 0
        self.pending: Optional[FlowStep] = None
        self.events: List[str] = []
        self.stack_ok = 0
        self.blocked: Optional[str] = None
        self.finished = False
        self.home_slow = False
        self.last_travel_zone: Optional[int] = None
        # 平面放置和粗加工回取要先看见这件的颜色。没看见就等，看错就停。
        self.awaiting_color = False
        self.awaiting_turntable = False

    def start(self) -> List[FlowStep]:
        self.phase = "load_source"
        self.cursor = 0
        return self._hold_for_turntable()

    def offer_turntable(self, stable: bool, window_open: Optional[bool]) -> List[FlowStep]:
        """转盘没停稳，或剩余时间未知、不够，就不发原料区抓取。"""
        if not self.awaiting_turntable or self.blocked or self.finished:
            return []
        if not stable or window_open is not True:
            return []
        self.awaiting_turntable = False
        return self._issue_pick(int(ZoneId.SOURCE), ring=0)

    def expected_color(self) -> Optional[str]:
        if not self.awaiting_color or self.blocked:
            return None
        return self._current().instruction.color

    def offer_color(self, color: Optional[str]) -> List[FlowStep]:
        """颜色一致才发出放下或回取。空颜色继续等。颜色不对则停，不改抓旁边那件。"""
        if not self.awaiting_color or self.blocked or self.finished:
            return []
        if color is None:
            return []
        record = self._current()
        if color != record.instruction.color:
            self.blocked = "color_mismatch"
            self.awaiting_color = False
            self.pending = None
            return []
        self.awaiting_color = False
        if self.phase == "pick_coarse":
            return self._issue_pick(int(ZoneId.COARSE))
        if self.phase == "place_coarse":
            return self._issue_place(int(ZoneId.COARSE), "ring_code")
        return self._issue_place(int(ZoneId.STAGING), "staging_ring")

    def take_events(self) -> List[str]:
        found = list(self.events)
        self.events.clear()
        return found

    def confirm(self, slot_mask: Optional[int], gripper: Optional[int]) -> List[FlowStep]:
        """用最近一次槽位和夹具上报决定是否前进。空上报不产生下一条命令。"""
        step = self.pending
        if step is None or self.blocked or self.finished:
            return []
        gripper_open = None if gripper is None else int(gripper) == GRIPPER_OPEN
        slot = int(step.data["slot"])
        if step.name == "PICK_TO_SLOT":
            if not pick_landed(slot_mask, gripper_open, slot):
                return []
            record = self._current()
            if self.phase == "load_source":
                self.plan.update(record.instruction.material_id, MaterialStatus.ON_SLOT)
                self.events.append("slot_loaded_from_source")
            else:
                self.plan.update(record.instruction.material_id, MaterialStatus.ON_SLOT_RETURN)
                self.events.append("slot_loaded_from_coarse")
            self.cursor += 1
            return self._after_pick()
        if step.name in ("PLACE_FROM_SLOT", "STACK_FROM_SLOT"):
            if not place_landed(slot_mask, gripper_open, slot):
                return []
            record = self._current()
            if step.name == "STACK_FROM_SLOT":
                self.plan.update(record.instruction.material_id, MaterialStatus.STACKED)
                self.stack_ok += 1
                self.events.append("stacked")
            elif self.phase == "place_coarse":
                self.plan.update(record.instruction.material_id, MaterialStatus.PLACED_COARSE)
                self.events.append("released_on_ring")
            else:
                self.plan.update(record.instruction.material_id, MaterialStatus.PLACED_STAGING)
                self.events.append("released_on_ring")
            self.cursor += 1
            return self._after_release(step.name)
        return []

    def travel_done(self, speed_mm_s: Optional[int] = None) -> List[FlowStep]:
        step = self.pending
        if step is None or step.name not in ("GOTO", "HOME") or self.blocked:
            return []
        self.last_travel_zone = int(step.data["zone"])
        if self.phase == "goto_coarse":
            self.phase = "place_coarse"
            self.cursor = 0
            return self._hold_for_color()
        if self.phase == "goto_staging":
            self.phase = "place_or_stack"
            self.cursor = 0
            if self.batch == 1:
                return self._hold_for_color()
            return self._issue_stage()
        if self.phase == "goto_source":
            self.batch = 2
            self.phase = "load_source"
            self.cursor = 0
            return self._hold_for_turntable()
        if self.phase == "goto_home":
            self.finished = True
            self.pending = None
            if (
                self.still_speed_mm_s is not None
                and speed_mm_s is not None
                and abs(int(speed_mm_s)) <= float(self.still_speed_mm_s)
            ):
                self.home_slow = True
            return []
        return []

    def _group(self) -> List[MaterialRecord]:
        return [record for record in self.plan.records if record.instruction.batch == self.batch]

    def _current(self) -> MaterialRecord:
        return self._group()[self.cursor]

    def _issue_pick(self, zone: int, ring: Optional[int] = None) -> List[FlowStep]:
        record = self._current()
        self.plan.update(record.instruction.material_id, MaterialStatus.IN_GRIPPER)
        chosen_ring = int(record.instruction.ring_code) if ring is None else int(ring)
        return self._emit("PICK_TO_SLOT", record, zone, chosen_ring)

    def _issue_place(self, zone: int, ring_attr: str) -> List[FlowStep]:
        record = self._current()
        return self._emit("PLACE_FROM_SLOT", record, zone, int(getattr(record.instruction, ring_attr)))

    def _issue_stage(self) -> List[FlowStep]:
        record = self._current()
        if self.batch == 1:
            return self._issue_place(int(ZoneId.STAGING), "staging_ring")
        if self.plan.stack_target(record) is None:
            self.blocked = "stack_blocked"
            self.pending = None
            return []
        return self._emit(
            "STACK_FROM_SLOT", record, int(ZoneId.STAGING), int(record.instruction.staging_ring)
        )

    def _after_pick(self) -> List[FlowStep]:
        group = self._group()
        if self.phase == "load_source":
            if can_depart_source(self.plan, self.depart_policy, self.batch):
                return self._goto(int(ZoneId.COARSE), "goto_coarse")
            if self.cursor < len(group):
                return self._hold_for_turntable()
            return []
        if self.cursor < len(group):
            return self._hold_for_color()
        return self._goto(int(ZoneId.STAGING), "goto_staging")

    def _after_release(self, name: str) -> List[FlowStep]:
        group = self._group()
        if self.cursor < len(group):
            if name == "STACK_FROM_SLOT" or (self.phase == "place_or_stack" and self.batch == 2):
                return self._issue_stage()
            return self._hold_for_color()
        if self.phase == "place_coarse":
            self.phase = "pick_coarse"
            self.cursor = 0
            return self._hold_for_color()
        if self.batch == 1:
            return self._goto(int(ZoneId.SOURCE), "goto_source")
        return self._goto(self.home_zone, "goto_home")

    def _hold_for_turntable(self) -> List[FlowStep]:
        self.awaiting_turntable = True
        self.awaiting_color = False
        self.pending = None
        return []

    def _hold_for_color(self) -> List[FlowStep]:
        self.awaiting_color = True
        self.pending = None
        return []

    def _goto(self, zone: int, phase: str) -> List[FlowStep]:
        if not can_leave_with_gripper(self.plan):
            return []
        self.phase = phase
        name = "HOME" if phase == "goto_home" else "GOTO"
        step = FlowStep(name, {"slot": 0, "color": 0, "zone": int(zone), "ring": 0, "batch": 0 if name == "HOME" else self.batch})
        self.pending = step
        self.last_travel_zone = int(zone)
        return [step]

    def _emit(self, name: str, record: MaterialRecord, zone: int, ring: int) -> List[FlowStep]:
        step = FlowStep(name, {
            "slot": int(record.instruction.order),
            "color": COLOR_ID[record.instruction.color],
            "zone": int(zone),
            "ring": int(ring),
            "batch": int(self.batch),
        })
        self.pending = step
        return [step]
