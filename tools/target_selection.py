"""Pixel-only target selection; caller supplies monotonic milliseconds."""
import json
import math
from pathlib import Path


def load_selection_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    keys = {"reference_normalized", "lost_timeout_ms", "max_processing_age_ms",
            "coordinate_log_interval_ms"}
    if not isinstance(config, dict) or not keys.issubset(config):
        raise ValueError("Selection config must contain: " + ", ".join(sorted(keys)))
    ref = config["reference_normalized"]
    def numeric(value):
        return type(value) in (int, float) and math.isfinite(value)
    if not isinstance(ref, list) or len(ref) != 2 or not all(numeric(v) and 0 <= v <= 1 for v in ref):
        raise ValueError("reference_normalized must be two finite numbers in [0, 1]")
    for key in keys - {"reference_normalized"}:
        if not numeric(config[key]) or config[key] <= 0:
            raise ValueError(key + " must be finite and positive")
    return config


class TargetSelector:
    def __init__(self, lost_timeout_ms=500, max_processing_age_ms=200):
        self.lost_timeout_ms = lost_timeout_ms
        self.max_processing_age_ms = max_processing_age_ms
        self.target_id = None
        self.last_seen = None
        self.state = "SEARCHING"
        self.events = []

    def reset(self, reason="manual_reset"):
        if self.target_id is not None:
            self.events.append(dict(event="target_released", target_id=self.target_id, reason=reason))
        self.target_id = None
        self.last_seen = None
        self.state = "SEARCHING"

    def drain_events(self):
        events, self.events = self.events, []
        return events

    def update(self, result, color, reference, now_ms, observed_ms, frame_index):
        info = result.get("colors", {}).get(color, {})
        age = now_ms - observed_ms
        reason = "no_confirmed_target"
        candidates = []
        if info.get("timed_out"):
            reason = "detection_timeout"
        elif age < 0 or age > self.max_processing_age_ms:
            reason = "processing_timeout"
        else:
            candidates = [t for t in info.get("valid_targets", [])
                          if t.get("color") == color and t.get("valid")
                          and t.get("status") == "confirmed"]
        # Expiry must precede matching: an ID seen after the deadline is a new acquisition.
        if self.target_id is not None and now_ms - self.last_seen >= self.lost_timeout_ms:
            self.reset("lost_timeout")
        target = None
        if self.target_id is None and candidates:
            target = min(candidates, key=lambda t: (math.dist(t["center"], reference), t["target_id"]))
            self.target_id = target["target_id"]
            self.events.append(dict(event="target_locked", target_id=self.target_id))
        elif self.target_id is not None:
            target = next((t for t in candidates if t["target_id"] == self.target_id), None)
        if target is not None:
            if self.state == "LOST":
                self.events.append(dict(event="target_recovered", target_id=self.target_id))
            self.state = "LOCKED"
            self.last_seen = now_ms
            reason = "current_confirmed_target"
        elif self.target_id is not None:
            if self.state != "LOST":
                self.events.append(dict(event="target_lost", target_id=self.target_id, reason=reason))
            self.state = "LOST"
        center = list(target["center"]) if target is not None else None
        return dict(state=self.state, target_id=self.target_id, color=color,
                    valid=target is not None, center_px=center,
                    offset_px=[center[i] - reference[i] for i in range(2)] if center else None,
                    reference_px=list(reference), frame_index=frame_index,
                    observed_monotonic_ms=observed_ms, reason=reason)


class ConfirmationGate:
    """OpenCV has no key-up event: re-arm after a quiet interval, not on repeats."""
    def __init__(self):
        self.blocked_since = None
        self.last_space = None
        self.armed = True

    def block(self, now_ms):
        self.blocked_since = now_ms
        self.armed = False

    def update(self, key, now_ms, valid):
        if key != 32 and not self.armed:
            last = max(t for t in (self.blocked_since, self.last_space) if t is not None)
            if now_ms - last >= 500 and valid:
                self.armed = True
        if key == 32:
            accepted = self.armed and valid
            self.last_space = now_ms
            self.block(now_ms)
            return accepted
        return False
