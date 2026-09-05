"""训练 ML 模型命令的执行层：/train 的参数解析、确认提示与子进程流式执行。

设计口径（对齐 05 文档与现有 /provider 红线）：
- 训练仍是显式手动触发：不带 --yes 只返回确认提示，绝不静默跑。
- 训练跑在独立子进程 :file:`tools/train_router.py`，本层只「spawn + 收日志」，
  不 import 训练逻辑，规避 torch 等重依赖进主进程。
- inline 走同步版 :func:`run_training_sync`；TUI 走异步版 :func:`run_training_async`，
  两者共用参数解析与 argv 构造，日志都逐行流式上报。
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_TRAIN_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "train_router.py"
_TRAIN_DEPS = ("lightgbm", "sklearn", "joblib", "numpy")


@dataclass
class TrainPlan:
    dataset: str | None = None
    output: str | None = None
    overwrite: bool = False
    feedback_only: bool = False


def _default_output() -> Path:
    """默认产物路径：与 feedback.log 同目录的 router.lgb（对齐 train_router）。"""
    from xg.adaptive.store import data_dir  # noqa: PLC0415

    return data_dir() / "router.lgb"


def check_train_deps() -> str | None:
    """缺训练依赖时返回安装提示；全部可用返回 None。

    注意：语义编码器（bge）非必需——缺失时 train_router 自动回退 TF-IDF 特征。
    """
    missing = [m for m in _TRAIN_DEPS if _import_safe(m) is False]
    if missing:
        return (
            "缺少训练依赖："
            + ", ".join(missing)
            + "。请先 `pip install -e .` 安装核心依赖后再试。"
        )
    return None


def _import_safe(mod: str) -> bool:
    try:
        __import__(mod)
        return True
    except Exception:
        return False


def parse_train_command(raw: str) -> tuple[TrainPlan, str | None]:
    """解析 `/train [...]` 参数，返回 (plan, error)。错误时 plan 不保证完整。"""
    plan = TrainPlan()
    tokens = raw.split(maxsplit=1)[1].split() if len(raw.split(maxsplit=1)) > 1 else []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t in ("--yes", "-y"):
            plan.overwrite = True
        elif t == "--feedback-only":
            plan.feedback_only = True
        elif t in ("--output", "--out", "-o"):
            i += 1
            if i >= len(tokens):
                return plan, "--output 需要一个路径参数"
            plan.output = tokens[i]
        elif t.startswith("-"):
            return plan, f"未知参数: {t}"
        else:
            if plan.dataset is not None:
                return plan, f"多余参数: {t}"
            plan.dataset = t
        i += 1
    if plan.dataset and plan.feedback_only:
        return plan, "不能同时指定数据集与 --feedback-only"
    if not plan.dataset and not plan.feedback_only:
        plan.feedback_only = True  # 缺省：仅用 feedback.log
    if plan.output is not None:
        plan.output = plan.output.strip('"\'')
    return plan, None


def confirmation_message(plan: TrainPlan) -> str:
    """未带 --yes 时返回的确认提示：告知将如何训练、需加 --yes 才执行。"""
    src = "反馈日志 feedback.log" if plan.feedback_only else f"数据集 {plan.dataset}"
    out = plan.output or str(_default_output())
    return (
        "确认将运行 SmartRouter 训练：\n"
        f"  样本来源: {src}\n"
        f"  产物路径: {out}\n"
        "完整数据集 / feedback 样本会被去噪后用于训练；训练为手动触发，"
        "不会自动运行。确认执行请在命令末尾加 --yes。"
    )


def build_argv(plan: TrainPlan) -> list[str]:
    """构造 tools/train_router.py 的 argv（output 缺省时不传，交给脚本默认）。"""
    argv = [sys.executable, str(_TRAIN_SCRIPT)]
    if plan.output:
        argv += ["--out", plan.output]
    if plan.feedback_only:
        argv += ["--feedback-only"]
    elif plan.dataset:
        argv += [plan.dataset]
    return argv


def _build_env() -> dict[str, str]:
    """让子进程以 UTF-8 输出（Windows 默认 GBK 会与读取端 utf-8 解码冲突导致乱码）。"""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run_training_sync(
    plan: TrainPlan, on_line: callable | None = None
) -> tuple[bool, list[str]]:
    """同步执行训练（inline 用）。逐行流式读子进程输出交给 on_line，返回 (ok, lines)。"""
    argv = build_argv(plan)
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_build_env(),
    )
    lines: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            lines.append(line)
            if on_line is not None:
                on_line(line)
    finally:
        rc = proc.wait()
    return rc == 0, lines


async def run_training_async(
    plan: TrainPlan, on_line: callable | None = None
) -> tuple[bool, list[str]]:
    """异步执行训练（TUI / CommandService 用）。逐行流式上报，返回 (ok, lines)。"""
    argv = build_argv(plan)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_build_env(),
    )
    lines: list[str] = []
    assert proc.stdout is not None
    while True:
        raw = await proc.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\n")
        lines.append(line)
        if on_line is not None:
            on_line(line)
    await proc.wait()
    return (proc.returncode or 0) == 0, lines