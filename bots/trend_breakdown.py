# bots/trend_breakdown.py
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, timedelta
from typing import List, Dict, Any

from core.models import Signal
from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)


def _fetch_daily_bars(
    client: PolygonClient,
    symbol: str,
    lookback: int,
) -> List[Dict[str, Any]]:
    """
    Fetch a window of daily bars and return them sorted by time ascending.
    We over-fetch a bit and then trim to the last (lookback + 1) bars.
    """
    today = date.today()
    # Over-fetch to account for weekends/holidays
    start = today - timedelta(days=lookback * 3)

    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start.isoformat()}/{today.isoformat()}"
    params = {"limit": 500, "sort": "asc"}

    data = client.get(path, params)
    bars = data.get("results") or []
    if not bars:
        return []

    # Keep only the last (lookback + 1) bars so we have:
    #  - prior window: previous `lookback` bars
    #  - current bar: last bar
    needed = lookback + 1
    if len(bars) > needed:
        bars = bars[-needed:]
    return bars


def _compute_rvol(bars: List[Dict[str, Any]]) -> float:
    """
    Compute relative volume = today's volume / average of prior bars.
    bars must be sorted ascending, last bar = "today".
    """
    if len(bars) < 2:
        return 1.0

    vols = [float(b.get("v") or 0.0) for b in bars]
    today_vol = vols[-1]
    prior = vols[:-1]

    avg_prior = sum(prior) / len(prior) if prior else 0.0
    if avg_prior <= 0:
        return 1.0

    return today_vol / avg_prior


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
    Bearish trend breakdown bot (mirror of trend breakout).

    Logic (per symbol):
      - Get last `breakdown_lookback + 1` daily bars.
      - Last close must be within [min_price, max_price].
      - Last dollar volume (close * volume) >= min_dollar_vol.
      - Last close breaks the prior N‑day support:
          close_today < min(prior_closes)
      - Relative volume >= min_rvol_breakdown.
      - Direction = BEAR.
    Conviction is scaled by:
      - Strength of breakdown vs prior low
      - Relative volume
      - Market context (bearish/risk-off boosts)
    """
    if breakdown_lookback <= 1:
        log.warning("trend_breakdown: breakdown_lookback <= 1, nothing to do")
        return

    for symbol in universe:
        try:
            bars = _fetch_daily_bars(client, symbol, breakdown_lookback)
        except Exception as exc:  # noqa: BLE001
            log.warning("trend_breakdown: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if len(bars) < breakdown_lookback + 1:
            log.debug(
                "trend_breakdown: not enough bars for %s (have=%d need=%d)",
                symbol,
                len(bars),
                breakdown_lookback + 1,
            )
            continue

        # bars sorted asc; last = most recent
        last_bar = bars[-1]
        prior_bars = bars[:-1]

        close = float(last_bar.get("c") or 0.0)
        volume = float(last_bar.get("v") or 0.0)

        if close <= 0:
            continue

        # Price band filter
        if close < min_price or close > max_price:
            continue

        # Dollar volume filter
        dollar_vol = close * volume
        if dollar_vol < min_dollar_vol:
            continue

        # Compute support level from prior closes
        prior_closes = [float(b.get("c") or 0.0) for b in prior_bars if b.get("c") is not None]
        if len(prior_closes) < breakdown_lookback:
            continue

        support = min(prior_closes[-breakdown_lookback:])

        # Must clearly break that support
        if close >= support:
            continue

        # Relative volume threshold
        rvol = _compute_rvol(bars)
        if rvol < float(min_rvol_breakdown):
            continue

        # Breakdown magnitude (% below prior support)
        breakdown_pct = (support - close) / support * 100.0 if support > 0 else 0.0

        # --- Conviction scoring ---
        # Base
        conviction = 0.6

        # Boost from breakdown strength
        if breakdown_pct > 1.0:
            conviction += min(breakdown_pct / 10.0, 0.2)  # cap

        # Boost from relative volume
        if rvol > 1.0:
            conviction += min((rvol - 1.0) / 3.0, 0.15)

        # Market context adjustments
        # ctx.trend might be "bull", "bear", "sideways" etc.
        if getattr(ctx, "trend", "").lower().startswith("bear"):
            conviction += 0.1
        if getattr(ctx, "risk_off", False):
            conviction += 0.05
        if getattr(ctx, "vol_regime", "").lower() in ("high", "elevated"):
            conviction += 0.05

        conviction = max(0.6, min(conviction, 0.99))

        reasons = [
            "trend_breakdown",
            f"price≈{close:.2f}",
            f"support≈{support:.2f}",
            f"breakdown≈{breakdown_pct:.1f}%",
            f"rvol≈{rvol:.1f}",
            f"dollar_vol≈${int(dollar_vol):,}",
        ]

        extra = {
            "kind": "trend_breakdown_v2",
            "symbol": symbol,
            "close": close,
            "support": support,
            "breakdown_pct": breakdown_pct,
            "volume": volume,
            "dollar_vol": dollar_vol,
            "rvol": rvol,
            "lookback": breakdown_lookback,
            "market_ctx": asdict(ctx),
        }

        sig = Signal(
            kind="TREND_BREAKDOWN",
            symbol=symbol,
            direction="bear",
            conviction=round(conviction, 2),
            reasons=reasons,
            extra=extra,
        )
        log.info(
            "trend_breakdown: %s breakdown %.1f%% rvol=%.2f dollar_vol=%.0f",
            symbol,
            breakdown_pct,
            rvol,
            dollar_vol,
        )
        bus.publish(sig)
