# -*- coding: utf-8 -*-
"""第 11–15 项：官方任务码仿真。抓 12、放 9、码垛 3。空上报不放行。"""

import unittest

from app.任务牌 import TaskBoard
from mission.初赛流程 import CompetitionFlow
from mission.扫码读取 import parse_task_code
from mission.任务计划 import MaterialStatus, TaskPlan
from mission.任务状态机 import MissionEvent, MissionState, MissionStateMachine
from mission.行驶许可 import can_leave_with_gripper
from protocol.串口协议 import GRIPPER_OPEN, ZoneId

OFFICIAL = "156+123+516+231"


def plan() -> TaskPlan:
    return TaskPlan(parse_task_code(OFFICIAL))


def occupied(slot: int) -> int:
    return 1 << (slot - 1)


def drive(flow: CompetitionFlow, steps, commands, events):
    guard = 0
    while guard < 80:
        guard += 1
        if flow.awaiting_turntable:
            steps = flow.offer_turntable(True, True)
            continue
        if flow.awaiting_color:
            steps = flow.offer_color(flow.expected_color())
            continue
        if not steps or flow.blocked or flow.finished:
            break
        step = steps[0]
        commands.append((step.name, step.data["slot"], step.data["color"], step.data["zone"], step.data["ring"], step.data["batch"]))
        if step.name in ("GOTO", "HOME"):
            steps = flow.travel_done(0)
        elif step.name == "PICK_TO_SLOT":
            steps = flow.confirm(occupied(step.data["slot"]), GRIPPER_OPEN)
        else:
            steps = flow.confirm(0, GRIPPER_OPEN)
        events.extend(flow.take_events())
        if flow.blocked or flow.finished:
            break
    return steps


