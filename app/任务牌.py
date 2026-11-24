# -*- coding: utf-8 -*-
"""车上上部任务牌。字高由显示设备保证，这里只送任务码和两个计数。"""

from __future__ import annotations

from dataclasses import dataclass
ROUND_RUNNING = "进行中"
ROUND_STALLED = "停滞结束"
ROUND_TIME_UP = "超时结束"
ROUND_START_MISSED = "未启动结束"
ROUND_COMPLETE = "完成"

_ROUND_BY_REASON = {
    "stalled": ROUND_STALLED,
    "time_up": ROUND_TIME_UP,
    "start_missed": ROUND_START_MISSED,
}


@dataclass
class BoardPayload:
    task_code: str
    grab_count: int
    place_count: int

    def text(self) -> str:
        return f"{self.task_code} {self.grab_count} {self.place_count}"


class TaskBoard:
    """保存完整任务码和两个得分计数。码垛不进放置数。"""

    def __init__(self) -> None:
        self.task_code = ""
        self.grab_count = 0
        self.place_count = 0
        self.status = ROUND_RUNNING

    def show_task(self, task_code: str) -> BoardPayload:
        self.task_code = task_code
        return self.payload()

    def payload(self) -> BoardPayload:
        return BoardPayload(self.task_code, self.grab_count, self.place_count)

    def apply_event(self, name: str) -> None:
        """只按第 15 项的事件加数。码垛事件不加放置数。"""
        if name in ("slot_loaded_from_source", "slot_loaded_from_coarse"):
            self.grab_count += 1
        elif name == "released_on_ring":
            self.place_count += 1

    def set_round_end(self, reason: str) -> None:
        self.status = _ROUND_BY_REASON.get(reason, self.status)

    def mark_complete(self) -> None:
        self.status = ROUND_COMPLETE

