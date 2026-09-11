# -*- coding: utf-8 -*-
"""PySide6 颜色追踪调参界面。"""

from __future__ import annotations

__all__ = ["MainWindow", "CameraWorker"]


def __getattr__(name: str):
    if name == "CameraWorker":
        from tools.qt_tuner.camera_worker import CameraWorker

        return CameraWorker
    if name == "MainWindow":
        from tools.qt_tuner.main_window import MainWindow

        return MainWindow
    raise AttributeError(name)
