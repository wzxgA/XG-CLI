"""SmartRouter 后处理规则引擎（可解释的安全兜底）。

规则与执行顺序来源：XG-docs/smart-docs/ADAPTIVE_ROUTING.md §7（1→6，先升后降、最后防降级）。

与文档代码的一处有意差异：防降级规则按注释意图实现为
``t = max(t, prev_tier - 1)``（600s 内最多比上一轮低 1 档）。
文档示例代码的 ``t = min(t, prev_tier + 1)`` 实际限制的是"上升"，
与其注释"不能比上一轮低超过 1 档"矛盾，此处以注释语义为准。
"""

from __future__ import annotations

from .features import code_blocks
from .keywords import KEYWORDS

TIER = ["Basic", "Enhanced", "Superior", "Ultimate"]

# 防降级窗口（秒）：同会话内两次路由间隔小于该值时，档位最多下降 1 档
ANTI_DOWNGRADE_WINDOW = 600


def hit(text: str, cat: str) -> bool:
    """判断文本是否命中某类关键词（英文转小写后子串匹配）。"""
    t = text.lower()
    return any(k in t for k in KEYWORDS[cat])


def postprocess(tier_idx: int, text: str, f: dict,
                prev_tier: int | None = None, prev_ts: float | None = None,
                ts: float = 0.0, context_tokens: int = 0,
                learned_rules=None) -> int:
    """按顺序应用规则，返回最终档位索引 0..3。

    ``learned_rules``（adaptive.LearnedRules，可选）在 6 条规则之后、且仅
    在未被硬规则强制时应用：命中的 ±1 档微调永不覆盖风险/闲聊/长上下文硬规则
    （phase-04 A1 验收约束）。
    """
    t = tier_idx
    forced = False  # 是否已被某条硬规则强制锁定（learned_rules 不再覆盖）

    # 1) 风险旗标 → 强制 >= Superior
    if hit(text, "risk"):
        t = max(t, 2)
        forced = True

    # 2) 长上下文旗标 → 强制 >= Superior
    blocks = code_blocks(text)
    if (len(text) > 6000
            or (blocks and max(len(b) for b in blocks) > 1500)
            or context_tokens > 2000):
        t = max(t, 2)
        forced = True

    # 3) 架构旗标 → 升一档
    if hit(text, "arch"):
        t = min(t + 1, 3)

    # 4) 调试旗标 → 升一档
    if hit(text, "debug"):
        t = min(t + 1, 3)

    # 5) 简短闲聊旗标 → 强制 <= Basic
    if (hit(text, "chatty") and f["num_code_blocks"] == 0
            and not hit(text, "teach") and not hit(text, "arch")
            and not hit(text, "risk") and not hit(text, "planning")):
        t = 0
        forced = True

    # 6) 防降级：同会话 600s 内，档位最多比上一轮低 1 档
    if prev_tier is not None and prev_ts is not None and ts - prev_ts < ANTI_DOWNGRADE_WINDOW:
        t = max(t, prev_tier - 1)

    # 7) learned_rules 局部规则（第 4 期 A1）：仅未被硬规则强制时 ±1 档微调
    if not forced and learned_rules is not None:
        action = learned_rules.apply(f)
        if action > 0:
            t = min(t + 1, 3)
        elif action < 0:
            t = max(t - 1, 0)

    return t
