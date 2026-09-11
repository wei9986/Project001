# -*- coding: utf-8 -*-
"""PySide6 视觉调参入口（颜色 / 站点 / 精定位 / 实机基础）。

用法:
  python -m tools.qt调参 --source 0
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PySide6 视觉模块实时调参界面")
    parser.add_argument(
        "--source",
        default="0",
        help="预览输入源（摄像头编号/图片/视频）；不会被实机配置覆盖，可用界面「用实机相机源重连」",
    )
    parser.add_argument(
        "--config",
        default="config/颜色追踪配置.json",
        help="颜色追踪 JSON 配置路径",
    )
    parser.add_argument(
        "--station-config",
        default="config/站点标签.json",
        help="站点标签 JSON 配置路径",
    )
    parser.add_argument(
        "--fine-config",
        default="config/精定位配置.json",
        help="精定位 JSON 配置路径",
    )
    parser.add_argument(
        "--live-config",
        default="config/实机配置.json",
        help="实机 JSON 配置路径",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("缺少依赖 PySide6，请先执行: pip install PySide6", file=sys.stderr)
        return 1

    from tools.qt_tuner.main_window import MainWindow

    args = build_parser().parse_args(argv)
    app = QApplication(sys.argv)
    window = MainWindow(
        source=args.source,
        config_path=args.config,
        station_path=args.station_config,
        fine_path=args.fine_config,
        live_path=args.live_config,
    )
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
