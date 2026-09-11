# -*- coding: utf-8 -*-
"""共享配置加载：项目根路径解析、JSON 读取与默认值深层合并。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Union


PathLike = Union[str, Path]


def project_root() -> Path:
    """返回工程根目录（vision/ 的上一级）。"""
    return Path(__file__).resolve().parents[1]


def resolve_path(path: Optional[PathLike], base: Optional[Path] = None) -> Optional[Path]:
    """相对路径拼到 base（默认项目根）；绝对路径原样返回；None 保持 None。"""
    if path is None:
        return None
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    root = base if base is not None else project_root()
    return root / candidate


def load_json(path: PathLike) -> Dict[str, Any]:
    """读取 UTF-8 JSON 对象。"""
    with Path(path).open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"配置文件根节点必须是 JSON 对象: {path}")
    return data


def deep_merge(defaults: Dict[str, Any], overrides: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """递归合并：dict 深入合并，list/标量整段替换。不修改入参。"""
    result = deepcopy(defaults)
    if not overrides:
        return result
    _merge_into(result, overrides)
    return result


def _merge_into(target: Dict[str, Any], overrides: Dict[str, Any]) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_into(target[key], value)
        else:
            target[key] = deepcopy(value)


def load_merged_config(
    path: Optional[PathLike],
    defaults: Dict[str, Any],
    *,
    base: Optional[Path] = None,
) -> Dict[str, Any]:
    """读取可选 JSON 并与 defaults 合并；path 为 None 时返回 defaults 副本。"""
    config = deep_merge(defaults, None)
    if path is None:
        return config
    resolved = resolve_path(path, base)
    if resolved is None:
        return config
    return deep_merge(config, load_json(resolved))


def normalize_source(source: object) -> object:
    """把配置里常见的 '0' 转成整数摄像头索引，避免部分平台 VideoCapture('0') 失败。

    字符串数字转为 int，其余原样返回（视频文件路径等保持不变）。
    """
    if isinstance(source, str):
        text = source.strip()
        return int(text) if text.isdigit() else text
    return source
