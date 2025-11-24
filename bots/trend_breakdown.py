# bots/trend_breakdown.py

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List, Dict, Any

from core.models import Signal
from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)


def _fetch_daily_bars(
    client: PolygonClient,
    symbol: str,
    lookback_days: int,
) -> List[Dict[str, Any]]:
    """
    Fetch daily bars for a symbol using Polygon v2 aggs.

    We pull ~2x the requested lookback in calendar days so we have enough
    trading sessions even across weekends/holidays.
    """
    today = date.today()
    start = today - timedelta(days=lookback_days * 2)

    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start.isoformat()}/{today.isoformat()}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 500,
    }

    data = client.get(path, params)
    bars = data.get("results") or []

    if not bars:
        log.debug("trend_breakdown: no bars for %s", symbol)

    return bars


def run(
    client,
    bus,
    ctx,
    *,
    universe: tuple[str, ...],
    min_price: float,
    max_price: float,
    min_dollar_vol: float,
    breakdown_lookback: int,
    min_rvol_breakdown: float,
) -> None:
    """
    Trend breakdown bot (bearish mirror of trend breakout).

    Logic per symbol:
      - Fetch daily bars.
      - Require at least `breakdown_lookback + 1` bars.
      - Last close within [min_price, max_price].
      - Last dollar volume >= min_dollar_vol.
      - Compute:
          * recent window = last `breakdown_lookback` bars (excluding today)
          * support = min(low in recent window)
          * avg_vol = mean(volume in recent window)
          * rvol = last_volume / avg_vol
      - Trigger breakdown if:
          * last_close < support
          * rvol >= min_rvol_breakdown
    """
    for symbol in universe:
        try:
            bars = _fetch_daily_bars(
                client,
                symbol,
                breakdown_lookback + 5,  # pad a bit
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("trend_breakdown: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if len(bars) < breakdown_lookback + 1:
            log.debug(
                "trend_breakdown: not enough bars for %s (have=%d, need=%d)",
                symbol,
                len(bars),
                breakdown_lookback + 1,
            )
            continue

        # We assume bars are sorted ascending by time because we requested sort=asc.
        last_bar = bars[-1]
        window = bars[-(breakdown_lookback + 1):-1]  # previous N bars, exclude today

        # Safely extract numeric fields
        try:
            last_close = float(last_bar.get("c", 0.0))
            last_vol = float(last_bar.get("v", 0.0))
        except (TypeError, ValueError):  # weird data
            continue

        if last_close <= 0 or last_vol <= 0:
            continue

        # Price and dollar volume filters
        if last_close < min_price or last_close > max_price:
            continue

        last_dollar_vol = last_close * last_vol
        if last_dollar_vol < min_dollar_vol:
            continue

        # Build lists for lows and volumes in the lookback window
        lows: List[float] = []
        vols: List[float] = []

        for bar in window:
            try:
                low_val = float(bar.get("l", 0.0))
                vol_val = float(bar.get("v", 0.0))
            except (TypeError, ValueError):
                continue
            if low_val <= 0 or vol_val <= 0:
                continue
            lows.append(low_val)
            vols.append(vol_val)

        if not lows or not vols:
            # Failed to build a proper window; skip
            continue

        support = min(lows)
        avg_vol = sum(vols) / float(len(vols))
        if avg_vol <= 0:
            continue

        rvol = last_vol / avg_vol

        # Breakdown conditions: close below recent support, elevated volume
        if last_close >= support:
            # Not actually breaking down yet
            continue

        if rvol < min_rvol_breakdown:
            continue

        # Direction is bearish; conviction scales with rvol and distance below support
        breakdown_pct = max(0.0, (support - last_close) / support) if support > 0 else 0.0

        base_conv = 0.6
        rvol_term = min(rvol / (min_rvol_breakdown * 2.0), 2.0)  # cap influence
        depth_term = min(breakdown_pct * 10.0, 2.0)  # 0‑20% below support → 0‑2

        raw_conv = base_conv + 0.1 * rvol_term + 0.1 * depth_term
        conviction = max(0.6, min(raw_conv, 0.99))

        reasons = [
            "trend_breakdown",
            f"close≈{last_close:.2f}",
            f"support≈{support:.2f}",
            f"rvol≈{rvol:.2f}",
            f"dollar_vol≈${int(last_dollar_vol):,}",
        ]

        extra = {
            "kind": "trend_breakdown_v2",
            "underlying": symbol,
            "last_close": last_close,
            "support": support,
            "rvol": rvol,
            "dollar_vol": last_dollar_vol,
            "lookback": breakdown_lookback,
            "market_trend": getattr(ctx, "trend", None),
            "market_vol_regime": getattr(ctx, "vol_regime", None),
            "market_risk_off": getattr(ctx, "risk_off", None),
        }

        sig = Signal(
            kind="TREND_BREAKDOWN",
            symbol=symbol,
            direction="bear",
            conviction=round(conviction, 2),
            reasons=reasons,
            extra=extra,
        )
        bus.publish(sig)

        log.info(
            "trend_breakdown: %s breakdown, close=%.2f support=%.2f rvol=%.2f dollar_vol=%.0f",
            symbol,
            last_close,
            support,
            rvol,
            last_dollar_vol,
        )

    log.info("trend_breakdown: finished universe of %d symbols", len(universe))