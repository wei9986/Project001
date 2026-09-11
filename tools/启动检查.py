"""启动检查：检查 Python、依赖和配置文件。先通过此检查再调试业务逻辑。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Dict, List, Literal


ROOT_DIR = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    ROOT_DIR / "config" / "颜色追踪配置.json",
    ROOT_DIR / "config" / "任务配置.json",
    ROOT_DIR / "config" / "仿真任务.json",
    ROOT_DIR / "config" / "站点标签.json",
    ROOT_DIR / "config" / "精定位配置.json",
    ROOT_DIR / "config" / "实机配置.json",
)

ProfileName = Literal["simulation", "live", "competition"]
VALID_PROFILES = ("simulation", "live", "competition")


def _validate_color_config(data: dict, relative: str, errors: List[str], _warnings: List[str]) -> None:
    tracking = data.get("tracking")
    if not isinstance(tracking, dict):
        errors.append(f"{relative}: tracking 必须是对象")
        return
    min_area = tracking.get("min_area")
    if not isinstance(min_area, (int, float)) or min_area < 0:
        errors.append(f"{relative}: tracking.min_area 必须为非负数")
    max_area = tracking.get("max_area")
    if max_area is not None:
        if not isinstance(max_area, (int, float)) or max_area < 0:
            errors.append(f"{relative}: tracking.max_area 必须为非负数")
        elif (
            isinstance(min_area, (int, float))
            and max_area > 0
            and max_area < min_area
        ):
            # max_area=0 表示不限制面积上限。
            errors.append(f"{relative}: tracking.max_area 必须 >= min_area（0 表示不限制）")
    colors = data.get("colors")
    if not isinstance(colors, dict) or not colors:
        errors.append(f"{relative}: colors 必须为非空对象")
        return
    for name, values in colors.items():
        if not isinstance(values, dict):
            errors.append(f"{relative}: colors.{name} 必须是对象")
            continue
        kernel_size = values.get("kernel_size")
        if kernel_size is not None and (not isinstance(kernel_size, int) or kernel_size < 1 or kernel_size % 2 == 0):
            errors.append(f"{relative}: colors.{name}.kernel_size 必须为正奇数")
        ranges = values.get("ranges")
        if not isinstance(ranges, list) or not ranges:
            errors.append(f"{relative}: colors.{name}.ranges 必须为非空列表")
            continue
        for index, pair in enumerate(ranges):
            if not isinstance(pair, list) or len(pair) != 2:
                errors.append(f"{relative}: colors.{name}.ranges[{index}] 必须是 [lower, upper]")
                continue
            lower, upper = pair
            for bound_name, bound in zip(("lower", "upper"), pair):
                if not isinstance(bound, list) or len(bound) != 3:
                    errors.append(
                        f"{relative}: colors.{name}.ranges[{index}].{bound_name} 必须是长度为 3 的 HSV"
                    )
                    continue
                hue, sat, val = bound
                if not isinstance(hue, (int, float)) or hue < 0 or hue > 179:
                    errors.append(
                        f"{relative}: colors.{name}.ranges[{index}].{bound_name}[0] H 需在 [0,179]"
                    )
                if not isinstance(sat, (int, float)) or sat < 0 or sat > 255:
                    errors.append(
                        f"{relative}: colors.{name}.ranges[{index}].{bound_name}[1] S 需在 [0,255]"
                    )
                if not isinstance(val, (int, float)) or val < 0 or val > 255:
                    errors.append(
                        f"{relative}: colors.{name}.ranges[{index}].{bound_name}[2] V 需在 [0,255]"
                    )
            if (
                isinstance(lower, list)
                and isinstance(upper, list)
                and len(lower) == 3
                and len(upper) == 3
                and all(isinstance(v, (int, float)) for v in (*lower, *upper))
            ):
                for channel, lo, hi in zip(("H", "S", "V"), lower, upper):
                    if lo > hi:
                        errors.append(
                            f"{relative}: colors.{name}.ranges[{index}] {channel} lower 必须 <= upper"
                        )


def _validate_station_config(data: dict, relative: str, errors: List[str], _warnings: List[str]) -> None:
    stations = data.get("stations")
    if not isinstance(stations, dict):
        errors.append(f"{relative}: stations 必须是对象")
    else:
        for tag_id in stations:
            try:
                int(tag_id)
            except (TypeError, ValueError):
                errors.append(f"{relative}: stations 键必须是整数 tag_id，实际 {tag_id!r}")
    tag_size = data.get("tag_size_mm")
    if not isinstance(tag_size, (int, float)) or tag_size <= 0:
        errors.append(f"{relative}: tag_size_mm 必须为正数")
    dictionary = data.get("dictionary")
    if dictionary is not None:
        if not isinstance(dictionary, str) or not dictionary:
            errors.append(f"{relative}: dictionary 必须为非空字符串")
        elif importlib.util.find_spec("cv2") is not None:
            import cv2

            const_name = dictionary if dictionary.startswith("DICT_") else f"DICT_{dictionary}"
            if not hasattr(cv2.aruco, const_name) and not hasattr(cv2.aruco, dictionary):
                errors.append(f"{relative}: dictionary 不是 OpenCV aruco 支持名: {dictionary}")


def _validate_fine_config(data: dict, relative: str, errors: List[str], _warnings: List[str]) -> None:
    required_samples = data.get("required_samples")
    if not isinstance(required_samples, int) or required_samples < 2:
        errors.append(f"{relative}: required_samples 必须为 >= 2 的整数")
    for key in ("max_spread_mm", "max_step_mm", "timeout_ms", "lost_timeout_ms"):
        value = data.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            errors.append(f"{relative}: {key} 必须为正数")


def _resolve_config_path(value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else ROOT_DIR / candidate


def _validate_task_config(data: dict, relative: str, errors: List[str], warnings: List[str]) -> None:
    """任务配置.json：条码坐标映射与站点位姿字段校验。"""
    mapping = data.get("barcode_mapping")
    if not isinstance(mapping, dict):
        errors.append(f"{relative}: barcode_mapping 必须是对象")
    else:
        for code, value in mapping.items():
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                errors.append(f"{relative}: barcode_mapping.{code} 必须是长度为 2 的坐标数组")
    stations = data.get("stations")
    if not isinstance(stations, dict):
        errors.append(f"{relative}: stations 必须是对象")
        return
    all_zero = True
    for station_id, value in stations.items():
        if not isinstance(value, dict) or not all(key in value for key in ("x_mm", "y_mm", "yaw_deg")):
            errors.append(f"{relative}: stations.{station_id} 缺少 x_mm/y_mm/yaw_deg")
            continue
        if any(float(value.get(key, 0.0)) != 0.0 for key in ("x_mm", "y_mm", "yaw_deg")):
            all_zero = False
    if all_zero and stations:
        warnings.append(f"{relative}: 全部站点坐标为 0，尚未实测冻结；实机运行前必须填写真实坐标")


def _validate_simulation_task(data: dict, relative: str, errors: List[str], _warnings: List[str]) -> None:
    """仿真任务.json：两批物料结构校验。"""
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        errors.append(f"{relative}: task_id 必须为非空字符串")
    for batch_name in ("first_batch", "second_batch"):
        batch = data.get(batch_name)
        if not isinstance(batch, list):
            errors.append(f"{relative}: {batch_name} 必须是数组")
            continue
        for index, item in enumerate(batch):
            if not isinstance(item, dict) or not all(
                isinstance(item.get(key), str) for key in ("material_id", "color", "ring_code")
            ):
                errors.append(f"{relative}: {batch_name}[{index}] 必须包含 material_id/color/ring_code 字符串")


def _validate_live_config(data: dict, relative: str, errors: List[str], warnings: List[str]) -> None:
    for section in ("camera", "scanner", "controller"):
        if not isinstance(data.get(section), dict):
            errors.append(f"{relative}: 缺少对象 {section}")
    path_keys = ("color_config", "station_config")
    for key in path_keys:
        if key not in data:
            continue
        if not isinstance(data[key], str):
            errors.append(f"{relative}: {key} 必须是路径字符串")
            continue
        path = _resolve_config_path(data[key])
        if not path.is_file():
            errors.append(f"{relative}: {key} 指向的文件不存在: {data[key]}")
    fine = data.get("fine_localization")
    if fine is not None and not isinstance(fine, (str, dict)):
        errors.append(f"{relative}: fine_localization 必须是路径或对象")
    elif isinstance(fine, str):
        path = _resolve_config_path(fine)
        if not path.is_file():
            errors.append(f"{relative}: fine_localization 指向的文件不存在: {fine}")
    calibration = data.get("calibration")
    if calibration is not None:
        if not isinstance(calibration, str):
            errors.append(f"{relative}: calibration 必须是路径字符串或 null")
        else:
            path = _resolve_config_path(calibration)
            if not path.is_file():
                errors.append(f"{relative}: calibration 指向的文件不存在: {calibration}")
    if bool(data.get("use_fine_localization", False)) and calibration is None:
        errors.append(f"{relative}: use_fine_localization=true 时必须提供 calibration 路径")
    colors = data.get("colors")
    if colors is not None and (not isinstance(colors, list) or not colors):
        errors.append(f"{relative}: colors 必须为非空数组")
    safety = data.get("safety")
    if safety is not None:
        if not isinstance(safety, dict):
            errors.append(f"{relative}: safety 必须是对象")
        else:
            tof = safety.get("minimum_tof_mm", 80.0)
            if not isinstance(tof, (int, float)) or tof <= 0:
                errors.append(f"{relative}: safety.minimum_tof_mm 必须为正数")
    protocol = data.get("protocol")
    if isinstance(protocol, dict):
        ack = protocol.get("ack_timeout_ms")
        done = protocol.get("done_timeout_ms")
        if ack is not None and (not isinstance(ack, (int, float)) or ack <= 0):
            errors.append(f"{relative}: protocol.ack_timeout_ms 必须为正数")
        if done is not None and (not isinstance(done, (int, float)) or done <= 0):
            errors.append(f"{relative}: protocol.done_timeout_ms 必须为正数")
        if isinstance(ack, (int, float)) and isinstance(done, (int, float)) and done < ack:
            warnings.append(f"{relative}: done_timeout_ms < ack_timeout_ms，动作易被误判超时")
    scanner = data.get("scanner")
    if isinstance(scanner, dict):
        max_line = scanner.get("max_line_length")
        if max_line is not None and (not isinstance(max_line, int) or max_line < 64):
            errors.append(f"{relative}: scanner.max_line_length 必须为 >= 64 的整数")


def _stations_all_zero(data: dict) -> bool:
    stations = data.get("stations")
    if not isinstance(stations, dict) or not stations:
        return False
    for value in stations.values():
        if not isinstance(value, dict):
            continue
        if any(float(value.get(key, 0.0)) != 0.0 for key in ("x_mm", "y_mm", "yaw_deg")):
            return False
    return True


def _apply_profile_gates(
    profile: ProfileName,
    filename: str,
    data: dict,
    relative: str,
    errors: List[str],
    warnings: List[str],
) -> None:
    """按 DEV/LIVE/COMPETITION 门禁升级严重级别。"""
    if filename == "任务配置.json" and _stations_all_zero(data):
        message = f"{relative}: 全部站点坐标为 0，尚未实测冻结；实机运行前必须填写真实坐标"
        if profile in ("live", "competition"):
            if message in warnings:
                warnings.remove(message)
            if message not in errors:
                errors.append(message)
        # simulation 保留 _validate_task_config 写入的 warning

    if filename != "实机配置.json":
        return

    calibration = data.get("calibration")
    if profile in ("live", "competition") and calibration is None:
        errors.append(f"{relative}: profile={profile} 要求提供 calibration 路径（当前为 null）")

    use_fine = bool(data.get("use_fine_localization", False))
    if profile == "competition" and not use_fine:
        errors.append(f"{relative}: profile=competition 要求 use_fine_localization=true")

    sensors = data.get("sensors")
    if isinstance(sensors, dict):
        tof = sensors.get("tof_distance_mm", None)
        grasp = sensors.get("grasp_confirmed", None)
        both_unwired = tof is None and grasp is None
        if both_unwired:
            message = f"{relative}: ToF 与抓取确认均未接入（sensors 均为 null）"
            if profile == "competition":
                errors.append(message)
            else:
                warnings.append(message)


VALIDATORS = {
    "颜色追踪配置.json": _validate_color_config,
    "站点标签.json": _validate_station_config,
    "精定位配置.json": _validate_fine_config,
    "实机配置.json": _validate_live_config,
    "任务配置.json": _validate_task_config,
    "仿真任务.json": _validate_simulation_task,
}

SIMULATION_MODULES = ("numpy", "cv2")
HARDWARE_MODULES = ("serial",)


def run_preflight(
    min_python: tuple[int, int] = (3, 10),
    require_hardware: bool = False,
    profile: ProfileName = "simulation",
) -> Dict[str, object]:
    """生成不打开硬件的完整启动检查报告。"""
    if profile not in VALID_PROFILES:
        raise ValueError(f"未知 profile: {profile!r}，可选 {VALID_PROFILES}")
    # live/competition 默认检查串口依赖；显式 require_hardware 也可单独打开。
    need_hardware = require_hardware or profile in ("live", "competition")
    errors: List[str] = []
    warnings: List[str] = []
    if sys.version_info[:2] < min_python:
        errors.append(f"Python 版本过低：需要 >= {min_python[0]}.{min_python[1]}")
    required_modules = SIMULATION_MODULES + (HARDWARE_MODULES if need_hardware else ())
    missing_modules = [name for name in required_modules if importlib.util.find_spec(name) is None]
    optional_missing_modules = [name for name in HARDWARE_MODULES if importlib.util.find_spec(name) is None]
    if missing_modules:
        errors.append(f"缺少依赖：{', '.join(missing_modules)}")
    missing_files = [str(path.relative_to(ROOT_DIR)) for path in REQUIRED_FILES if not path.is_file()]
    if missing_files:
        errors.append(f"缺少配置文件：{', '.join(missing_files)}")
    invalid_json: List[str] = []
    loaded: Dict[str, dict] = {}
    for path in REQUIRED_FILES:
        if not path.is_file():
            continue
        relative = str(path.relative_to(ROOT_DIR))
        try:
            with path.open("r", encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            invalid_json.append(f"{path.name}: {exc}")
            continue
        if not isinstance(data, dict):
            invalid_json.append(f"{path.name}: 根节点必须是 JSON 对象")
            continue
        loaded[path.name] = data
        validator = VALIDATORS.get(path.name)
        if validator is not None:
            validator(data, relative, errors, warnings)
        _apply_profile_gates(profile, path.name, data, relative, errors, warnings)
    if invalid_json:
        errors.extend(invalid_json)
    return {
        "passed": not errors,
        "profile": profile,
        "python": ".".join(str(part) for part in sys.version_info[:3]),
        "missing_modules": missing_modules,
        "optional_missing_modules": optional_missing_modules,
        "missing_files": missing_files,
        "errors": errors,
        "warnings": warnings,
    }


def main() -> int:
    """打印报告，并用退出码表示是否可启动。"""
    parser = argparse.ArgumentParser(description="视觉模块启动前检查")
    parser.add_argument("--json", action="store_true", help="输出 JSON 报告")
    parser.add_argument("--require-hardware", action="store_true", help="同时检查真实串口依赖")
    parser.add_argument(
        "--profile",
        choices=VALID_PROFILES,
        default="simulation",
        help="门禁级别：simulation（开发）/ live（实机）/ competition（比赛）",
    )
    args = parser.parse_args()
    report = run_preflight(require_hardware=args.require_hardware, profile=args.profile)
    print(json.dumps(report, ensure_ascii=False, indent=None if args.json else 2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
