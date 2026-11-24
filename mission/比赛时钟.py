# -*- coding: utf-8 -*-
"""比赛时钟：3 分钟从发令起算，静止 15 秒结束本轮，等转盘时为 23 秒。

状态机的约 5 秒超时只表示这一步卡住了，不在这里判断。
"""

from __future__ import annotations

from typing import Optional

MATCH_DURATION_MS = 180_000
STALL_MS = 15_000
TURNTABLE_STALL_MS = 23_000

STOP_REASON = {
    "stalled": 1,
    "time_up": 2,
    "start_missed": 3,
}


class MatchClock:
    """发令时刻固定。一键启动晚了，只缩短剩余时间，不重新获得 180 秒。"""

    def __init__(self, still_speed_mm_s: float, start_window_s: Optional[float] = None) -> None:
        if isinstance(still_speed_mm_s, bool) or not isinstance(still_speed_mm_s, (int, float)):
            raise TypeError("静止速度门槛必须是数值")
        if still_speed_mm_s <= 0:
            raise ValueError("静止速度门槛必须来自底盘实测，且大于 0")
        if start_window_s is not None:
            if isinstance(start_window_s, bool) or not isinstance(start_window_s, (int, float)):
                raise TypeError("start_window_s 必须是数值或空")
            if start_window_s <= 0:
                raise ValueError("start_window_s 必须为正数")
        self.still_speed_mm_s = float(still_speed_mm_s)
        self.start_window_s = None if start_window_s is None else float(start_window_s)
        self.clock_start_ms: Optional[float] = None
        self.robot_started_ms: Optional[float] = None
        self._still_since: Optional[float] = None
        self.outcome: Optional[str] = None

    def clear_round(self) -> None:
        """第二轮开始前清掉发令时刻。静止门槛保留，下一次发令才重新计时。"""
        self.clock_start_ms = None
        self.robot_started_ms = None
        self._still_since = None
        self.outcome = None

    def start(self, clock_start_ms: float) -> None:
        """记下裁判发令时刻。不从一键启动重新计 180 秒。"""
        self.clock_start_ms = float(clock_start_ms)
        self.robot_started_ms = None
        self._still_since = None
        self.outcome = None

    def note_robot_start(self, now_ms: float) -> None:
        """一键启动。晚按不改发令时刻。"""
        if self.clock_start_ms is None:
            raise RuntimeError("尚未发令，不能一键启动")
        if self.robot_started_ms is None:
            self.robot_started_ms = float(now_ms)

    def remaining_ms(self, now_ms: float) -> float:
        """距离 3 分钟结束还剩多少毫秒，从发令时刻算。"""
        if self.clock_start_ms is None:
            raise RuntimeError("尚未发令")
        return max(0.0, MATCH_DURATION_MS - (float(now_ms) - self.clock_start_ms))

    def tick(self, now_ms: float, speed_mm_s: Optional[float], waiting_turntable: bool, finished: bool = False) -> str:
        """返回 running、stalled、time_up 或 start_missed。

        完成后不再把停车记成停滞。没有速度样本时不累计静止时间。
        """
        if self.outcome is not None:
            return self.outcome
        if finished or self.clock_start_ms is None:
            return "running"
        now = float(now_ms)
        elapsed = now - self.clock_start_ms
        if self.robot_started_ms is None:
            if self.start_window_s is not None and elapsed > self.start_window_s * 1000.0:
                self.outcome = "start_missed"
            return self.outcome or "running"
        if elapsed >= MATCH_DURATION_MS:
            self.outcome = "time_up"
            return self.outcome
        if speed_mm_s is None:
            return "running"
        if abs(float(speed_mm_s)) < self.still_speed_mm_s:
            if self._still_since is None:
                self._still_since = now
            limit = TURNTABLE_STALL_MS if waiting_turntable else STALL_MS
            if now - self._still_since >= limit:
                self.outcome = "stalled"
        else:
            self._still_since = None
        return self.outcome or "running"
