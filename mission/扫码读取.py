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


# 任务码长度上限：与扫码器 max_line_length（默认 512）对齐并留余量。
# 超长任务码几乎必然是噪声或协议错位，直接判无效比喂给 JSON 解析更安全。
MAX_TASK_QR_LENGTH = 4096


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
    """校验扫码文本并保留来源和错误信息。

    TASK_QR 做轻量预校验（非空 + JSON 对象前缀 + 长度上限），但不在此解析
    JSON——完整结构与字段校验由 ``parse_task_qr`` 负责，保持分层。
    这样脏数据在扫码层就被判无效，``scan_retries`` 才反映真实的扫码质量，
    而不是被推迟成「JSON 解析失败」(F-05)。
    """
    if not value:
        return ScanResult(scan_type, value, False, source, "扫码结果为空")
    if scan_type == ScanType.CODE128 and not re.fullmatch(r"[0-9A-Za-z._-]{1,64}", value):
        return ScanResult(scan_type, value, False, source, "Code128 内容包含非法字符")
    if scan_type == ScanType.TASK_QR:
        if len(value) > MAX_TASK_QR_LENGTH:
            return ScanResult(scan_type, value, False, source,
                              f"任务码超长（{len(value)} > {MAX_TASK_QR_LENGTH}）")
        if not value.lstrip().startswith("{") and not re.fullmatch(r"[0-9]+(?:\+[0-9]+)+", value.strip()):
            return ScanResult(scan_type, value, False, source, "任务码必须是数字分组或 JSON")
    return ScanResult(scan_type, value, True, source)


