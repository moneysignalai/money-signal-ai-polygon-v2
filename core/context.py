# core/context.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import MarketContext
from .polygon_client import PolygonClient

log = logging.getLogger(__name__)


def _get_spy_minute_agg(client: PolygonClient) -> Optional[dict]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=30)

    path = f"/v2/aggs/ticker/SPY/range/1/minute/{start.date()}/{now.date()}"
    params = {"limit": 50, "sort": "desc"}
    try:
        data = client.get(path, params)
        if data.get("resultsCount", 0) == 0:
            return None
        return data
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to fetch SPY aggs for context: %s", exc)
        return None


def compute_market_context(client: PolygonClient) -> MarketContext:
    data = _get_spy_minute_agg(client)
    if not data:
        return MarketContext(trend="chop", vol_regime="normal", risk_off=False)

    results = data.get("results", [])
    if not results:
        return MarketContext(trend="chop", vol_regime="normal", risk_off=False)

    tail = results[:5]
    closes = [bar.get("c") for bar in tail if "c" in bar]
    highs = [bar.get("h") for bar in tail if "h" in bar]
    lows = [bar.get("l") for bar in tail if "l" in bar]

    if len(closes) < 2:
        return MarketContext(trend="chop", vol_regime="normal", risk_off=False)

    if closes[0] > closes[-1] * 1.001:
        trend = "bull"
    elif closes[0] < closes[-1] * 0.999:
        trend = "bear"
    else:
        trend = "chop"

    avg_range = (sum(h - l for h, l in zip(highs, lows)) / len(highs)) if highs and lows else 0.0

    if avg_range < 0.3:
        vol = "low"
    elif avg_range > 1.0:
        vol = "high"
    else:
        vol = "normal"

    risk_off = trend == "bear" and vol == "high"

    return MarketContext(trend=trend, vol_regime=vol, risk_off=bool(risk_off))
