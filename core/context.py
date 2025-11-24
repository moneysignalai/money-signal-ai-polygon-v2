# core/context.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)


@dataclass
class MarketContext:
    """
    Lightweight snapshot of overall market regime that other bots can use
    for filtering / conviction tweaks.

    - as_of:      when this context snapshot was computed (UTC)
    - trend:      string label like "bull", "bear", "chop", "unknown"
    - vol_regime: string label like "low", "normal", "high"
    - risk_off:   boolean "are we in a risk-off environment?"
    """
    as_of: datetime
    trend: str
    vol_regime: str
    risk_off: bool


def compute_market_context(client: PolygonClient) -> MarketContext:
    """
    Compute a basic MarketContext.

    NOTE: This implementation is intentionally simple and robust so that
    the main loop can never die here. If you had more detailed logic
    before (e.g. based on SPY / VIX / TLT), that can be re‑added on top
    of this, as long as we *always* construct MarketContext with `as_of`.
    """
    now = datetime.now(timezone.utc)

    # --- Placeholder logic: keep it safe & boring ---
    # In case you want something a bit more nuanced later, you can
    # fetch aggregates here and set these three fields accordingly.
    trend = "unknown"
    vol_regime = "normal"
    risk_off = False

    log.debug(
        "Computed MarketContext(as_of=%s, trend=%s, vol_regime=%s, risk_off=%s)",
        now,
        trend,
        vol_regime,
        risk_off,
    )

    return MarketContext(
        as_of=now,
        trend=trend,
        vol_regime=vol_regime,
        risk_off=risk_off,
    )