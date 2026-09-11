# -*- coding: utf-8 -*-
"""实机测试会话：统一保存事件、指标和扫码统计报告。"""

from __future__ import annotations

import csv
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional


class TestSession:
    """每次实机运行创建一个目录，避免覆盖历史验收证据。"""

    def __init__(self, root: Path, name: str, metadata: Dict[str, Any]) -> None:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.directory = root / f"{timestamp}_{name}"
        self.directory.mkdir(parents=True, exist_ok=False)
        self.events_path = self.directory / "events.jsonl"
        self.scans_path = self.directory / "scans.csv"
        self.summary_path = self.directory / "summary.json"
        self.started_at = time.time()
        self.counts: Counter[str] = Counter()
        self.write("session_started", metadata)

    def write(self, event: str, data: Optional[Dict[str, Any]] = None) -> None:
        record = {"timestamp": time.time(), "event": event, "data": data or {}}
        with self.events_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")

    def record_frame(self, elapsed_ms: float, target_count: int, dropped: bool = False,
                     colors: Optional[list[str]] = None) -> None:
        self.counts["frames"] += 1
        self.counts["dropped_frames"] += int(dropped)
        self.counts["targets_seen"] += int(target_count > 0)
        self.counts["elapsed_ms_total"] += elapsed_ms  # pyright: ignore[reportArgumentType]
        for color in colors or []:
            self.counts[f"targets_{color}"] += 1

    def record_scan(self, scan_type: str, raw_value: str, valid: bool, source: str, error: Optional[str]) -> None:
        new_file = not self.scans_path.exists()
        with self.scans_path.open("a", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=["timestamp", "type", "value", "valid", "source", "error"])
            if new_file:
                writer.writeheader()
            writer.writerow({"timestamp": time.time(), "type": scan_type, "value": raw_value,
                             "valid": valid, "source": source, "error": error or ""})
        self.counts["scans"] += 1
        self.counts["valid_scans"] += int(valid)
        self.counts[f"scan_{scan_type}"] += 1
        self.counts[f"valid_scan_{scan_type}"] += int(valid)

    def close(self, final_state: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        duration_s = time.time() - self.started_at
        frames = self.counts["frames"]
        summary = {
            "duration_s": round(duration_s, 3), "final_state": final_state, **dict(self.counts),
            "average_fps": round(frames / duration_s, 2) if duration_s else 0.0,
            "average_vision_ms": round(self.counts["elapsed_ms_total"] / frames, 3) if frames else 0.0,
            "scan_success_rate": round(self.counts["valid_scans"] / self.counts["scans"], 4)
            if self.counts["scans"] else None,
            **(extra or {}),
        }
        self.write("session_finished", summary)
        self.summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        return summary
