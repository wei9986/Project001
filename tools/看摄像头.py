# -*- coding: utf-8 -*-
"""交互式摄像头预览：逐个显示每个索引的实时画面，按数字键选中外接摄像头。

用法（在项目根目录下）:
    python -m tools.看摄像头            # 探测 0..5
    python -m tools.看摄像头 8         # 探测 0..8

操作:
    数字键 0-9    选中当前正在预览的摄像头索引，立即退出并打印该索引
    n 或 空格     切到下一颗摄像头
    p             切到上一颗摄像头
    q             退出（不打印）

预览前会先预热读取若干帧，等自动曝光稳定后再显示，
避免第一帧偏暗被误判成『无图像』；窗口右上角显示帧亮度，方便对照。
"""

from __future__ import annotations

import sys

import cv2
import numpy as np


def open_warmed(index: int) -> cv2.VideoCapture | None:
    """打开 + 预热（丢弃前若干帧，让自动曝光/白平衡收敛）。"""
    cap = cv2.VideoCapture(index)
    if not cap.isOpened():
        cap.release()
        return None
    for _ in range(25):
        ok, _ = cap.read()
        if not ok:
            cap.release()
            return None
    return cap


def scan_usable(max_index: int) -> list[int]:
    usable: list[int] = []
    for i in range(max_index + 1):
        cap = open_warmed(i)
        if cap is not None:
            cap.release()
            usable.append(i)
    return usable


def main() -> None:
    max_index = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    usable = scan_usable(max_index)
    if not usable:
        print("没有任何摄像头索引能打开：请检查连接与驱动。")
        return
    print(f"可用摄像头索引: {usable}")

    pos = 0
    while True:
        index = usable[pos % len(usable)]
        cap = open_warmed(index)
        if cap is None:
            print(f"索引 {index} 打开失败，自动跳过。")
            pos += 1
            continue

        print(f"\n>>> 正在显示 索引 {index}   [0-9]选中  [n/空格]下一颗  [p]上一颗  [q]退出")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            mean = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean())
            cv2.putText(
                frame, f"idx={index} mean={mean:.0f}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2, cv2.LINE_AA,
            )
            cv2.imshow(f"摄像头预览 索引 {index}", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                cap.release()
                cv2.destroyAllWindows()
                return
            if key in (ord("n"), ord(" ")):
                break
            if key == ord("p"):
                pos -= 2
                break
            if 48 <= key <= 57:
                chosen = key - 48
                cap.release()
                cv2.destroyAllWindows()
                print(f"已选中 摄像头索引 {chosen} -> 运行: python vision/颜色追踪.py --source {chosen}")
                return
        cap.release()
        cv2.destroyAllWindows()
        pos += 1


if __name__ == "__main__":
    main()
