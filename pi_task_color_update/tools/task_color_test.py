"""Camera QR -> ordered color recognition, without controller connections."""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import cv2

from mission.扫码读取 import (QRCodeReader, ScanType, parse_task_qr, validate_scan,
                         MaterialInstruction, TaskInstruction)
from mission.任务计划 import TaskPlan
from vision.颜色追踪 import ColorTracker, load_config


class UnsupportedTaskCode(ValueError):
    """Decoded four-group code whose semantics have not been verified."""


def parse_order(text):
    text = text.strip()
    if re.fullmatch(r"[0-9]{3}(?:\+[0-9]{3}){3}", text):
        raise UnsupportedTaskCode("Four-group code: mapping unverified; display only")
    if re.fullmatch(r"[123]{3}\+[123]{3}", text):
        batches = text.split("+")
        if any(set(batch) != set("123") for batch in batches):
            raise ValueError("Each batch must contain 1, 2, 3 exactly once")
        colors = {"1": "red", "2": "green", "3": "blue"}
        def batch_items(batch, prefix):
            return tuple(MaterialInstruction(f"{prefix}{i+1}", colors[c], c)
                         for i, c in enumerate(batch))
        task = TaskInstruction("PREP-2025-" + text,
                               batch_items(batches[0], "F"), batch_items(batches[1], "S"))
    elif text.startswith("{"):
        task = parse_task_qr(validate_scan(ScanType.TASK_QR, text, "camera"))
    else:
        raise ValueError("Expected two batches, e.g. 123+231 (1=red, 2=green, 3=blue)")
    TaskPlan(task)
    return task, list(task.first_batch + task.second_batch)


def current_targets(result, color):
    """Only allow current, confirmed detections of the requested color."""
    info = result.get("colors", {}).get(color, {})
    if info.get("timed_out"):
        return []
    return [target for target in info.get("valid_targets", [])
            if target.get("color") == color and target.get("valid")
            and target.get("status") == "confirmed"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="0")
    parser.add_argument("--config", default="config/颜色追踪配置.json")
    parser.add_argument("--make-qr", metavar="PNG", help="Generate a sample QR and exit")
    parser.add_argument("--task", help="Optional JSON task file used only for QR generation")
    parser.add_argument("--code", default="123+231", help="QR content (default: 123+231)")
    args = parser.parse_args()
    if args.make_qr:
        raw = args.code.strip()
        if args.task:
            payload = json.loads(Path(args.task).read_text(encoding="utf-8-sig"))
            raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        try:
            parse_order(raw)
        except UnsupportedTaskCode:
            print("Generating display-only four-group sample")
        params = cv2.QRCodeEncoder_Params()
        params.mode = (cv2.QRCodeEncoder_MODE_BYTE if raw.startswith("{")
                       else cv2.QRCodeEncoder_MODE_ALPHANUMERIC)
        params.correction_level = cv2.QRCodeEncoder_CORRECT_LEVEL_H
        qr = cv2.QRCodeEncoder_create(params).encode(raw)
        qr = cv2.copyMakeBorder(qr, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        qr = cv2.resize(qr, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
        decoded = cv2.QRCodeDetector().detectAndDecode(qr)[0]
        if decoded != raw:
            raise RuntimeError("Generated QR failed round-trip verification; no file written")
        output = Path(args.make_qr)
        output.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(output), qr):
            raise RuntimeError("Cannot save QR image")
        print(f"QR saved: {output}")
        return

    config = load_config(args.config)
    reader = QRCodeReader()
    source = int(args.source) if args.source.isdigit() else args.source
    capture = cv2.VideoCapture(source)
    task = None
    order = []
    index = 0
    tracker = None
    raw_code = ""
    scan_message = "SCAN TASK QR | Q: quit"
    rejected_codes = set()
    log_path = Path("logs") / ("task_color_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".jsonl")
    try:
        if not capture.isOpened():
            raise RuntimeError(f"Cannot open camera: {args.source}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("x", encoding="utf-8") as log:
            def record(event, **data):
                log.write(json.dumps(dict(event=event, **data), ensure_ascii=False) + "\n")
                log.flush()

            print("Show task QR. SPACE confirms current visible target; Q quits.")
            print(f"Log: {log_path}")
            while True:
                ok, frame = capture.read()
                if not ok:
                    record("camera_read_failed")
                    raise RuntimeError("Camera stopped returning frames")
                targets = []
                if task is None:
                    status = scan_message
                    for scan in reader.read(frame):
                        raw_code = scan.raw_value.strip()
                        try:
                            task, order = parse_order(raw_code)
                        except ValueError as exc:
                            scan_message = ("UNSUPPORTED: four groups; display only"
                                            if isinstance(exc, UnsupportedTaskCode)
                                            else "INVALID TASK: show a valid 123+231 code")
                            status = scan_message
                            if raw_code not in rejected_codes:
                                rejected_codes.add(raw_code)
                                record("unsupported_task_code" if isinstance(exc, UnsupportedTaskCode)
                                       else "invalid_task_code", raw=scan.raw_value, reason=str(exc))
                                print(f"{raw_code}: {exc}")
                            continue
                        record("task_scanned", raw=scan.raw_value,
                               profile="legacy-json" if raw_code.startswith("{") else "2025-preparation",
                               order=[item.__dict__ for item in order])
                        print("Order:", " -> ".join(item.color for item in order))
                        tracker = ColorTracker([order[0].color], config=config)
                        break
                elif index < len(order):
                    item = order[index]
                    frame, result = tracker.track(frame)
                    targets = current_targets(result, item.color)
                    batch = 1 if index < len(task.first_batch) else 2
                    status = f"Batch {batch} {index+1}/{len(order)} {item.color} | " + (
                        "READY: SPACE confirm" if targets else "WAIT: no confirmed target")
                else:
                    status = "DONE: recognition sequence complete | Q: quit"
                cv2.putText(frame, status, (10, 28), cv2.FONT_HERSHEY_SIMPLEX,
                            0.55, (0, 255, 255), 2, cv2.LINE_AA)
                if raw_code and not raw_code.startswith("{"):
                    cv2.putText(frame, "Task: " + raw_code[:40], (10, 55),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
                cv2.imshow("Task color test", frame)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    record("exit", completed=bool(task and index == len(order)))
                    break
                if key == 32 and targets:
                    record("recognition_confirmed", index=index, material=order[index].__dict__, targets=targets)
                    index += 1
                    if index < len(order):
                        tracker = ColorTracker([order[index].color], config=config)
                    else:
                        record("sequence_complete")
                        print("Recognition complete. No physical grasp was performed.")
    finally:
        capture.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
