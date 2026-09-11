# -*- coding: utf-8 -*-
"""摄像头采集线程：在后台读帧，主线程只负责显示。"""

from __future__ import annotations

from typing import Any, Optional, Union

import cv2
import numpy as np
from PySide6.QtCore import QMutex, QThread, Signal


def parse_source(source: Union[str, int]) -> Any:
    if isinstance(source, int):
        return source
    text = str(source).strip()
    return int(text) if text.isdigit() else text


class CameraWorker(QThread):
    """循环读取 VideoCapture；只保留最新一帧，避免 UI 处理不过来时队列堆积。"""

    frame_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Union[str, int] = 0, parent=None) -> None:
        super().__init__(parent)
        self._source = parse_source(source)
        self._running = False
        self._mutex = QMutex()
        self._pending: Optional[np.ndarray] = None
        self._emit_requested = True

    def set_source(self, source: Union[str, int]) -> None:
        self._source = parse_source(source)

    def stop(self) -> None:
        self._running = False

    def request_next_frame(self) -> None:
        """主线程处理完一帧后调用；由采集线程发出下一帧，避免同线程同步重入。"""
        self._mutex.lock()
        try:
            self._emit_requested = True
        finally:
            self._mutex.unlock()

    def run(self) -> None:
        self._running = True
        capture = cv2.VideoCapture(self._source)
        if not capture.isOpened():
            self.failed.emit(f"无法打开输入源: {self._source}")
            return
        try:
            while self._running:
                ok, frame = capture.read()
                if not ok or frame is None:
                    if isinstance(self._source, str):
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        self.msleep(5)
                        continue
                    self.msleep(20)
                    continue
                if not isinstance(frame, np.ndarray):
                    continue
                frame_copy = frame.copy()
                self._mutex.lock()
                try:
                    if self._emit_requested:
                        self._emit_requested = False
                        self._pending = None
                        to_emit = frame_copy
                    else:
                        # 覆盖未取走的旧帧，只保留最新；等待 UI 请求后再发
                        self._pending = frame_copy
                        to_emit = None
                finally:
                    self._mutex.unlock()
                if to_emit is not None:
                    self.frame_ready.emit(to_emit)
                    continue
                # UI 繁忙时降低读帧频率，并尽快响应 request_next_frame
                for _ in range(8):
                    if not self._running:
                        break
                    self._mutex.lock()
                    try:
                        flush = self._emit_requested and self._pending is not None
                        if flush:
                            self._emit_requested = False
                            to_emit = self._pending
                            self._pending = None
                        else:
                            to_emit = None
                    finally:
                        self._mutex.unlock()
                    if to_emit is not None:
                        self.frame_ready.emit(to_emit)
                        break
                    self.msleep(1)
        finally:
            capture.release()
