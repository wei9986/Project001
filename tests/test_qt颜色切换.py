# -*- coding: utf-8 -*-
"""P1-10 Qt 调参切颜色配置污染回归测试。

on_color_changed 必须先按新颜色 reload 控件、再 reset_tracker；
顺序反了会把上一颜色的 HSV 滑块值写进新颜色 ranges（保存即落盘污染）。
本测试用 offscreen 平台 + 桩相机构造 MainWindow，避免依赖摄像头硬件。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from tools.qt_tuner.config_binding import read_hsv_from_config


class FakeCameraWorker(QObject):
    """不真实打开相机的 CameraWorker 替身，避免测试启动硬件线程。"""

    frame_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, source):
        super().__init__()
        self._source = source

    def start(self):
        pass

    def stop(self):
        pass

    def wait(self, ms):
        return True

    def request_next_frame(self):
        pass


class QtColorSwitchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_switching_color_does_not_pollute_target_color(self):
        """切颜色后，新颜色 ranges 保持配置值，不被上一颜色滑块污染。"""
        from tools.qt_tuner import main_window

        with patch.object(main_window, "CameraWorker", FakeCameraWorker):
            window = main_window.MainWindow()
        try:
            red_before = read_hsv_from_config(window.config, "red", 0)
            green_before = read_hsv_from_config(window.config, "green", 0)
            # 测试前提：两颜色配置不同，否则无法观察污染
            self.assertNotEqual(red_before, green_before)

            window.on_color_changed("green")
            green_after = read_hsv_from_config(window.config, "green", 0)
            self.assertEqual(green_after, green_before)
        finally:
            window.worker.stop()


if __name__ == "__main__":
    unittest.main()
