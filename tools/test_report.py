"""Summarize task_color JSONL sessions."""
import json
from pathlib import Path
def build_report(path):
    rows=[json.loads(x) for x in Path(path).read_text(encoding="utf-8").splitlines() if x.strip()]
    events=[r.get("event") for r in rows]; times=[r.get("detection_ms") for r in rows if isinstance(r.get("detection_ms"),(int,float))]
    q=[r for r in rows if r.get("event")=="coordinates" and isinstance(r.get("center_jitter_px"),(int,float))]
    confirmed=[r for r in rows if r.get("event")=="recognition_confirmed"]
    return {"task_code": next((r.get("raw") for r in rows if r.get("event")=="task_scanned"), None),
            "total_items": len(confirmed), "confirmed_items": len(confirmed),
            "wrong_color_rejections": events.count("invalid_task_code"), "lost_events": events.count("target_lost"),
            "recovered_events": events.count("target_recovered"), "reselection_events": events.count("target_locked")-1,
            "mean_detection_ms": sum(times)/len(times) if times else None, "max_detection_ms": max(times) if times else None,
            "mean_jitter_px": sum(r["center_jitter_px"] for r in q)/len(q) if q else None,
            "max_jitter_px": max((r["center_jitter_px"] for r in q), default=None),
            "completed": "sequence_complete" in events}
def main():
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("jsonl"); p.add_argument("--output")
    a=p.parse_args(); report=build_report(a.jsonl); output=Path(a.output or str(Path(a.jsonl).with_suffix(".summary.json")))
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8"); print(output)
if __name__ == "__main__": main()
