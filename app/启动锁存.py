# -*- coding: utf-8 -*-
"""一键启动锁存。调试按下和上电不消耗本轮机会。

clock_start 是裁判发令，robot_start 是车上标识过的那一下。
无线和键盘不能调用 robot_start。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from mission.比赛时钟 import MatchClock


class LatchState(str, Enum):
    DEBUG = "debug"
    STAGED = "staged"
    CLOCK_ON = "clock_on"
    RUNNING = "running"
    ENDED = "ended"


class ClockStartHandoff:
    """仅把发令时刻交给比赛时钟。比赛模式走 StartLatch。"""

    def __init__(self, clock: MatchClock) -> None:
        self.clock = clock

    def clock_start(self, clock_start_ms: float) -> None:
        self.clock.start(clock_start_ms)

    def robot_start(self, now_ms: float) -> None:
        self.clock.note_robot_start(now_ms)


class RisingEdges:
    """把电平变成上升沿。去抖毫秒来自按键实测；为空时不额外等待。"""

    def __init__(self, debounce_ms: Optional[float] = None) -> None:
        if debounce_ms is not None and (isinstance(debounce_ms, bool) or not isinstance(debounce_ms, (int, float)) or debounce_ms < 0):
            raise ValueError("debounce_ms 必须是非负实测值或空")
        self.debounce_ms = None if debounce_ms is None else float(debounce_ms)
        self._high: dict[str, bool] = {}
        self._since: dict[str, float] = {}

    def update(self, name: str, level: bool, now_ms: float) -> bool:
        high = bool(level)
        was_high = self._high.get(name, False)
        self._high[name] = high
        if not high:
            self._since.pop(name, None)
            return False
        if self.debounce_ms is None or self.debounce_ms == 0:
            return not was_high
        if name not in self._since:
            if was_high:
                return False
            self._since[name] = float(now_ms)
            return False
        if float(now_ms) - self._since[name] >= self.debounce_ms:
            self._since.pop(name, None)
            return True
        return False


class StartLatch:
    """每轮只接受一次车上的上升沿。发令时限未填写时，比赛模式拒绝武装。"""

    def __init__(
        self,
        *,
        competition: bool,
        start_window_s: Optional[float],
        clock: Optional[MatchClock] = None,
    ) -> None:
        if competition and start_window_s is None:
            raise ValueError("比赛模式未填写 start_window_s，拒绝武装")
        if start_window_s is not None and start_window_s <= 0:
            raise ValueError("start_window_s 必须为正数")
        self.competition = competition
        self.start_window_s = None if start_window_s is None else float(start_window_s)
        self.clock = clock
        self.state = LatchState.STAGED if competition else LatchState.DEBUG
        self.clock_start_ms: Optional[float] = None
        self.accepted_robot_start = False

    def mission_armed(self) -> bool:
        """调试态可以运动。就位、等发令、本轮结束后不再自主运动。"""
        return self.state in (LatchState.DEBUG, LatchState.RUNNING)

    def allows_config_write(self) -> bool:
        """一键启动之后不再保存参数、调参或接收无线写入。"""
        return self.state != LatchState.RUNNING

    def allows_autonomous_motion(self) -> bool:
        """放到出发位之后、发车之前，不再自主运动。"""
        return self.state in (LatchState.DEBUG, LatchState.RUNNING)

    def debug_press(self) -> bool:
        """调试按钮不进入自检，也不消耗本轮机会。"""
        return False

    def stage(self) -> None:
        """放到抽签出发位。只在调试态接受。"""
        if self.state == LatchState.DEBUG:
            self.state = LatchState.STAGED

    def clock_start(self, now_ms: float) -> bool:
        """发令。只在已经放到出发位、并且时限已填写时武装。"""
        if self.state != LatchState.STAGED:
            return False
        if self.start_window_s is None:
            raise ValueError("start_window_s 未填写，拒绝武装")
        self.clock_start_ms = float(now_ms)
        self.accepted_robot_start = False
        self.state = LatchState.CLOCK_ON
        if self.clock is not None:
            self.clock.start(self.clock_start_ms)
        return True

    def robot_start(self, now_ms: float) -> bool:
        """车上的一键启动。时限内的第一次上升沿才进入自检。"""
        if self.state != LatchState.CLOCK_ON or self.clock_start_ms is None:
            return False
        if now_ms - self.clock_start_ms > self.start_window_s * 1000.0:
            self.state = LatchState.ENDED
            return False
        self.state = LatchState.RUNNING
        self.accepted_robot_start = True
        if self.clock is not None:
            self.clock.note_robot_start(now_ms)
        return True

    def tick(self, now_ms: float) -> Optional[str]:
        """发令后超过启动时限仍未按下，本轮结束。"""
        if self.state != LatchState.CLOCK_ON or self.clock_start_ms is None or self.start_window_s is None:
            return None
        if now_ms - self.clock_start_ms > self.start_window_s * 1000.0:
            self.state = LatchState.ENDED
            return "start_missed"
        return None

    def end(self) -> None:
        """本轮因停滞、到时或未启动结束。上电不会调用这里。"""
        if self.state in (LatchState.CLOCK_ON, LatchState.RUNNING):
            self.state = LatchState.ENDED

    def next_round(self) -> bool:
        """显式进入下一轮。上电本身不完成这次复位。"""
        if self.state != LatchState.ENDED:
            return False
        self.state = LatchState.STAGED
        self.clock_start_ms = None
        self.accepted_robot_start = False
        return True
