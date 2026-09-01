"""SmartRouter 路由核心包。

按输入文本的规则特征判断复杂度档位（Basic / Enhanced / Superior / Ultimate），
并解析出该档位应使用的 provider / model。

第 1 期子步骤 A：仅提供纯函数路由能力，尚未接入主循环与命令开关
（见 XG-docs/smart-docs/states/phase-01-smart-router-core.md）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .features import extract
from .model_tiers import TIER_NAMES, TierTarget, resolve
from .postprocess import postprocess
from .rule_router import RuleDecision, confidence, rule_route, rule_score


@dataclass(frozen=True)
class RouteResult:
    """一次完整路由的结果。"""

    tier: str                  # Basic / Enhanced / Superior / Ultimate
    tier_idx: int              # 0..3
    provider: str
    model: str
    configured: bool           # 档位是否来自用户显式配置
    confidence: float          # 0~1，规则路由的置信度估计
    score: float               # 规则加权总分
    hard_rule: bool            # 是否由硬规则直接决定
    features: dict = field(default_factory=dict)  # 特征快照（测试/调试/后续反馈采集用）


def _tier_index(prev_tier: str | int | None) -> int | None:
    """把档位名（或索引）统一转成索引；未知名称返回 None。"""
    if prev_tier is None:
        return None
    if isinstance(prev_tier, int):
        return prev_tier
    try:
        return TIER_NAMES.index(prev_tier)
    except ValueError:
        return None


def route(text: str, *,
          prev_tier: str | int | None = None,
          prev_ts: float | None = None,
          ts: float | None = None,
          context_tokens: int = 0,
          fallback_provider: str = "",
          fallback_model: str = "",
          tiers_config: dict | None = None) -> RouteResult:
    """对一段用户输入做完整路由：特征 → 规则打分 → 后处理 → 档位解析。"""
    f = extract(text)
    decision: RuleDecision = rule_route(f)
    final_idx = postprocess(
        decision.tier_idx, text, f,
        prev_tier=_tier_index(prev_tier), prev_ts=prev_ts,
        ts=ts if ts is not None else time.time(),
        context_tokens=context_tokens,
    )
    target: TierTarget = resolve(final_idx, fallback_provider, fallback_model, tiers_config)
    return RouteResult(
        tier=target.tier,
        tier_idx=final_idx,
        provider=target.provider,
        model=target.model,
        configured=target.configured,
        confidence=confidence(decision),
        score=decision.score,
        hard_rule=decision.hard_rule,
        features=f,
    )


__all__ = [
    "RouteResult",
    "TierTarget",
    "TIER_NAMES",
    "extract",
    "route",
    "resolve",
    "rule_route",
    "rule_score",
    "postprocess",
    "KEYWORDS",
]
