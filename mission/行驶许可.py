"""第 12 项：手上有料，或本趟还没进槽，就不许离开原料区。"""

from __future__ import annotations

from typing import Optional

from mission.任务计划 import MaterialStatus, TaskPlan

DEPART_FULL_BATCH = "full_batch"
DEPART_PARTIAL = "partial"


def can_leave_with_gripper(plan: TaskPlan) -> bool:
    """任一物料还在夹具里，就拒绝 GOTO。"""
    return all(record.status != MaterialStatus.IN_GRIPPER for record in plan.records)


def can_depart_source(plan: TaskPlan, policy: str = DEPART_FULL_BATCH, batch: int = 1) -> bool:
    """full_batch 要本批全部进槽；partial 至少一件进槽，且手上不能有料。"""
    if not can_leave_with_gripper(plan):
        return False
    group = [record for record in plan.records if record.instruction.batch == batch]
    loaded = [record for record in group if record.status == MaterialStatus.ON_SLOT]
    if policy == DEPART_PARTIAL:
        return len(loaded) >= 1
    if policy != DEPART_FULL_BATCH:
        raise ValueError(f"未知出发策略: {policy}")
    return len(group) > 0 and len(loaded) == len(group)


def slot_bit(slot: int) -> int:
    """1 号槽对应 bit0。"""
    return 1 << (int(slot) - 1)


def pick_landed(slot_mask: Optional[int], gripper_open: Optional[bool], slot: int) -> bool:
    """抓取落地：该槽有料，并且夹具已经松开。"""
    if slot_mask is None or gripper_open is None:
        return False
    return bool(gripper_open) and bool(int(slot_mask) & slot_bit(slot))


def place_landed(slot_mask: Optional[int], gripper_open: Optional[bool], slot: int) -> bool:
    """放下落地：该槽已空，并且夹具已经松开。"""
    if slot_mask is None or gripper_open is None:
        return False
    return bool(gripper_open) and not bool(int(slot_mask) & slot_bit(slot))
