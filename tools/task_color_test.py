"""Camera QR -> ordered color recognition, without controller connections."""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

import cv2

from mission.扫码读取 import QRCodeReader, ScanType, parse_task_code, parse_task_qr, validate_scan
from mission.任务计划 import TaskPlan
from vision.颜色追踪 import ColorTracker, load_config
from tools.target_selection import TargetSelector, ConfirmationGate, load_selection_config
from tools.task_color_display import render_display
from tools.target_quality import TargetQuality


class UnsupportedTaskCode(ValueError):
    """Decoded four-group code whose semantics have not been verified."""


def load_task_code_config(path="config/task_color_test.json"):
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    section = data.get("task_code", {})
    if not isinstance(section, dict):
        raise ValueError("task_code must be an object")
    mapping = section.get("symbol_to_color", {"1": "red", "2": "green", "3": "blue"})
    if not isinstance(mapping, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("task_code.symbol_to_color must map strings to color names")
    return section, mapping


def parse_order(text, task_code_config="config/task_color_test.json"):
    """调试入口。四组任务码交给 parse_task_code，JSON 仍走 parse_task_qr。"""
    del task_code_config  # 颜色映射由 parse_task_code 读取同一份配置。
    text = text.strip()
    if text.startswith("{"):
        task = parse_task_qr(validate_scan(ScanType.TASK_QR, text, "camera"))
    else:
        task = parse_task_code(validate_scan(ScanType.TASK_QR, text, "camera"))
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
    parser.add_argument("--selection-config", default="config/task_color_test.json")
    parser.add_argument("--make-qr", metavar="PNG", help="Generate a sample QR and exit")
    parser.add_argument("--task", help="Optional JSON task file used only for QR generation")
    parser.add_argument("--code", default="452+321+254+312", help="QR content (default: four groups)")
    args = parser.parse_args()
    if args.make_qr:
        raw = args.code.strip()
        if args.task:
            payload = json.loads(Path(args.task).read_text(encoding="utf-8-sig"))
            raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        try:
            parse_order(raw, args.selection_config)
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
    selection_config = load_selection_config(args.selection_config)
    task_code_config, _ = load_task_code_config(args.selection_config)
    selector = TargetSelector(selection_config["lost_timeout_ms"], selection_config["max_processing_age_ms"])
    gate = ConfirmationGate()
    quality = TargetQuality(min_stable_frames=selection_config.get("min_stable_frames", 5),
                           max_center_jitter_px=selection_config.get("max_center_jitter_px", 5.0),
                           max_area_change_ratio=selection_config.get("max_area_change_ratio", .30),
                           max_detection_age_ms=selection_config.get("max_detection_age_ms", 200))
    reference_normalized = list(selection_config["reference_normalized"])
    mouse_actions = []
    frame_index = 0
    previous_loop = None
    last_coordinate_log = float("-inf")
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
                log.write(json.dumps(dict(event=event, monotonic_ms=time.monotonic()*1000,
                                          task_index=index, **data), ensure_ascii=False) + "\n")
                log.flush()

            print("Show task QR. SPACE confirms current visible target; Q quits.")
            print(f"Log: {log_path}")
            record("session_started", selection_config=selection_config)
            cv2.namedWindow("Task color test", cv2.WINDOW_AUTOSIZE)
            def on_mouse(event, x, y, flags, param):
                if event == cv2.EVENT_LBUTTONDOWN:
                    mouse_actions.append((x, y))
            cv2.setMouseCallback("Task color test", on_mouse)
            while True:
                loop_start = time.monotonic()*1000
                fps = 1000/(loop_start-previous_loop) if previous_loop is not None and loop_start > previous_loop else 0
                previous_loop = loop_start
                ok, frame = capture.read()
                observed_ms = time.monotonic()*1000
                if not ok:
                    record("camera_read_failed")
                    raise RuntimeError("Camera stopped returning frames")
                frame_index += 1
                height, width = frame.shape[:2]
                while mouse_actions:
                    x, y = mouse_actions.pop(0)
                    if 0 <= x < width and 0 <= y < height:
                        reference_normalized = [x/max(width-1, 1), y/max(height-1, 1)]
                        selector.reset("reference_changed")
                        gate.block(observed_ms)
                        record("reference_changed", reference_normalized=reference_normalized)
                reference = [reference_normalized[0]*(width-1), reference_normalized[1]*(height-1)]
                result = {}
                color = "--"
                detection_ms = 0
                if task is None:
                    status = scan_message
                    for scan in reader.read(frame):
                        raw_code = scan.raw_value.strip()
                        try:
                            task, order = parse_order(raw_code, args.selection_config)
                        except ValueError as exc:
                            scan_message = ("UNSUPPORTED: official four-group code"
                                            if isinstance(exc, UnsupportedTaskCode)
                                            else "INVALID TASK: use xxx+xxx+xxx+xxx")
                            status = scan_message
                            if raw_code not in rejected_codes:
                                rejected_codes.add(raw_code)
                                record("unsupported_task_code" if isinstance(exc, UnsupportedTaskCode)
                                       else "invalid_task_code", raw=scan.raw_value, reason=str(exc))
                                print(f"{raw_code}: {exc}")
                            continue
                        record("task_scanned", raw=scan.raw_value,
                               profile="legacy-json" if raw_code.startswith("{") else "official-four-group",
                               order=[item.__dict__ for item in order])
                        print("Order:", " -> ".join(item.color for item in order))
                        tracker = ColorTracker([order[0].color], config=config)
                        break
                elif index < len(order):
                    item = order[index]
                    color = item.color
                    frame, result = tracker.track(frame)
                    detection_ms = result["elapsed_ms"]
                    batch = 1 if index < len(task.first_batch) else 2
                    local_index = index if batch == 1 else index - len(task.first_batch)
                    batch_size = len(task.first_batch) if batch == 1 else len(task.second_batch)
                    status = f"Batch {batch}: {local_index+1}/{batch_size}  Total {index+1}/{len(order)}"
                else:
                    status = "DONE: sequence complete"
                now_ms = time.monotonic()*1000
                selection = selector.update(result, color, reference, now_ms, observed_ms, frame_index)
                selected = next((t for t in result.get("colors", {}).get(color, {}).get("valid_targets", [])
                                 if t.get("target_id") == selection.get("target_id")), None)
                quality_result = quality.evaluate(selected, now_ms=now_ms, observed_ms=observed_ms,
                                                  timed_out=result.get("timed_out", False))
                if selection["valid"]:
                    quality_result["offset_px"] = selection["offset_px"]
                selection.update(quality_result)
                for event in selector.drain_events():
                    record(**event)
                if now_ms - last_coordinate_log >= selection_config["coordinate_log_interval_ms"]:
                    record("coordinates", **selection)
                    last_coordinate_log = now_ms
                label = raw_code if not raw_code.startswith("{") else "JSON (see log)"
                can_confirm = selection["valid"] and selection.get("quality_state") == "STABLE"
                display = render_display(frame, selection, label, status, detection_ms, fps,
                                         gate.armed and can_confirm)
                cv2.imshow("Task color test", display)
                key = cv2.waitKey(1) & 0xFF
                key_ms = time.monotonic()*1000
                if key == ord("q"):
                    record("exit", completed=bool(task and index == len(order)))
                    break
                if key in (ord("r"), ord("R"), ord("c"), ord("C")):
                    selector.reset("reference_centered" if key in (ord("c"), ord("C")) else "manual_reset")
                    if key in (ord("c"), ord("C")):
                        reference_normalized = [0.5, 0.5]
                        record("reference_changed", reference_normalized=reference_normalized)
                    gate.block(key_ms)
                    for event in selector.drain_events():
                        record(**event)
                fresh = key_ms-observed_ms <= selection_config["max_processing_age_ms"]
                if gate.update(key, key_ms, can_confirm and fresh):
                    record("recognition_confirmed", index=index, material=order[index].__dict__, selected_target=selection)
                    selector.reset("task_changed")
                    quality.reset()
                    for event in selector.drain_events():
                        record(**event)
                    index += 1
                    record("task_changed", next_index=index)
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
