# -*- coding: utf-8 -*-
"""扫码读取：统一处理任务二维码、外接扫码器和 Code128 条码。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np


class ScanType(str, Enum):
    TASK_QR = "task_qr"
    CODE128 = "code128"


@dataclass(frozen=True)
class ScanResult:
    scan_type: ScanType
    raw_value: str
    valid: bool
    source: str
    error: Optional[str] = None


class ExternalScannerParser:
    """解析 GM865 或 USB 扫码器的换行数据。"""

    def __init__(self, scan_type: ScanType, max_line_length: int = 512) -> None:
        if max_line_length < 1:
            raise ValueError("max_line_length 必须为正数")
        self.scan_type = scan_type
        self.max_line_length = max_line_length
        self.buffer = bytearray()

    def feed(self, data: bytes) -> List[ScanResult]:
        """接收任意长度字节块，只输出完整扫码结果。"""
        self.buffer.extend(data)
        results: List[ScanResult] = []
        while b"\n" in self.buffer:
            line, _, remainder = self.buffer.partition(b"\n")
            self.buffer = bytearray(remainder)
            if len(line) > self.max_line_length:
                results.append(ScanResult(self.scan_type, "", False, "external", "扫码数据过长"))
                continue
            value = line.rstrip(b"\r").decode("utf-8", errors="replace").strip()
            results.append(validate_scan(self.scan_type, value, "external"))
        if len(self.buffer) > self.max_line_length:
            self.buffer.clear()
            results.append(ScanResult(self.scan_type, "", False, "external", "扫码缓冲区溢出"))
        return results


def validate_scan(scan_type: ScanType, value: str, source: str) -> ScanResult:
    """校验扫码文本并保留来源和错误信息。"""
    if not value:
        return ScanResult(scan_type, value, False, source, "扫码结果为空")
    if scan_type == ScanType.CODE128 and not re.fullmatch(r"[0-9A-Za-z._-]{1,64}", value):
        return ScanResult(scan_type, value, False, source, "Code128 内容包含非法字符")
    return ScanResult(scan_type, value, True, source)


class QRCodeReader:
    """使用 OpenCV 作为摄像头二维码识别备用方案。"""

    def __init__(self) -> None:
        self.detector = cv2.QRCodeDetector()

    def read(self, frame: np.ndarray) -> List[ScanResult]:
        """返回零条或多条有效二维码结果。"""
        try:
            ok, values, _, _ = self.detector.detectAndDecodeMulti(frame)
        except cv2.error:
            ok, values = False, []
        if not ok or values is None:
            value, _, _ = self.detector.detectAndDecode(frame)
            values = [value] if value else []
        return [validate_scan(ScanType.TASK_QR, value, "camera") for value in values if value]


@dataclass(frozen=True)
class MaterialInstruction:
    material_id: str
    color: str
    ring_code: str


@dataclass(frozen=True)
class TaskInstruction:
    task_id: str
    first_batch: Tuple[MaterialInstruction, ...]
    second_batch: Tuple[MaterialInstruction, ...]


def parse_task_qr(result: ScanResult) -> TaskInstruction:
    """解析任务二维码 JSON，并拒绝缺失字段。"""
    if result.scan_type != ScanType.TASK_QR or not result.valid:
        raise ValueError("不是有效任务二维码")
    try:
        payload = json.loads(result.raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("任务二维码必须是 JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("task_id"), str):
        raise ValueError("任务二维码缺少 task_id")

    def parse_batch(name: str) -> Tuple[MaterialInstruction, ...]:
        values = payload.get(name, [])
        if not isinstance(values, list):
            raise ValueError(f"{name} 必须是数组")
        records = []
        for item in values:
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) for key in ("material_id", "color", "ring_code")):
                raise ValueError(f"{name} 存在不完整物料记录")
            if item["color"] not in {"red", "green", "blue"}:
                raise ValueError(f"不支持的物料颜色: {item['color']}")
            records.append(MaterialInstruction(item["material_id"], item["color"], item["ring_code"]))
        return tuple(records)

    return TaskInstruction(payload["task_id"], parse_batch("first_batch"), parse_batch("second_batch"))


class BarcodeWorldMapper:
    """把有效条码映射为已标定的世界坐标。"""

    def __init__(self, mapping: Dict[str, Sequence[float]]) -> None:
        self.mapping = {str(code): (float(value[0]), float(value[1])) for code, value in mapping.items()}

    def lookup(self, result: ScanResult) -> Tuple[float, float]:
        """查询一条有效且已配置条码的坐标。"""
        if result.scan_type != ScanType.CODE128 or not result.valid:
            raise ValueError("不是有效 Code128 结果")
        if result.raw_value not in self.mapping:
            raise KeyError(f"未配置条码坐标: {result.raw_value}")
        return self.mapping[result.raw_value]
