"""Render a sidebar without resizing the original camera pixels."""
import cv2
import numpy as np


def render_display(frame, selection, task_label, progress, detection_ms, fps, armed):
    height, width = frame.shape[:2]
    canvas = np.zeros((max(height, 520), width + 390, 3), dtype=np.uint8)
    canvas[:height, :width] = frame
    reference = tuple(round(v) for v in selection["reference_px"])
    cv2.drawMarker(canvas, reference, (255, 255, 255), cv2.MARKER_CROSS, 20, 2)
    if selection["valid"]:
        center = tuple(round(v) for v in selection["center_px"])
        cv2.circle(canvas, center, 24, (0, 255, 255), 3)
        cv2.line(canvas, reference, center, (0, 255, 255), 1)
    center, offset = selection["center_px"], selection["offset_px"]
    lines = ["PIXEL TARGET TEST", "Task: " + task_label, progress,
             "State: " + selection.get("quality_state", selection["state"]), "Lock: " + selection["state"], "Color: " + selection["color"],
             "Target ID: " + str(selection["target_id"] if selection["target_id"] is not None else "--"),
             f"X: {center[0]:.1f}  Y: {center[1]:.1f} px" if center else "X: --  Y: -- px",
             f"dX: {offset[0]:+.1f}  dY: {offset[1]:+.1f} px" if offset else "dX: --  dY: -- px",
             f"Ref: {selection['reference_px'][0]:.1f}, {selection['reference_px'][1]:.1f} px", f"Image: {width} x {height}",
             f"Detection: {detection_ms:.1f} ms", f"Loop FPS: {fps:.1f}",
             f"Stable: {selection.get('stable_frames', 0)}  Jitter: {selection.get('center_jitter_px', 0):.1f} px",
             "SPACE: confirm" if armed and selection["valid"] else "Confirmation: blocked",
             "R: release  C: center  Q: quit", "Click image: reference point",
             "Pixel only; NO physical grasp"]
    for i, line in enumerate(lines):
        cv2.putText(canvas, line, (width + 12, 28 + i * 29),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1, cv2.LINE_AA)
    return canvas