class QRCodeReader:
    """摄像头二维码。原图失败后放大中部再解，同一串确认后只向上送一次。"""

    def __init__(self, confirm_frames: int = 1) -> None:
        if confirm_frames < 1:
            raise ValueError("confirm_frames 必须为正数")
        self.confirm_frames = confirm_frames
        self.detector = cv2.QRCodeDetector()
        self._streak = ""
        self._streak_count = 0

    def read(self, frame: np.ndarray) -> List[ScanResult]:
        """先解原图。失败则取中部，按 2 倍、4 倍最近邻放大再解。"""
        texts = self._decode(frame)
        if not texts:
            center = self._center(frame)
            for scale in (2, 4):
                enlarged = cv2.resize(center, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
                texts = self._decode(enlarged)
                if texts:
                    break
        if not texts:
            self._streak = ""
            self._streak_count = 0
            return []
        text = texts[0]
        if text == self._streak:
            self._streak_count += 1
        else:
            self._streak = text
            self._streak_count = 1
        if self._streak_count != self.confirm_frames:
            return []
        return [validate_scan(ScanType.TASK_QR, text, "camera")]

    def pending(self) -> bool:
        """同一串还没攒够确认帧。这时搜索不能把车开走。"""
        return 0 < self._streak_count < self.confirm_frames

    def _decode(self, image: np.ndarray) -> List[str]:
        try:
            ok, values, _, _ = self.detector.detectAndDecodeMulti(image)
        except cv2.error:
            ok, values = False, []
        found: List[str] = []
        if ok and values is not None:
            found.extend(value for value in values if value)
        if not found:
            value, _, _ = self.detector.detectAndDecode(image)
            if value:
                found.append(value)
        # 半截字符串不上送。完整四组码或完整 JSON 才保留。
        complete = []
        for value in found:
            text = value.strip()
            if re.fullmatch(r"\d{3}(?:\+\d{3}){3}", text) or (text.startswith("{") and text.endswith("}")):
                complete.append(text)
        return complete

    @staticmethod
    def _center(frame: np.ndarray) -> np.ndarray:
        height, width = frame.shape[:2]
        y0, y1 = height // 4, height - height // 4
        x0, x1 = width // 4, width - width // 4
        if y1 - y0 < 8 or x1 - x0 < 8:
            return frame
        return frame[y0:y1, x0:x1]


_COMPLETE_TASK = re.compile(r"\d{3}(?:\+\d{3}){3}")


def complete_task_text(text: Optional[str]) -> Optional[str]:
    """只留下完整四组任务码。半截字符串和只有三组的字符串丢掉。"""
    if text is None:
        return None
    stripped = text.strip()
    if _COMPLETE_TASK.fullmatch(stripped):
        return stripped
    return None


class QrSearch:
    """沿二维码板区域逐点读。读到完整任务码就停，不再走完剩下的点。"""

    def __init__(self, points: Sequence[Tuple[float, float]]) -> None:
        if not points:
            raise ValueError("搜索至少要有一个停靠点")
        self.points = tuple(points)
        self.index = 0
        self.found: Optional[str] = None

    def current(self) -> Optional[Tuple[float, float]]:
        if self.found is not None or self.index >= len(self.points):
            return None
        return self.points[self.index]

    def observe(self, text: Optional[str]) -> str:
        """返回 found、advance 或 exhausted。空文本表示这个点没有完整码。"""
        complete = complete_task_text(text)
        if complete is not None:
            self.found = complete
            return "found"
        self.index += 1
        if self.index >= len(self.points):
            return "exhausted"
        return "advance"


@dataclass(frozen=True)
class MaterialInstruction:
    material_id: str
    color: str
    ring_code: str
    staging_ring: str = ""
    batch: int = 0
    order: int = 0


class TaskCodeError(ValueError):
    """比赛任务码解析失败。code 与任务计划里的环位报错分开。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_COLOR_DIGITS = set("123456")
_RING_DIGITS = set("123")


def _symbol_to_color() -> Dict[str, str]:
    """颜色名字只读 config/task_color_test.json，和显示共用一份。"""
    from vision.配置加载 import project_root

    path = project_root() / "config" / "task_color_test.json"
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    mapping = data.get("task_code", {}).get("symbol_to_color", {})
    if not isinstance(mapping, dict):
        raise TaskCodeError("bad_color", "symbol_to_color 必须是对象")
    return {str(key): str(value) for key, value in mapping.items()}


def parse_task_code(scan: ScanResult | str) -> TaskInstruction:
    """比赛任务码的唯一解析入口。不接受 JSON。"""
    if isinstance(scan, ScanResult):
        if scan.scan_type != ScanType.TASK_QR or not scan.valid:
            raise TaskCodeError("bad_length", "不是有效任务二维码")
        text = scan.raw_value.strip()
    else:
        text = str(scan).strip()
    groups = text.split("+")
    if len(groups) != 4 or any(len(group) != 3 or not group.isdigit() for group in groups):
        raise TaskCodeError("bad_length", "任务码必须是四组三位数字，用 + 连接")
    colors_a, rings_a, colors_b, rings_b = groups
    if any(digit not in _COLOR_DIGITS for digit in colors_a + colors_b):
        raise TaskCodeError("bad_color", "第 1、3 组的颜色编号必须在 1..6")
    if any(digit not in _RING_DIGITS for digit in rings_a + rings_b):
        raise TaskCodeError("bad_ring", "第 2、4 组的环位必须在 1..3")
    if len(set(rings_a)) != 3 or len(set(rings_b)) != 3:
        raise TaskCodeError("bad_ring", "同一组环位不能重复")
    if len(set(colors_a)) != 3:
        raise TaskCodeError("duplicate_color", "第 1 组三种颜色必须互不相同")
    if len(set(colors_b)) != 3:
        raise TaskCodeError("duplicate_color", "第 3 组颜色不能重复")
    if set(colors_a) != set(colors_b):
        raise TaskCodeError("color_set_mismatch", "第 3 组必须和第 1 组是同一组颜色")
    mapping = _symbol_to_color()
    missing = [digit for digit in set(colors_a) if digit not in mapping]
    if missing:
        raise TaskCodeError("bad_color", "没有颜色映射: " + ",".join(sorted(missing)))
    staging_by_color = {colors_a[index]: rings_a[index] for index in range(3)}

    def material(batch: int, index: int, color_digit: str, coarse_ring: str) -> MaterialInstruction:
        return MaterialInstruction(
            f"{'F' if batch == 1 else 'S'}{index + 1}",
            mapping[color_digit],
            coarse_ring,
            staging_ring=staging_by_color[color_digit],
            batch=batch,
            order=index + 1,
        )

    first = tuple(material(1, index, colors_a[index], rings_a[index]) for index in range(3))
    second = tuple(material(2, index, colors_b[index], rings_b[index]) for index in range(3))
    return TaskInstruction("TASK-CODE-" + text, first, second)


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
