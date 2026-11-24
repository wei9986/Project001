"""Frame-independent quality gate for confirmed pixel targets."""
from collections import deque
import math

class TargetQuality:
    def __init__(self, min_stable_frames=5, max_center_jitter_px=5.0, max_area_change_ratio=.30,
                 max_detection_age_ms=200):
        self.min_frames = int(min_stable_frames); self.max_jitter = float(max_center_jitter_px)
        self.max_area_change = float(max_area_change_ratio); self.max_age = float(max_detection_age_ms)
        self._history = deque(maxlen=max(self.min_frames, 2)); self._areas = deque(maxlen=2)
    def reset(self): self._history.clear(); self._areas.clear()
    def evaluate(self, target=None, *, now_ms=0, observed_ms=0, timed_out=False):
        if timed_out or now_ms-observed_ms > self.max_age:
            return self._result("INVALID", False, target, "detection_timeout")
        if not target or not target.get("valid") or target.get("status") != "confirmed":
            return self._result("SEARCHING", False, target, "no_confirmed_target")
        center = tuple(float(x) for x in target["center"]); area = float(target.get("area", 0))
        self._history.append(center); self._areas.append(area)
        if len(self._history) < self.min_frames:
            return self._result("TRACKING", False, target, "waiting_stable_frames")
        mean = tuple(sum(p[i] for p in self._history)/len(self._history) for i in (0,1))
        jitter = max(math.dist(p, mean) for p in self._history)
        change = abs(self._areas[-1]-self._areas[-2])/max(self._areas[-2], 1) if len(self._areas)>1 else 0
        if jitter > self.max_jitter: return self._result("INVALID", False, target, "center_jitter_too_large", jitter, change)
        if change > self.max_area_change: return self._result("INVALID", False, target, "area_change_too_large", jitter, change)
        return self._result("STABLE", True, target, "stable", jitter, change)
    def _result(self, state, valid, target, reason, jitter=0, change=0):
        center = list(target["center"]) if valid and target else None
        return {"quality_state": state, "valid": valid, "target_id": target.get("target_id") if target else None,
                "center_px": center, "offset_px": None, "stable_frames": len(self._history),
                "center_jitter_px": round(jitter, 3), "area_change_ratio": round(change, 3), "reason": reason}
