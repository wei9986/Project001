# -*- coding: utf-8 -*-
"""任务计划：校验任务数据并限制物料状态变化。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from mission.扫码读取 import MaterialInstruction, TaskInstruction


class MaterialStatus(str, Enum):
    PENDING = "pending"
    ON_ROBOT = "on_robot"
    RING_PLACED = "ring_placed"
    ASSEMBLED = "assembled"
    FAILED = "failed"


@dataclass(frozen=True)
class StationPose:
    station_id: str
    x_mm: float
    y_mm: float
    yaw_deg: float


@dataclass(frozen=True)
class TaskRuntimeConfig:
    barcode_mapping: Dict[str, Tuple[float, float]]
    stations: Dict[str, StationPose]

    @classmethod
    def from_file(cls, path: str) -> "TaskRuntimeConfig":
        with Path(path).open("r", encoding="utf-8") as file:
            data = json.load(file)
        mapping = data.get("barcode_mapping", {})
        stations_data = data.get("stations", {})
        if not isinstance(mapping, dict) or not isinstance(stations_data, dict):
            raise ValueError("任务配置.json 必须包含 barcode_mapping 和 stations 对象")
        stations = {}
        for station_id, value in stations_data.items():
            if not isinstance(value, dict) or not all(key in value for key in ("x_mm", "y_mm", "yaw_deg")):
                raise ValueError(f"站点配置不完整: {station_id}")
            stations[station_id] = StationPose(station_id, float(value["x_mm"]), float(value["y_mm"]), float(value["yaw_deg"]))
        for code, value in mapping.items():
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(f"barcode_mapping.{code} 必须是长度为 2 的数值列表")
            try:
                float(value[0]), float(value[1])
            except (TypeError, ValueError):
                raise ValueError(f"barcode_mapping.{code} 坐标值无法转为浮点数: {value}")
        return cls(
            {str(code): (float(value[0]), float(value[1])) for code, value in mapping.items()},
            stations,
        )


@dataclass
class MaterialRecord:
    instruction: MaterialInstruction
    batch: str
    status: MaterialStatus = MaterialStatus.PENDING


class TaskPlan:
    """记录两批物料，防止重复和错误顺序。"""

    def __init__(self, instruction: TaskInstruction) -> None:
        self.task_id = instruction.task_id
        if not instruction.first_batch:
            raise ValueError("任务必须至少包含一件第一批物料")
        self.records: List[MaterialRecord] = [
            *(MaterialRecord(item, "first") for item in instruction.first_batch),
            *(MaterialRecord(item, "second") for item in instruction.second_batch),
        ]
        allowed_ring_codes = {"1", "2", "3"}
        for batch_name, batch_items in (("first_batch", instruction.first_batch),
                                        ("second_batch", instruction.second_batch)):
            batch_codes = [item.ring_code for item in batch_items]
            for code in batch_codes:
                if str(code) not in allowed_ring_codes:
                    raise ValueError(f"任务二维码 {batch_name} 存在非法 ring_code: {code!r}（仅允许 1/2/3）")
            if len(set(batch_codes)) != len(batch_codes):
                raise ValueError(f"任务二维码 {batch_name} 中存在重复 ring_code")
        material_ids = [item.material_id for item in (*instruction.first_batch, *instruction.second_batch)]
        if len(set(material_ids)) != len(material_ids):
            raise ValueError("任务二维码中存在重复 material_id")

    def next_pending(self, batch: str) -> Optional[MaterialRecord]:
        return next((record for record in self.records if record.batch == batch and record.status == MaterialStatus.PENDING), None)

    def update(self, material_id: str, status: MaterialStatus) -> MaterialRecord:
        record = next((item for item in self.records if item.instruction.material_id == material_id), None)
        if record is None:
            raise KeyError(f"未知物料: {material_id}")
        allowed = {
            MaterialStatus.PENDING: {MaterialStatus.ON_ROBOT, MaterialStatus.FAILED},
            MaterialStatus.ON_ROBOT: {MaterialStatus.RING_PLACED, MaterialStatus.FAILED},
            MaterialStatus.RING_PLACED: {MaterialStatus.ASSEMBLED, MaterialStatus.FAILED},
            MaterialStatus.ASSEMBLED: set(),
            MaterialStatus.FAILED: set(),
        }
        if status not in allowed[record.status]:
            raise ValueError(f"非法物料状态转换: {record.status.value} -> {status.value}")
        record.status = status
        return record
