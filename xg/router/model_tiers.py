"""SmartRouter 档位→模型映射。

第 1 期子步骤 A 的骨架版本：尚未接入 ConfigManager，
``tiers_config`` 为空时所有档位回落到当前生效的 fallback (provider, model)。
子步骤 B 将接入 ``ConfigManager.smart_router_config()`` 并补充
provider 存在性校验与 API Key 检查（见 states/phase-01 文档）。
"""

from __future__ import annotations

from dataclasses import dataclass

from .postprocess import TIER

TIER_NAMES = TIER


@dataclass(frozen=True)
class TierTarget:
    """某档位解析出的模型目标。"""

    tier: str            # Basic / Enhanced / Superior / Ultimate
    provider: str
    model: str
    configured: bool     # 是否来自用户显式配置（False 表示回落默认）


def resolve(tier_idx: int, fallback_provider: str, fallback_model: str,
            tiers_config: dict | None = None) -> TierTarget:
    """把档位索引解析成 (provider, model)。

    ``tiers_config`` 形如 ``{"Basic": {"provider": "glm", "model": "glm-4-flash"}, ...}``；
    未配置的档位回落到 fallback。provider 存在性 / API Key 校验在子步骤 B 接入。
    """
    tier_name = TIER_NAMES[tier_idx]
    cfg = (tiers_config or {}).get(tier_name) or {}
    provider = cfg.get("provider") or fallback_provider
    model = cfg.get("model") or fallback_model
    return TierTarget(tier=tier_name, provider=provider, model=model,
                      configured=bool(cfg.get("provider") or cfg.get("model")))
