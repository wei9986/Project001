# -*- coding: utf-8 -*-
"""预览与通用控件。"""

from __future__ import annotations

from typing import Iterable

import cv2
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget


def bgr_to_pixmap(frame: np.ndarray, max_width: int = 960) -> QPixmap:
    """BGR/灰度图转 QPixmap，必要时等比缩小。"""
    if frame.ndim == 2:
        rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
    else:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    if width > max_width:
        scale = max_width / float(width)
        rgb = cv2.resize(rgb, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
        height, width = rgb.shape[:2]
    if not rgb.flags["C_CONTIGUOUS"]:
        rgb = np.ascontiguousarray(rgb)
    image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888)
    return QPixmap.fromImage(image.copy())


def expand_mask_to_frame(
    mask: np.ndarray,
    frame_shape: tuple[int, ...],
    roi: tuple[int, int, int, int] | None,
) -> np.ndarray:
    """把 ROI 掩膜铺回整帧尺寸，便于与原图对齐观察。"""
    height, width = frame_shape[:2]
    if roi is None:
        if mask.shape[:2] == (height, width):
            return mask
        return cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST)
    x, y, w, h = (int(v) for v in roi)
    canvas = np.zeros((height, width), dtype=np.uint8)
    if w <= 0 or h <= 0:
        return canvas
    resized = mask
    if mask.shape[:2] != (h, w):
        resized = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    y2 = min(height, y + h)
    x2 = min(width, x + w)
    canvas[y:y2, x:x2] = resized[: y2 - y, : x2 - x]
    return canvas


def draw_station_overlay(frame: np.ndarray, detections: Iterable[object]) -> np.ndarray:
    """在画面上绘制 AprilTag 检测框与站点名。"""
    canvas = frame.copy()
    for detection in detections:
        points = np.asarray(detection.corners_px, dtype=np.int32).reshape(-1, 1, 2)
        cv2.polylines(canvas, [points], True, (0, 255, 255), 2)
        label = detection.station_id or f"id={detection.tag_id}"
        cx, cy = detection.center_px
        cv2.putText(
            canvas,
            f"{label} ({detection.tag_id})",
            (int(cx) + 6, int(cy) - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 255, 255),
            2,
            cv2.LINE_AA,
        )
    return canvas


class SliderRow(QWidget):
    """带数值标签的水平滑块。"""

    def __init__(self, name: str, minimum: int, maximum: int, value: int = 0, parent=None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel(name)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(minimum, maximum)
        self.slider.setValue(value)
        self.value_label = QLabel(str(value))
        self.value_label.setMinimumWidth(36)
        self.slider.valueChanged.connect(lambda v: self.value_label.setText(str(v)))
        layout.addWidget(self.label, 2)
        layout.addWidget(self.slider, 5)
        layout.addWidget(self.value_label, 1)

    def value(self) -> int:
        return int(self.slider.value())

    def set_value(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(int(value))
        self.value_label.setText(str(int(value)))
        self.slider.blockSignals(False)
