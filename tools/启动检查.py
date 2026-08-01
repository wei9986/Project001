"""启动检查：检查 Python、依赖和配置文件。先通过此检查再调试业务逻辑。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Dict, List


ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    ROOT_DIR / "config" / "颜色追踪配置.json",
    ROOT_DIR / "config" / "任务配置.json",
    ROOT_DIR / "config" / "仿真任务.json",
    ROOT_DIR / "config" / "站点标签.json",
)
SIMULATION_MODULES = ("numpy", "cv2")
HARDWARE_MODULES = ("serial",)


def run_preflight(min_python: tuple[int, int] = (3, 10), require_hardware: bool = False) -> Dict[str, object]:
    """生成不打开硬件的完整启动检查报告。"""
    errors: List[str] = []
    if sys.version_info[:2] < min_python:
        errors.append(f"Python 版本过低：需要 >= {min_python[0]}.{min_python[1]}")
    required_modules = SIMULATION_MODULES + (HARDWARE_MODULES if require_hardware else ())
    missing_modules = [name for name in required_modules if importlib.util.find_spec(name) is None]
    optional_missing_modules = [name for name in HARDWARE_MODULES if importlib.util.find_spec(name) is None]
    if missing_modules:
        errors.append(f"缺少依赖：{', '.join(missing_modules)}")
    missing_files = [str(path.relative_to(ROOT_DIR)) for path in REQUIRED_FILES if not path.is_file()]
    if missing_files:
        errors.append(f"缺少配置文件：{', '.join(missing_files)}")
    invalid_json: List[str] = []
    for path in REQUIRED_FILES:
        if path.is_file():
            try:
                with path.open("r", encoding="utf-8") as file:
                    json.load(file)
            except (OSError, json.JSONDecodeError) as exc:
                invalid_json.append(f"{path.name}: {exc}")
    if invalid_json:
        errors.extend(invalid_json)
    return {
        "passed": not errors,
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "missing_modules": missing_modules,
        "optional_missing_modules": optional_missing_modules,
        "missing_files": missing_files,
        "errors": errors,
    }


def main() -> int:
    """打印报告，并用退出码表示是否可启动。"""
    parser = argparse.ArgumentParser(description="视觉模块启动前检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument("--require-hardware", action="store_true", help="同时检查真实串口依赖")
    args = parser.parse_args()
    report = run_preflight(require_hardware=args.require_hardware)
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
