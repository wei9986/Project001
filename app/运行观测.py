"""运行观测：把每轮状态写入 JSONL，并生成汇总指标。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable

if TYPE_CHECKING:
    from app.运行循环 import RuntimeSnapshot


class RuntimeSnapshotLogger:
    """每轮追加一条 JSON 状态记录（持有文件句柄，避免每帧重复打开）。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def write(self, snapshot: "RuntimeSnapshot") -> None:
        """写入用于排查问题的公开状态字段。"""
        self._file.write(json.dumps(snapshot.__dict__, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._file.flush()

    def close(self) -> None:
        """关闭日志文件；重复调用是安全的。"""
        if not self._file.closed:
            self._file.close()


def summarize_snapshots(snapshots: Iterable["RuntimeSnapshot"]) -> Dict[str, object]:
    """把多轮状态汇总为可比较的指标。"""
    values = list(snapshots)
    if not values:
        raise ValueError("至少需要一条运行时状态记录")
    return {
        "cycles": len(values),
        "duration_ms": values[-1].timestamp_ms - values[0].timestamp_ms,
        "frames_with_targets": sum(item.target_count > 0 for item in values),
        "frames_with_stations": sum(item.station_count > 0 for item in values),
        "average_target_count": sum(item.target_count for item in values) / len(values),
        "average_station_count": sum(item.station_count for item in values) / len(values),
        "final_state": values[-1].state,
    }
