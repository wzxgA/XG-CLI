"""adaptive 数据的持久化工具：原子写、损坏回退读取、目录懒创建。

设计依据：phase-03 步骤 A。red line 为"损坏安全"——任何读失败都回退默认
值，绝不抛错影响启动；写文件用 tmp + os.replace（同目录原子覆盖），
避免写一半导致 JSON 损坏。
"""

from __future__ import annotations

import json
import os
import tempfile
import traceback
from pathlib import Path
from typing import Any

from . import data_dir

# 三个数据文件名
FEEDBACK_LOG = "feedback.log"        # JSONL 追加，只增不改
CALIBRATION_JSON = "calibration.json"  # 校准结果，原子写
LEARNED_RULES_JSON = "learned_rules.json"  # 自学习规则，re_learn 落盘（见 learned_rules.py）


def feedback_log_path() -> Path:
    return data_dir() / FEEDBACK_LOG


def calibration_path() -> Path:
    return data_dir() / CALIBRATION_JSON


def learned_rules_path() -> Path:
    return data_dir() / LEARNED_RULES_JSON


def reset_adaptive_data() -> list[str]:
    """清空校准与自学习规则（feedback.log 保留作历史）。返回被删除的文件名。

    phase-04 A3 的 `/smartRouter reset`。删除后校准/规则回到空态，
    等价于"删掉 calibration.json + learned_rules.json 即回第 1 期行为"。
    """
    removed: list[str] = []
    for p in (calibration_path(), learned_rules_path()):
        try:
            if p.exists():
                p.unlink()
                removed.append(p.name)
        except OSError:
            pass
    return removed


def ensure_dir() -> Path:
    """懒创建数据目录；重复调用是幂等的。"""
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_replace(path: Path, payload: str) -> None:
    """写 tmp 文件后 os.replace 覆盖目标，保证同目录内原子替换。"""
    ensure_dir()
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        # 清理残留 tmp，避免堆积
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, obj: Any) -> None:
    """把任意 JSON 可序列化对象原子写到 ``path``。"""
    _atomic_replace(path, json.dumps(obj, ensure_ascii=False, indent=2))


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """JSONL 追加写入一行（append 模式，不覆盖既有内容）。"""
    ensure_dir()
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        fh.write("\n")


def read_json_safe(path: Path, default: Any = None) -> Any:
    """读 JSON，文件不存在 / 解析失败 / 结构异常时返回 ``default``，不抛错。"""
    if not path.exists():
        return default
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        traceback.print_exc() if os.environ.get("XG_DEBUG") else None
        return default