class CompetitionFlowTests(unittest.TestCase):
    def test_full_run_counts_and_home(self):
        flow = CompetitionFlow(plan(), still_speed_mm_s=30)
        board = TaskBoard()
        board.show_task(OFFICIAL)
        commands = []
        events = []
        drive(flow, flow.start(), commands, events)
        for name in events:
            board.apply_event(name)
        self.assertEqual(board.grab_count, 12)
        self.assertEqual(board.place_count, 9)
        self.assertEqual(flow.stack_ok, 3)
        self.assertTrue(flow.finished)
        self.assertTrue(flow.home_slow)
        self.assertEqual(commands[-1][0], "HOME")
        self.assertEqual(commands[-1][3], int(ZoneId.HOME_A))
        picks = [item for item in commands if item[0] == "PICK_TO_SLOT"]
        self.assertEqual([item[2] for item in picks[:3]], [1, 5, 6])
        self.assertEqual([item[4] for item in picks[:3]], [0, 0, 0])
        self.assertEqual([item[5] for item in picks[:3]], [1, 1, 1])

    def test_source_only_is_three_grabs(self):
        flow = CompetitionFlow(plan())
        events = []
        steps = flow.start()
        self.assertEqual(flow.offer_turntable(False, True), [])
        steps = flow.offer_turntable(True, True)
        grabs = 0
        guard = 0
        while grabs < 3 and guard < 10:
            guard += 1
            if flow.awaiting_turntable:
                steps = flow.offer_turntable(True, True)
            self.assertTrue(steps)
            step = steps[0]
            self.assertEqual(step.name, "PICK_TO_SLOT")
            steps = flow.confirm(occupied(step.data["slot"]), GRIPPER_OPEN)
            events.extend(flow.take_events())
            grabs += 1
        self.assertEqual(grabs, 3)
        self.assertEqual(events.count("slot_loaded_from_source"), 3)
        self.assertEqual(events.count("released_on_ring"), 0)
        self.assertEqual(steps[0].name, "GOTO")
        self.assertEqual(steps[0].data["zone"], int(ZoneId.COARSE))

    def test_empty_status_blocks_next_pick_and_goto(self):
        flow = CompetitionFlow(plan())
        flow.start()
        self.assertEqual(flow.offer_turntable(True, None), [])
        first = flow.offer_turntable(True, True)[0]
        self.assertEqual(first.name, "PICK_TO_SLOT")
        self.assertEqual(flow.confirm(None, None), [])
        self.assertFalse(can_leave_with_gripper(flow.plan))
        self.assertEqual(flow.phase, "load_source")

    def test_partial_departs_after_one_slot(self):
        flow = CompetitionFlow(plan(), depart_policy="partial")
        flow.start()
        step = flow.offer_turntable(True, True)[0]
        nxt = flow.confirm(occupied(step.data["slot"]), GRIPPER_OPEN)
        self.assertEqual(nxt[0].name, "GOTO")
        self.assertEqual(nxt[0].data["zone"], int(ZoneId.COARSE))

    def test_gripper_holding_blocks_goto(self):
        flow = CompetitionFlow(plan())
        flow.start()
        flow.offer_turntable(True, True)
        self.assertFalse(can_leave_with_gripper(flow.plan))
        self.assertEqual(flow._goto(int(ZoneId.COARSE), "goto_coarse"), [])

    def test_wrong_lower_piece_does_not_stack(self):
        flow = CompetitionFlow(plan(), still_speed_mm_s=30)
        commands = []
        steps = flow.start()
        blocked = None
        guard = 0
        while guard < 80:
            guard += 1
            if flow.awaiting_turntable:
                steps = flow.offer_turntable(True, True)
                continue
            if flow.awaiting_color:
                steps = flow.offer_color(flow.expected_color())
                continue
            if not steps:
                break
            step = steps[0]
            commands.append(step.name)
            if step.name == "GOTO" and flow.phase == "goto_staging" and flow.batch == 2:
                lower = next(record for record in flow.plan.records if record.instruction.material_id == "F2")
                lower.status = MaterialStatus.PLACED_COARSE
                blocked = flow.travel_done(0)
                break
            if step.name in ("GOTO", "HOME"):
                steps = flow.travel_done(0)
            elif step.name == "PICK_TO_SLOT":
                steps = flow.confirm(occupied(step.data["slot"]), GRIPPER_OPEN)
            else:
                steps = flow.confirm(0, GRIPPER_OPEN)
        self.assertEqual(blocked, [])
        self.assertEqual(flow.blocked, "stack_blocked")
        self.assertNotIn("STACK_FROM_SLOT", commands)

    def test_selected_home_is_last_travel(self):
        flow = CompetitionFlow(plan(), home_zone=int(ZoneId.HOME_B), still_speed_mm_s=30)
        commands = []
        drive(flow, flow.start(), commands, [])
        self.assertEqual(commands[-1], ("HOME", 0, 0, int(ZoneId.HOME_B), 0, 0))

    def test_machine_reaches_complete_only_when_slow(self):
        machine = MissionStateMachine(still_speed_mm_s=30)
        machine.handle(MissionEvent("start"), 0)
        machine.handle(MissionEvent("self_test_ok"), 1)
        from mission.扫码读取 import ScanType, validate_scan
        machine.handle(MissionEvent("task_scanned", {"scan": validate_scan(ScanType.TASK_QR, OFFICIAL, "test")}), 2)
        self.assertTrue(machine.competition_mode)
        board = TaskBoard()
        guard = 0
        while machine.state != MissionState.COMPLETE and guard < 80:
            guard += 1
            if machine.flow.awaiting_turntable:
                machine.handle(MissionEvent("turntable_ready", {"stable": True, "window_open": True}), guard)
                continue
            if machine.flow.awaiting_color:
                machine.handle(MissionEvent("target_color", {"color": machine.flow.expected_color()}), guard)
                continue
            action = machine.in_flight
            self.assertIsNotNone(action)
            if action.name in ("GOTO", "HOME"):
                actions = machine.handle(MissionEvent("travel_done", {"speed_mm_s": 0}), guard)
            elif action.name == "PICK_TO_SLOT":
                actions = machine.handle(
                    MissionEvent("action_done", {"slot_mask": occupied(action.data["slot"]), "gripper": GRIPPER_OPEN}),
                    guard,
                )
            else:
                actions = machine.handle(
                    MissionEvent("action_done", {"slot_mask": 0, "gripper": GRIPPER_OPEN}),
                    guard,
                )
            for name in machine.flow.take_events():
                board.apply_event(name)
            if machine.flow.blocked:
                break
            if actions and actions[0].name == "task_complete":
                break
        self.assertEqual(machine.state, MissionState.COMPLETE)
        self.assertEqual((board.grab_count, board.place_count, machine.flow.stack_ok), (12, 9, 3))


if __name__ == "__main__":
    unittest.main()
