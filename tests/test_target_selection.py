import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from itertools import count
import numpy as np
from tools.target_selection import TargetSelector, ConfirmationGate, load_selection_config
from tools.task_color_display import render_display


def result(*targets, timeout=False):
    return {"colors": {"red": {"timed_out": timeout, "valid_targets": list(targets)}}}


def target(i, x, y=10, color="red", valid=True):
    return dict(target_id=i, center=[x, y], color=color, valid=valid, status="confirmed")


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.s = TargetSelector()

    def update(self, t, *targets, age=0, timeout=False):
        return self.s.update(result(*targets, timeout=timeout), "red", [10, 10], t, t-age, int(t))

    def test_nearest_tie_and_keep(self):
        self.assertEqual(self.update(0, target(3, 12), target(2, 8))["target_id"], 2)
        v = self.update(10, target(2, 30), target(3, 10))
        self.assertEqual(v["target_id"], 2)
        self.assertEqual(v["offset_px"], [20, 0])

    def test_loss_recovery_and_expiry(self):
        self.update(0, target(1, 10))
        v = self.update(100, target(2, 10))
        self.assertEqual(v["state"], "LOST")
        self.assertIsNone(v["center_px"])
        self.assertIsNone(v["offset_px"])
        self.assertEqual(self.update(200, target(1, 15))["state"], "LOCKED")
        self.assertEqual(self.update(699, target(2, 10))["target_id"], 1)
        self.assertEqual(self.update(700, target(2, 10))["target_id"], 2)
        events = [e["event"] for e in self.s.drain_events()]
        self.assertIn("target_recovered", events)
        self.assertIn("target_released", events)

    def test_wrong_invalid_stale_timeout(self):
        for kwargs in ({"age": 201}, {"timeout": True}):
            self.assertFalse(self.update(300, target(1, 10), **kwargs)["valid"])
        self.assertFalse(self.update(301, target(1, 10, color="blue"), target(2, 10, valid=False))["valid"])

    def test_reset_and_reference(self):
        self.update(0, target(1, 10))
        self.s.reset("task_changed")
        v = self.s.update(result(target(1, 10), target(2, 30)), "red", [30, 5], 1, 1, 2)
        self.assertEqual(v["target_id"], 2)
        self.assertEqual(v["offset_px"], [0, 5])

    def test_gate_repeats_and_invalid(self):
        g = ConfirmationGate()
        self.assertTrue(g.update(32, 0, True))
        for t in (100, 400, 800, 1200):
            self.assertFalse(g.update(32, t, True))
        self.assertFalse(g.update(-1, 1700, False))
        self.assertFalse(g.armed)
        g.update(-1, 1800, True)
        self.assertTrue(g.update(32, 1810, True))

    def test_render_preserves_coordinates(self):
        frame = np.full((240, 320, 3), 80, dtype=np.uint8)
        v = self.update(0, target(1, 100))
        image = render_display(frame, v, "123+231", "Batch 1: 1/3", 5, 30, True)
        self.assertEqual(image.shape, (520, 710, 3))
        self.assertTrue(np.array_equal(image[200, 200], frame[200, 200]))
        self.assertEqual(v["center_px"], [100, 10])

    def test_default_config(self):
        self.assertEqual(load_selection_config("config/task_color_test.json")["lost_timeout_ms"], 500)

    def test_bad_configs(self):
        base = load_selection_config("config/task_color_test.json")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            for field, value in (("reference_normalized", [1.2, 0]), ("lost_timeout_ms", 0),
                                 ("max_processing_age_ms", True), ("coordinate_log_interval_ms", float("nan"))):
                path.write_text(json.dumps(dict(base, **{field: value})), encoding="utf-8")
                with self.subTest(field=field), self.assertRaises(ValueError):
                    load_selection_config(path)

    def test_full_loop_six_unique_confirmations(self):
        from tools import task_color_test as app
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        root = Path.cwd()
        ticks = count()
        keys = [-1] + sum(([-1]*40 + [32] for _ in range(6)), []) + [ord("q")]
        camera = MagicMock()
        camera.read.side_effect = lambda: (True, np.zeros((240, 320, 3), dtype=np.uint8))
        class Tracker:
            def __init__(self, colors, **kwargs):
                self.color = colors[0]
            def track(self, frame):
                return frame, {"elapsed_ms": 1, "colors": {self.color: {"valid_targets": [
                    target(1, 160, 120, self.color), target(2, 280, 120, self.color)]}}}
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "Path", side_effect=lambda p: Path(folder)/"logs" if p == "logs" else Path(p)), \
             patch("sys.argv", ["tool", "--config", str(root/"config/颜色追踪配置.json"),
                                "--selection-config", str(root/"config/task_color_test.json")]), \
             patch.object(app.cv2, "VideoCapture", return_value=camera), \
             patch.object(app, "QRCodeReader") as reader, patch.object(app, "ColorTracker", Tracker), \
             patch.object(app.time, "monotonic", side_effect=lambda: next(ticks)*0.005), \
             patch.object(app.cv2, "namedWindow"), patch.object(app.cv2, "setMouseCallback"), \
             patch.object(app.cv2, "imshow"), patch.object(app.cv2, "destroyAllWindows"), \
             patch.object(app.cv2, "waitKey", side_effect=keys):
            # 官方四组任务码：颜色 143=红绿蓝，环位 123，第三组 431 与第一批同色集。
            # 按当前 symbol_to_color（1红 4绿 3蓝），顺序为 red green blue green blue red。
            reader.return_value.read.return_value = [SimpleNamespace(raw_value="143+123+431+123")]
            app.main()
            rows = [json.loads(line) for line in next((Path(folder)/"logs").glob("*.jsonl")).read_text().splitlines()]
            confirmed = [r for r in rows if r["event"] == "recognition_confirmed"]
            self.assertEqual([r["material"]["color"] for r in confirmed], ["red", "green", "blue", "green", "blue", "red"])
            self.assertTrue(all(r["selected_target"]["target_id"] == 1 for r in confirmed))
            self.assertTrue(rows[-1]["completed"])
            camera.release.assert_called_once()


if __name__ == "__main__":
    unittest.main()
