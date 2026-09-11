# -*- coding: utf-8 -*-
"""多页签调参主窗口：颜色 / 站点 / 精定位 / 实机基础 + 实时预览。"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from tools.参数调试 import save_config
from tools.qt_tuner.camera_worker import CameraWorker
from tools.qt_tuner.config_binding import (
    apply_color_morphology,
    apply_hsv_sliders,
    apply_live_basic_values,
    apply_preprocess_values,
    apply_tracking_values,
    build_fine_localization_config,
    build_station_config,
    read_hsv_from_config,
)
from tools.qt_tuner.preview import SliderRow, bgr_to_pixmap, draw_station_overlay, expand_mask_to_frame
from vision.站点定位 import AprilTagStationDetector, load_station_config, station_mapping_from_config
from vision.精定位 import load_fine_localization_config
from vision.颜色追踪 import ColorTracker, load_config
from vision.配置加载 import load_json, project_root, resolve_path


COLOR_CHOICES = ("red", "green", "blue", "yellow")
DICT_CHOICES = (
    "DICT_APRILTAG_36h11",
    "DICT_APRILTAG_25h9",
    "DICT_APRILTAG_16h5",
    "DICT_4X4_50",
    "DICT_5X5_100",
    "DICT_6X6_250",
    "DICT_7X7_1000",
)


class MainWindow(QMainWindow):
    def __init__(
        self,
        source: str = "0",
        config_path: Optional[str] = None,
        station_path: Optional[str] = None,
        fine_path: Optional[str] = None,
        live_path: Optional[str] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("视觉模块调参 (PySide6)")
        self.resize(1480, 900)

        root = project_root()
        self.color_path = str(resolve_path(config_path) or (root / "config" / "颜色追踪配置.json"))
        self.station_path = str(resolve_path(station_path) or (root / "config" / "站点标签.json"))
        self.fine_path = str(resolve_path(fine_path) or (root / "config" / "精定位配置.json"))
        self.live_path = str(resolve_path(live_path) or (root / "config" / "实机配置.json"))

        self.config: Dict[str, Any] = load_config(self.color_path)
        self.station_config: Dict[str, Any] = load_station_config(self.station_path)
        self.fine_config: Dict[str, Any] = load_fine_localization_config(self.fine_path)
        self.live_config: Dict[str, Any] = load_json(self.live_path)

        self.color_name = "red"
        self.range_index = 0
        self.tracker = ColorTracker([self.color_name], config=self.config)
        self.station_detector = AprilTagStationDetector.from_config(self.station_config)
        self._frame_size = (640, 480)
        self._last_fps_ts = time.perf_counter()
        self._fps = 0.0
        self._updating_ui = False
        self._color_dirty = True
        # CLI --source 为准；实机配置里的 camera.source 仅用于「用实机相机源重连」
        self.worker = CameraWorker(source)
        self.worker.frame_ready.connect(self.on_frame, Qt.ConnectionType.QueuedConnection)
        self.worker.failed.connect(self.on_camera_failed, Qt.ConnectionType.QueuedConnection)

        self._build_ui()
        self.reload_all_controls()
        self.worker.start()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)

        left = QVBoxLayout()
        left.addWidget(self._build_camera_group())
        self.mode_tabs = QTabWidget()
        self.mode_tabs.addTab(self._build_color_tab(), "颜色追踪")
        self.mode_tabs.addTab(self._build_station_tab(), "站点标签")
        self.mode_tabs.addTab(self._build_fine_tab(), "精定位")
        self.mode_tabs.addTab(self._build_live_tab(), "实机基础")
        left.addWidget(self.mode_tabs, 1)
        left.addWidget(self._build_action_buttons())
        layout.addLayout(left, 2)

        right = QVBoxLayout()
        self.status_label = QLabel("就绪")
        self.view_tabs = QTabWidget()
        self.view_original = QLabel("等待画面…")
        self.view_mask = QLabel("等待画面…")
        self.view_annotated = QLabel("等待画面…")
        for view in (self.view_original, self.view_mask, self.view_annotated):
            view.setAlignment(Qt.AlignmentFlag.AlignCenter)
            view.setMinimumSize(640, 360)
            view.setStyleSheet("background:#222; color:#ddd;")
        self.view_tabs.addTab(self.view_original, "原图")
        self.view_tabs.addTab(self.view_mask, "掩膜")
        self.view_tabs.addTab(self.view_annotated, "标注/站点")
        right.addWidget(self.status_label)
        right.addWidget(self.view_tabs, 1)
        layout.addLayout(right, 5)

        self.setStatusBar(QStatusBar())
        self.statusBar().showMessage("赛前调参工具：保存后关闭，再一键启动任务")

    def _build_camera_group(self) -> QGroupBox:
        box = QGroupBox("相机")
        form = QFormLayout(box)
        self.source_edit = QLineEdit(str(self.worker._source))
        reconnect = QPushButton("重连相机")
        reconnect.clicked.connect(self.reconnect_camera)
        form.addRow("相机源", self.source_edit)
        form.addRow(reconnect)
        return box

    def _build_color_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        io = QGroupBox("颜色配置")
        form = QFormLayout(io)
        self.color_path_edit = QLineEdit(self.color_path)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._browse_into(self.color_path_edit))
        row = QHBoxLayout()
        row.addWidget(self.color_path_edit, 1)
        row.addWidget(browse)
        self.color_combo = QComboBox()
        self.color_combo.addItems(COLOR_CHOICES)
        self.color_combo.currentTextChanged.connect(self.on_color_changed)
        self.range_spin = QSpinBox()
        self.range_spin.setRange(0, 1)
        self.range_spin.valueChanged.connect(self.on_range_changed)
        form.addRow("配置文件", row)
        form.addRow("颜色", self.color_combo)
        form.addRow("HSV range 索引", self.range_spin)
        layout.addWidget(io)

        hsv_box = QGroupBox("HSV 范围")
        hsv_layout = QVBoxLayout(hsv_box)
        self.hsv_sliders = {
            "h_low": SliderRow("H low", 0, 179),
            "h_high": SliderRow("H high", 0, 179),
            "s_low": SliderRow("S low", 0, 255),
            "s_high": SliderRow("S high", 0, 255),
            "v_low": SliderRow("V low", 0, 255),
            "v_high": SliderRow("V high", 0, 255),
        }
        for slider in self.hsv_sliders.values():
            slider.slider.valueChanged.connect(self.on_color_params_changed)
            hsv_layout.addWidget(slider)
        layout.addWidget(hsv_box)

        morph = QGroupBox("形态学")
        morph_form = QFormLayout(morph)
        self.kernel_spin = QSpinBox()
        self.kernel_spin.setRange(1, 31)
        self.open_spin = QSpinBox()
        self.open_spin.setRange(0, 10)
        self.close_spin = QSpinBox()
        self.close_spin.setRange(0, 10)
        for widget in (self.kernel_spin, self.open_spin, self.close_spin):
            widget.valueChanged.connect(self.on_color_params_changed)
        morph_form.addRow("kernel_size", self.kernel_spin)
        morph_form.addRow("open_iterations", self.open_spin)
        morph_form.addRow("close_iterations", self.close_spin)
        layout.addWidget(morph)

        tracking = QGroupBox("Tracking")
        tform = QFormLayout(tracking)
        self.min_area = QSpinBox()
        self.min_area.setRange(0, 2_000_000)
        self.max_area = QSpinBox()
        self.max_area.setRange(0, 2_000_000)
        self.confirm_frames = QSpinBox()
        self.confirm_frames.setRange(1, 30)
        self.max_match = QDoubleSpinBox()
        self.max_match.setRange(0.0, 2000.0)
        self.max_match.setDecimals(1)
        self.max_lost = QSpinBox()
        self.max_lost.setRange(0, 60)
        self.min_circ = QDoubleSpinBox()
        self.min_circ.setRange(0.0, 1.0)
        self.min_circ.setSingleStep(0.01)
        self.min_solid = QDoubleSpinBox()
        self.min_solid.setRange(0.0, 1.0)
        self.min_solid.setSingleStep(0.01)
        self.roi_enabled = QCheckBox("启用 ROI")
        self.roi_x = QSpinBox()
        self.roi_y = QSpinBox()
        self.roi_w = QSpinBox()
        self.roi_h = QSpinBox()
        for spin in (self.roi_x, self.roi_y, self.roi_w, self.roi_h):
            spin.setRange(0, 10000)
        for widget in (
            self.min_area, self.max_area, self.confirm_frames, self.max_match,
            self.max_lost, self.min_circ, self.min_solid, self.roi_enabled,
            self.roi_x, self.roi_y, self.roi_w, self.roi_h,
        ):
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self.on_color_params_changed)
            if hasattr(widget, "toggled"):
                widget.toggled.connect(self.on_color_params_changed)
        tform.addRow("min_area", self.min_area)
        tform.addRow("max_area (0=关)", self.max_area)
        tform.addRow("confirm_frames", self.confirm_frames)
        tform.addRow("max_match_distance", self.max_match)
        tform.addRow("max_lost_frames", self.max_lost)
        tform.addRow("min_circularity", self.min_circ)
        tform.addRow("min_solidity", self.min_solid)
        tform.addRow(self.roi_enabled)
        tform.addRow("ROI x/y", self._pair(self.roi_x, self.roi_y))
        tform.addRow("ROI w/h", self._pair(self.roi_w, self.roi_h))
        layout.addWidget(tracking)

        preprocess = QGroupBox("Preprocess")
        pform = QFormLayout(preprocess)
        self.blur_ksize = QSpinBox()
        self.blur_ksize.setRange(1, 31)
        self.blur_sigma = QDoubleSpinBox()
        self.blur_sigma.setRange(0.0, 20.0)
        self.blur_sigma.setSingleStep(0.1)
        self.blur_ksize.valueChanged.connect(self.on_color_params_changed)
        self.blur_sigma.valueChanged.connect(self.on_color_params_changed)
        pform.addRow("blur_ksize", self.blur_ksize)
        pform.addRow("blur_sigma", self.blur_sigma)
        layout.addWidget(preprocess)
        layout.addStretch(1)
        return tab

    def _build_station_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        form_box = QGroupBox("站点标签配置")
        form = QFormLayout(form_box)
        self.station_path_edit = QLineEdit(self.station_path)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._browse_into(self.station_path_edit))
        path_row = QHBoxLayout()
        path_row.addWidget(self.station_path_edit, 1)
        path_row.addWidget(browse)
        self.dict_combo = QComboBox()
        self.dict_combo.addItems(DICT_CHOICES)
        self.dict_combo.setEditable(False)
        self.tag_size_spin = QDoubleSpinBox()
        self.tag_size_spin.setRange(1.0, 500.0)
        self.tag_size_spin.setDecimals(1)
        self.dict_combo.currentTextChanged.connect(self.on_station_dictionary_changed)
        self.tag_size_spin.valueChanged.connect(self.on_station_mapping_changed)
        form.addRow("配置文件", path_row)
        form.addRow("dictionary", self.dict_combo)
        form.addRow("tag_size_mm", self.tag_size_spin)
        layout.addWidget(form_box)

        self.station_table = QTableWidget(0, 2)
        self.station_table.setHorizontalHeaderLabels(["tag_id", "station_id"])
        self.station_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.station_table.itemChanged.connect(self.on_station_mapping_changed)
        layout.addWidget(self.station_table, 1)
        row = QHBoxLayout()
        add_btn = QPushButton("添加映射")
        del_btn = QPushButton("删除选中")
        add_btn.clicked.connect(self.add_station_row)
        del_btn.clicked.connect(self.remove_station_row)
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        layout.addLayout(row)
        return tab

    def _build_fine_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        box = QGroupBox("精定位阈值")
        form = QFormLayout(box)
        self.fine_path_edit = QLineEdit(self.fine_path)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._browse_into(self.fine_path_edit))
        path_row = QHBoxLayout()
        path_row.addWidget(self.fine_path_edit, 1)
        path_row.addWidget(browse)
        self.fine_samples = QSpinBox()
        self.fine_samples.setRange(2, 50)
        self.fine_spread = QDoubleSpinBox()
        self.fine_spread.setRange(0.1, 200.0)
        self.fine_step = QDoubleSpinBox()
        self.fine_step.setRange(0.1, 500.0)
        self.fine_timeout = QDoubleSpinBox()
        self.fine_timeout.setRange(10.0, 60_000.0)
        self.fine_lost = QDoubleSpinBox()
        self.fine_lost.setRange(10.0, 60_000.0)
        for widget in (
            self.fine_samples, self.fine_spread, self.fine_step, self.fine_timeout, self.fine_lost
        ):
            widget.valueChanged.connect(self.on_fine_params_changed)
        form.addRow("配置文件", path_row)
        form.addRow("required_samples", self.fine_samples)
        form.addRow("max_spread_mm", self.fine_spread)
        form.addRow("max_step_mm", self.fine_step)
        form.addRow("timeout_ms", self.fine_timeout)
        form.addRow("lost_timeout_ms", self.fine_lost)
        layout.addWidget(box)
        tip = QLabel("精定位在任务抓取阶段使用；此处调参保存后由实机/任务状态机读取。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        layout.addStretch(1)
        return tab

    def _build_live_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        box = QGroupBox("实机基础（赛前）")
        form = QFormLayout(box)
        self.live_path_edit = QLineEdit(self.live_path)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._browse_into(self.live_path_edit))
        path_row = QHBoxLayout()
        path_row.addWidget(self.live_path_edit, 1)
        path_row.addWidget(browse)
        self.live_cam_source = QLineEdit("0")
        self.live_width = QSpinBox()
        self.live_width.setRange(160, 4096)
        self.live_height = QSpinBox()
        self.live_height.setRange(120, 2160)
        self.live_fps = QSpinBox()
        self.live_fps.setRange(1, 120)
        self.live_tof = QDoubleSpinBox()
        self.live_tof.setRange(1.0, 2000.0)
        self.live_sleep = QDoubleSpinBox()
        self.live_sleep.setRange(0.0, 1.0)
        self.live_sleep.setDecimals(4)
        self.live_display = QCheckBox("实机显示 OpenCV 窗口")
        self.color_checks = {name: QCheckBox(name) for name in COLOR_CHOICES}
        colors_row = QHBoxLayout()
        for check in self.color_checks.values():
            check.toggled.connect(self.on_live_params_changed)
            colors_row.addWidget(check)
        for widget in (
            self.live_cam_source, self.live_width, self.live_height, self.live_fps,
            self.live_tof, self.live_sleep, self.live_display,
        ):
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(self.on_live_params_changed)
            if hasattr(widget, "textChanged"):
                widget.textChanged.connect(self.on_live_params_changed)
            if hasattr(widget, "toggled"):
                widget.toggled.connect(self.on_live_params_changed)
        form.addRow("配置文件", path_row)
        form.addRow("camera.source", self.live_cam_source)
        form.addRow("camera.width", self.live_width)
        form.addRow("camera.height", self.live_height)
        form.addRow("camera.fps", self.live_fps)
        form.addRow("启用颜色", colors_row)
        form.addRow("safety.minimum_tof_mm", self.live_tof)
        form.addRow("loop.sleep_s", self.live_sleep)
        form.addRow(self.live_display)
        layout.addWidget(box)
        tip = QLabel("保存后写入实机配置.json；比赛一键启动会读取该文件，无需再开 Qt。")
        tip.setWordWrap(True)
        layout.addWidget(tip)
        apply_cam = QPushButton("用实机相机源重连预览")
        apply_cam.clicked.connect(self.apply_live_camera_to_preview)
        layout.addWidget(apply_cam)
        layout.addStretch(1)
        return tab

    def _build_action_buttons(self) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        save_page = QPushButton("保存当前页")
        save_all = QPushButton("全部保存")
        reload_all = QPushButton("全部重载")
        reset = QPushButton("重置追踪")
        save_page.clicked.connect(self.save_current_page)
        save_all.clicked.connect(self.save_all)
        reload_all.clicked.connect(self.reload_all_from_disk)
        reset.clicked.connect(self.reset_tracker)
        layout.addWidget(save_page)
        layout.addWidget(save_all)
        layout.addWidget(reload_all)
        layout.addWidget(reset)
        return row

    @staticmethod
    def _pair(a: QWidget, b: QWidget) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(a)
        layout.addWidget(b)
        return w

    def _browse_into(self, edit: QLineEdit) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择配置文件", str(project_root() / "config"), "JSON (*.json)"
        )
        if path:
            edit.setText(path)

    def reconnect_camera(self) -> None:
        old = self.worker
        try:
            old.frame_ready.disconnect(self.on_frame)
            old.failed.disconnect(self.on_camera_failed)
        except (RuntimeError, TypeError):
            pass
        old.stop()
        if not old.wait(2000):
            # P2-18：线程仍在跑时不要立刻 deleteLater，避免 QThread destroyed while running。
            self.statusBar().showMessage("旧采集线程未在 2s 内退出，将延迟回收")
            if hasattr(old, "requestInterruption"):
                old.requestInterruption()
            old.deleteLater()
        else:
            old.deleteLater()
        self.worker = CameraWorker(self.source_edit.text().strip() or "0")
        self.worker.frame_ready.connect(self.on_frame, Qt.ConnectionType.QueuedConnection)
        self.worker.failed.connect(self.on_camera_failed, Qt.ConnectionType.QueuedConnection)
        self.worker.start()
        self.statusBar().showMessage(f"已重连: {self.source_edit.text().strip()}")

    def apply_live_camera_to_preview(self) -> None:
        self.apply_live_controls()
        self.source_edit.setText(str(self.live_config.get("camera", {}).get("source", 0)))
        self.reconnect_camera()

    def on_camera_failed(self, message: str) -> None:
        QMessageBox.critical(self, "相机错误", message)
        self.status_label.setText(message)

    def on_color_changed(self, color: str) -> None:
        if self._updating_ui:
            return
        self.color_name = color
        # 必须先按新颜色从配置刷新控件、再重建 tracker；顺序反了会把上一颜色的
        # HSV 滑块值写进新颜色 ranges（保存即落盘污染，P1-10）。
        self.reload_color_controls()
        self.range_spin.setValue(self.range_index)
        self.reset_tracker()

    def on_range_changed(self, index: int) -> None:
        if self._updating_ui:
            return
        self.range_index = int(index)
        self.reload_color_controls(hsv_only=True)

    def on_color_params_changed(self, *_args: Any) -> None:
        if not self._updating_ui:
            self._color_dirty = True

    def on_station_dictionary_changed(self, *_args: Any) -> None:
        if not self._updating_ui:
            self.apply_station_controls(rebuild=True)

    def on_station_mapping_changed(self, *_args: Any) -> None:
        """表格 / tag_size 变更：只更新映射，不重建 Aruco 检测器。"""
        if not self._updating_ui:
            self.apply_station_controls(rebuild=False)

    def on_fine_params_changed(self, *_args: Any) -> None:
        if not self._updating_ui:
            self.apply_fine_controls()

    def on_live_params_changed(self, *_args: Any) -> None:
        if not self._updating_ui:
            self.apply_live_controls()

    def apply_color_controls(self) -> None:
        values = [
            self.hsv_sliders["h_low"].value(),
            self.hsv_sliders["h_high"].value(),
            self.hsv_sliders["s_low"].value(),
            self.hsv_sliders["s_high"].value(),
            self.hsv_sliders["v_low"].value(),
            self.hsv_sliders["v_high"].value(),
        ]
        try:
            apply_hsv_sliders(self.config, self.color_name, self.range_index, values)
        except (ValueError, IndexError):
            return
        apply_color_morphology(
            self.config,
            self.color_name,
            kernel_size=self.kernel_spin.value(),
            open_iterations=self.open_spin.value(),
            close_iterations=self.close_spin.value(),
        )
        apply_tracking_values(
            self.config,
            min_area=self.min_area.value(),
            max_area=self.max_area.value(),
            confirm_frames=self.confirm_frames.value(),
            max_match_distance=self.max_match.value(),
            max_lost_frames=self.max_lost.value(),
            min_circularity=self.min_circ.value(),
            min_solidity=self.min_solid.value(),
            roi_enabled=self.roi_enabled.isChecked(),
            roi=[self.roi_x.value(), self.roi_y.value(), self.roi_w.value(), self.roi_h.value()],
        )
        apply_preprocess_values(
            self.config,
            blur_ksize=self.blur_ksize.value(),
            blur_sigma=self.blur_sigma.value(),
        )
        self.tracker.config = self.config
        roi = self.config["tracking"].get("roi")
        self.tracker.roi = tuple(roi) if roi else None
        self._color_dirty = False

    def apply_station_controls(self, rebuild: bool = False) -> None:
        stations: Dict[str, str] = {}
        for row in range(self.station_table.rowCount()):
            id_item = self.station_table.item(row, 0)
            name_item = self.station_table.item(row, 1)
            if id_item is None or name_item is None:
                continue
            tag_id = id_item.text().strip()
            station_id = name_item.text().strip()
            if tag_id and station_id:
                stations[tag_id] = station_id
        try:
            self.station_config = build_station_config(
                dictionary=self.dict_combo.currentText().strip(),
                tag_size_mm=self.tag_size_spin.value(),
                stations=stations,
            )
            if rebuild:
                self.station_detector = AprilTagStationDetector.from_config(self.station_config)
            else:
                self.station_detector.station_mapping = station_mapping_from_config(self.station_config)
                self.station_detector.tag_size_mm = float(self.station_config["tag_size_mm"])
        except ValueError as exc:
            self.status_label.setText(f"站点配置无效: {exc}")

    def apply_fine_controls(self) -> None:
        try:
            self.fine_config = build_fine_localization_config(
                required_samples=self.fine_samples.value(),
                max_spread_mm=self.fine_spread.value(),
                max_step_mm=self.fine_step.value(),
                timeout_ms=self.fine_timeout.value(),
                lost_timeout_ms=self.fine_lost.value(),
            )
        except ValueError as exc:
            self.status_label.setText(f"精定位配置无效: {exc}")

    def apply_live_controls(self) -> None:
        colors = [name for name, check in self.color_checks.items() if check.isChecked()]
        if not colors:
            colors = ["red"]
        try:
            apply_live_basic_values(
                self.live_config,
                camera_source=self.live_cam_source.text().strip() or "0",
                width=self.live_width.value(),
                height=self.live_height.value(),
                fps=self.live_fps.value(),
                colors=colors,
                minimum_tof_mm=self.live_tof.value(),
                display=self.live_display.isChecked(),
                sleep_s=self.live_sleep.value(),
            )
        except ValueError as exc:
            self.status_label.setText(f"实机配置无效: {exc}")

    def reload_color_controls(self, hsv_only: bool = False) -> None:
        self._updating_ui = True
        try:
            ranges = self.config["colors"][self.color_name]["ranges"]
            self.range_spin.setMaximum(max(0, len(ranges) - 1))
            self.range_index = min(self.range_index, len(ranges) - 1)
            hsv = read_hsv_from_config(self.config, self.color_name, self.range_index)
            for key, value in zip(("h_low", "h_high", "s_low", "s_high", "v_low", "v_high"), hsv):
                self.hsv_sliders[key].set_value(value)
            if hsv_only:
                return
            color_cfg = self.config["colors"][self.color_name]
            self.kernel_spin.setValue(int(color_cfg.get("kernel_size", 5)))
            self.open_spin.setValue(int(color_cfg.get("open_iterations", 1)))
            self.close_spin.setValue(int(color_cfg.get("close_iterations", 1)))
            tracking = self.config["tracking"]
            self.min_area.setValue(int(tracking.get("min_area", 500)))
            self.max_area.setValue(int(tracking.get("max_area", 0)))
            self.confirm_frames.setValue(int(tracking.get("confirm_frames", 3)))
            self.max_match.setValue(float(tracking.get("max_match_distance", 80.0)))
            self.max_lost.setValue(int(tracking.get("max_lost_frames", 5)))
            self.min_circ.setValue(float(tracking.get("min_circularity", 0.0)))
            self.min_solid.setValue(float(tracking.get("min_solidity", 0.0)))
            roi = tracking.get("roi")
            self.roi_enabled.setChecked(bool(roi))
            width, height = self._frame_size
            if roi:
                self.roi_x.setValue(int(roi[0]))
                self.roi_y.setValue(int(roi[1]))
                self.roi_w.setValue(int(roi[2]))
                self.roi_h.setValue(int(roi[3]))
            else:
                self.roi_x.setValue(0)
                self.roi_y.setValue(0)
                self.roi_w.setValue(width)
                self.roi_h.setValue(height)
            preprocess = self.config.get("preprocess", {})
            self.blur_ksize.setValue(int(preprocess.get("blur_ksize", 5)))
            self.blur_sigma.setValue(float(preprocess.get("blur_sigma", 0.0)))
            self.color_combo.setCurrentText(self.color_name)
            self.range_spin.setValue(self.range_index)
            self.color_path_edit.setText(self.color_path)
        finally:
            self._updating_ui = False

    def reload_station_controls(self) -> None:
        self._updating_ui = True
        try:
            self.station_path_edit.setText(self.station_path)
            self.dict_combo.setCurrentText(str(self.station_config.get("dictionary", "")))
            self.tag_size_spin.setValue(float(self.station_config.get("tag_size_mm", 40.0)))
            stations = self.station_config.get("stations", {})
            self.station_table.blockSignals(True)
            self.station_table.setRowCount(0)
            for tag_id, station_id in stations.items():
                row = self.station_table.rowCount()
                self.station_table.insertRow(row)
                self.station_table.setItem(row, 0, QTableWidgetItem(str(tag_id)))
                self.station_table.setItem(row, 1, QTableWidgetItem(str(station_id)))
            self.station_table.blockSignals(False)
        finally:
            self._updating_ui = False

    def reload_fine_controls(self) -> None:
        self._updating_ui = True
        try:
            self.fine_path_edit.setText(self.fine_path)
            self.fine_samples.setValue(int(self.fine_config["required_samples"]))
            self.fine_spread.setValue(float(self.fine_config["max_spread_mm"]))
            self.fine_step.setValue(float(self.fine_config["max_step_mm"]))
            self.fine_timeout.setValue(float(self.fine_config["timeout_ms"]))
            self.fine_lost.setValue(float(self.fine_config["lost_timeout_ms"]))
        finally:
            self._updating_ui = False

    def reload_live_controls(self) -> None:
        self._updating_ui = True
        try:
            self.live_path_edit.setText(self.live_path)
            camera = self.live_config.get("camera", {})
            self.live_cam_source.setText(str(camera.get("source", 0)))
            self.live_width.setValue(int(camera.get("width", 1280)))
            self.live_height.setValue(int(camera.get("height", 720)))
            self.live_fps.setValue(int(camera.get("fps", 30)))
            enabled = set(self.live_config.get("colors", ["red", "green", "blue"]))
            for name, check in self.color_checks.items():
                check.setChecked(name in enabled)
            safety = self.live_config.get("safety", {})
            self.live_tof.setValue(float(safety.get("minimum_tof_mm", 80.0)))
            loop = self.live_config.get("loop", {})
            self.live_sleep.setValue(float(loop.get("sleep_s", 0.002)))
            self.live_display.setChecked(bool(self.live_config.get("display", False)))
        finally:
            self._updating_ui = False

    def reload_all_controls(self) -> None:
        self.reload_color_controls()
        self.reload_station_controls()
        self.reload_fine_controls()
        self.reload_live_controls()

    def add_station_row(self) -> None:
        row = self.station_table.rowCount()
        self.station_table.insertRow(row)
        self.station_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        self.station_table.setItem(row, 1, QTableWidgetItem("new_station"))

    def remove_station_row(self) -> None:
        rows = sorted({index.row() for index in self.station_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.station_table.removeRow(row)
        self.apply_station_controls(rebuild=False)

    def save_json(self, path: str, data: Dict[str, Any]) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def save_current_page(self) -> None:
        index = self.mode_tabs.currentIndex()
        if index == 0:
            self.apply_color_controls()
            path = self.color_path_edit.text().strip() or self.color_path
            save_config(self.config, path)
            self.color_path = path
        elif index == 1:
            self.apply_station_controls(rebuild=True)
            path = self.station_path_edit.text().strip() or self.station_path
            self.save_json(path, self.station_config)
            self.station_path = path
        elif index == 2:
            self.apply_fine_controls()
            path = self.fine_path_edit.text().strip() or self.fine_path
            self.save_json(path, self.fine_config)
            self.fine_path = path
        else:
            self.apply_live_controls()
            path = self.live_path_edit.text().strip() or self.live_path
            self.save_json(path, self.live_config)
            self.live_path = path
        self.statusBar().showMessage(f"当前页已保存", 5000)

    def save_all(self) -> None:
        self.apply_color_controls()
        self.apply_station_controls(rebuild=True)
        self.apply_fine_controls()
        self.apply_live_controls()
        self.color_path = self.color_path_edit.text().strip() or self.color_path
        self.station_path = self.station_path_edit.text().strip() or self.station_path
        self.fine_path = self.fine_path_edit.text().strip() or self.fine_path
        self.live_path = self.live_path_edit.text().strip() or self.live_path
        save_config(self.config, self.color_path)
        self.save_json(self.station_path, self.station_config)
        self.save_json(self.fine_path, self.fine_config)
        self.save_json(self.live_path, self.live_config)
        self.statusBar().showMessage("全部配置已保存", 5000)

    def reload_all_from_disk(self) -> None:
        self.color_path = self.color_path_edit.text().strip() or self.color_path
        self.station_path = self.station_path_edit.text().strip() or self.station_path
        self.fine_path = self.fine_path_edit.text().strip() or self.fine_path
        self.live_path = self.live_path_edit.text().strip() or self.live_path
        self.config = load_config(self.color_path)
        self.station_config = load_station_config(self.station_path)
        self.fine_config = load_fine_localization_config(self.fine_path)
        self.live_config = load_json(self.live_path)
        self.station_detector = AprilTagStationDetector.from_config(self.station_config)
        self.reset_tracker()
        self.reload_all_controls()
        self.statusBar().showMessage("已从磁盘重新加载全部配置", 5000)

    def reset_tracker(self) -> None:
        try:
            self.apply_color_controls()
            # P2-17：max_area < min_area 等非法配置不得让 UI 槽崩溃。
            min_area = int(self.min_area.value())
            max_area = int(self.max_area.value())
            if max_area > 0 and max_area < min_area:
                self.max_area.blockSignals(True)
                self.max_area.setValue(min_area)
                self.max_area.blockSignals(False)
                self.config["tracking"]["max_area"] = min_area
            self.tracker = ColorTracker([self.color_name], config=self.config)
            roi = self.config["tracking"].get("roi")
            self.tracker.roi = tuple(roi) if roi else None
            self._color_dirty = False
        except (ValueError, KeyError, TypeError) as exc:
            self.status_label.setText(f"追踪器重置失败: {exc}")

    def on_frame(self, frame: np.ndarray) -> None:
        try:
            if not isinstance(frame, np.ndarray) or frame.size == 0:
                return
            height, width = frame.shape[:2]
            self._frame_size = (width, height)
            if self._color_dirty:
                self.apply_color_controls()
            annotated, result = self.tracker.track(frame)
            stations: List[Any] = []
            station_view = annotated
            need_stations = self.view_tabs.currentIndex() == 2 or self.mode_tabs.currentIndex() == 1
            if need_stations:
                stations = self.station_detector.detect(frame)
                station_view = draw_station_overlay(annotated, stations)

            mask_view = None
            if self.view_tabs.currentIndex() == 1:
                hsv = self.tracker.preprocess(frame)
                roi_hsv, _ = self.tracker.apply_roi(hsv)
                mask = self.tracker.create_mask(roi_hsv, self.color_name)
                mask_view = expand_mask_to_frame(mask, frame.shape, self.tracker.roi)

            now = time.perf_counter()
            dt = now - self._last_fps_ts
            if dt > 0:
                self._fps = 0.9 * self._fps + 0.1 * (1.0 / dt) if self._fps else 1.0 / dt
            self._last_fps_ts = now
            targets = len(result.get("targets", []))
            mode = self.mode_tabs.tabText(self.mode_tabs.currentIndex())
            self.status_label.setText(
                f"[{mode}] 颜色={self.color_name} 目标={targets} 站点={len(stations)} "
                f"FPS={self._fps:.1f}  {result.get('elapsed_ms', 0):.1f}ms"
            )
            view_index = self.view_tabs.currentIndex()
            if view_index == 0:
                self.view_original.setPixmap(bgr_to_pixmap(frame))
            elif view_index == 1 and mask_view is not None:
                self.view_mask.setPixmap(bgr_to_pixmap(mask_view))
            else:
                self.view_annotated.setPixmap(bgr_to_pixmap(station_view))
        except Exception as exc:  # noqa: BLE001 - 预览线程路径需兜底，避免卡死拉流
            self.status_label.setText(f"处理失败: {exc}")
        finally:
            self.worker.request_next_frame()

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            self.worker.frame_ready.disconnect(self.on_frame)
            self.worker.failed.disconnect(self.on_camera_failed)
        except (RuntimeError, TypeError):
            pass
        self.worker.stop()
        if not self.worker.wait(2000):
            if hasattr(self.worker, "requestInterruption"):
                self.worker.requestInterruption()
        super().closeEvent(event)
