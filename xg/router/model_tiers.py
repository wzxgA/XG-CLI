"""SmartRouter 档位→模型映射。

接入 ConfigManager（phase-01 子步骤 B）：
- ``tiers_config`` 形如 ``{"Basic": {"provider": "glm", "model": "glm-4-flash"}, ...}``，
  通常来自 ``ConfigManager.smart_router_config()["tiers"]``；
- 传入 ``manager`` 时启用完整校验链：provider 必须可解析、API Key 必须已配置，
  否则该档回落 fallback（active 模型）并标记 ``configured=False``；
- 未配置的档位同样回落 fallback。

校验失败的档位在 UI 上应显示为不可用（阶段 2 的暗淡 + (x) 标记）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .postprocess import TIER

if TYPE_CHECKING:
    from xg.config.manager import ConfigManager

TIER_NAMES = TIER


@dataclass(frozen=True)
class TierTarget:
    """某档位解析出的模型目标。"""

    tier: str            # Basic / Enhanced / Superior / Ultimate
    provider: str
    model: str
    configured: bool     # 显式配置且校验通过（False 表示回落默认/校验失败）


def resolve(tier_idx: int, fallback_provider: str, fallback_model: str,
            tiers_config: dict | None = None,
            manager: "ConfigManager | None" = None) -> TierTarget:
    """把档位索引解析成 (provider, model)。

    回落顺序：显式配置的 provider/model → fallback（当前 active）。
    传入 ``manager`` 时对显式配置做校验：
    1. provider 必须在 ``manager.provider_names()`` 中（内置 + 自定义）；
    2. ``manager.resolve_api_key()`` 必须返回非空（占位值视为未配置）。
    校验不过 → 整档回落 fallback，``configured=False``。
    """
    tier_name = TIER_NAMES[tier_idx]
    cfg = (tiers_config or {}).get(tier_name) or {}
    provider = str(cfg.get("provider") or "") or fallback_provider
    model = str(cfg.get("model") or "") or fallback_model
    configured = bool(cfg.get("provider") or cfg.get("model"))

    if configured and manager is not None and (provider, model) != (fallback_provider, fallback_model):
        prov = manager.resolve_provider(provider)
        if prov is None or not manager.resolve_api_key(prov):
            # provider 不存在或 API Key 未配置：整档回落 active
            return TierTarget(tier=tier_name, provider=fallback_provider,
                              model=fallback_model, configured=False)
    return TierTarget(tier=tier_name, provider=provider, model=model, configured=configured)
