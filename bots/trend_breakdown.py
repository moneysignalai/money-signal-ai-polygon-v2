# bots/trend_breakdown.py

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import List

from core.models import Signal
from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)


def _fetch_daily_window(
    client: PolygonClient,
    symbol: str,
    days_back: int,
) -> List[dict]:
    """
    Fetch a window of daily bars for the symbol.

    We over‑fetch in calendar days to safely cover `days_back` trading sessions.
    """
    today = date.today()
    from_date = (today - timedelta(days=days_back * 3)).isoformat()
    to_date = today.isoformat()

    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{from_date}/{to_date}"
    params = {
        "adjusted": "true",
        "sort": "asc",
        "limit": 500,
    }

    data = client.get(path, params)
    bars = data.get("results") or []
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
    Trend breakdown bot (mirror of trend breakout, but to the downside).

    Rough logic per symbol:
      - Get last `breakdown_lookback + 1` daily bars
      - Today's close must be:
          * within [min_price, max_price]
          * below the prior N‑day low (fresh breakdown)
          * with dollar volume >= min_dollar_vol
          * with relative volume >= min_rvol_breakdown
      - Emit a bearish TREND_BREAKDOWN signal.
    """
    today = date.today()

    for symbol in universe:
        try:
            bars = _fetch_daily_window(client, symbol, breakdown_lookback + 1)
        except Exception as exc:  # noqa: BLE001
            log.warning("trend_breakdown: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if len(bars) < breakdown_lookback + 1:
            log.debug("trend_breakdown: not enough bars for %s", symbol)
            continue

        # Use only the most recent window
        recent = bars[-(breakdown_lookback + 1) :]
        prior = recent[:-1]
        today_bar = recent[-1]

        close = float(today_bar.get("c") or 0.0)
        high = float(today_bar.get("h") or 0.0)
        low = float(today_bar.get("l") or 0.0)
        volume = float(today_bar.get("v") or 0.0)

        if close <= 0 or volume <= 0:
            continue

        if close < min_price or close > max_price:
            continue

        dollar_vol = close * volume
        if dollar_vol < min_dollar_vol:
            continue

        # Prior N‑day low
        prior_low = min(float(bar.get("l") or 0.0) for bar in prior if bar.get("l") is not None)
        if prior_low <= 0:
            continue

        # Require a breakdown: today's close below prior N‑day low
        if close >= prior_low:
            continue

        # Relative volume vs prior N sessions
        avg_vol = sum(float(bar.get("v") or 0.0) for bar in prior) / max(len(prior), 1)
        rvol = volume / avg_vol if avg_vol > 0 else 0.0
        if rvol < min_rvol_breakdown:
            continue

        # Strength of breakdown: deeper close + higher rvol = more conviction
        depth = max(0.0, (prior_low - close) / prior_low)  # % below prior low
        raw_conv = 0.5 + depth * 1.5 + (rvol - min_rvol_breakdown) * 0.1
        conviction = max(0.6, min(raw_conv, 0.99))

        reasons = [
            "trend_breakdown",
            f"close={close:.2f}",
            f"prior_{breakdown_lookback}d_low={prior_low:.2f}",
            f"rvol≈{rvol:.1f}x",
            f"dollar_vol≈${int(dollar_vol):,}",
        ]

        extra = {
            "underlying": symbol,
            "kind": "trend_breakdown_v2",
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
            "dollar_vol": dollar_vol,
            "prior_low": prior_low,
            "rvol": rvol,
            "as_of": today.isoformat(),
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
            "trend_breakdown: %s breakdown close=%.2f < prior_low=%.2f rvol=%.1f dollar_vol≈$%s",
            symbol,
            close,
            prior_low,
            rvol,
            int(dollar_vol),
        )
