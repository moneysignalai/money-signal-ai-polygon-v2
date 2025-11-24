# bots/trend_breakdown.py

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List

from core.models import Signal
from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)


def _fetch_daily_aggs(
    client: PolygonClient,
    symbol: str,
    lookback: int,
) -> list[dict]:
    """
    Fetch recent daily candles for a symbol.

    We pull ~3x the lookback window to make sure we have enough trading days,
    then truncate to the most recent `lookback + 1` bars.
    """
    today = date.today()
    start = (today - timedelta(days=lookback * 3)).isoformat()
    end = today.isoformat()

    path = f"/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": lookback * 3,
    }

    data = client.get(path, params)
    results: List[dict] = data.get("results") or []
    if not results:
        return []

    # keep only the last (lookback + 1) bars (yesterday + today)
    return results[-(lookback + 1) :]


def run(
    client: PolygonClient,
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
    Bearish trend breakdown bot.

    Logic per symbol:
      - Get recent daily bars.
      - Last close in [min_price, max_price].
      - Dollar volume (last_close * last_volume) >= min_dollar_vol.
      - RVOL (last_volume / avg_volume_prior) >= min_rvol_breakdown.
      - Last close breaks below the recent support (min close of prior
        `breakdown_lookback` bars), with a small buffer.
    """

    if breakdown_lookback < 3:
        log.warning("trend_breakdown: breakdown_lookback too small (%s), skipping", breakdown_lookback)
        return

    for symbol in universe:
        try:
            bars = _fetch_daily_aggs(client, symbol, breakdown_lookback)
        except Exception as exc:  # noqa: BLE001
            log.warning("trend_breakdown: failed to fetch aggs for %s: %s", symbol, exc)
            continue

        # Need at least lookback + 1 bars (prior window + today)
        if len(bars) < breakdown_lookback + 1:
            continue

        closes = [float(b.get("c") or 0.0) for b in bars]
        vols = [float(b.get("v") or 0.0) for b in bars]

        last_close = closes[-1]
        last_vol = vols[-1]

        if last_close <= 0 or last_vol <= 0:
            continue

        # Price filter
        if not (min_price <= last_close <= max_price):
            continue

        dollar_vol = last_close * last_vol
        if dollar_vol < min_dollar_vol:
            continue

        prior_vols = vols[:-1]
        avg_prior_vol = sum(prior_vols) / max(len(prior_vols), 1)
        if avg_prior_vol <= 0:
            continue

        rvol = last_vol / avg_prior_vol
        if rvol < min_rvol_breakdown:
            continue

        # Breakdown condition: today closes below the lowest close of the
        # prior `breakdown_lookback` bars by a small margin.
        prior_closes_window = closes[:-1][-breakdown_lookback:]
        prior_low = min(prior_closes_window)

        # Small buffer (e.g. 0.5% below prior low) so it's a clear breakdown
        if last_close > prior_low * 0.995:
            continue

        breakdown_pct = (prior_low - last_close) / prior_low * 100.0

        # Conviction: scale off RVOL and breakdown magnitude
        # Clamp between 0.6 and 0.99
        raw_conv = 0.5 + (rvol / min_rvol_breakdown) * 0.2 + (breakdown_pct / 5.0) * 0.2
        conviction = max(0.6, min(raw_conv, 0.99))

        reasons = [
            "trend_breakdown",
            f"price≈{last_close:.2f}",
            f"rvol≈{rvol:.1f}",
            f"breakdown≈{breakdown_pct:.1f}%",
        ]

        extra = {
            "kind": "trend_breakdown_v2",
            "close": last_close,
            "rvol": rvol,
            "breakdown_pct": breakdown_pct,
            "dollar_vol": dollar_vol,
        }

        # First positional arg is the signal type
        sig = Signal(
            "TREND_BREAKDOWN",
            symbol=symbol,
            direction="bear",
            conviction=round(conviction, 2),
            reasons=reasons,
            extra=extra,
        )
        bus.publish(sig)

        log.info(
            "trend_breakdown: %s breakdown %.1f%% (rvol=%.1f, close=%.2f)",
            symbol,
            breakdown_pct,
            rvol,
            last_close,
        )