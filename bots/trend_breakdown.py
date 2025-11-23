# bots/trend_breakdown.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

from core.models import Signal, MarketContext
from core.bus import SignalBus
from core.polygon_client import PolygonClient
from bots.volume_monster import compute_rvol  # reuse RVOL logic

log = logging.getLogger(__name__)


def _fetch_daily_aggs(
    client: PolygonClient,
    symbol: str,
    days: int = 60,
) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days + 5)
    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start.date()}/{now.date()}"
    params = {"sort": "asc", "limit": 150}
    data = client.get(path, params)
    return data.get("results") or []


def _sma(values: List[float], window: int) -> List[float]:
    if len(values) < window:
        return []
    out: List[float] = []
    for i in range(len(values)):
        if i + 1 < window:
            continue
        window_vals = values[i - window + 1 : i + 1]
        out.append(sum(window_vals) / float(window))
    return out


def run(
    client: PolygonClient,
    bus: SignalBus,
    ctx: MarketContext,
    *,
    universe: Iterable[str],
    min_price: float,
    max_price: float,
    min_dollar_vol: float,
    breakdown_lookback: int,
    min_rvol_breakdown: float,
) -> None:
    """
    Trend breakdown (bearish mirror of breakout):
      • Strong downtrend: price < SMA20 < SMA50
      • Close breaks below recent N-day low
      • Elevated RVOL
    """
    for symbol in universe:
        try:
            bars = _fetch_daily_aggs(client, symbol, days=60)
        except Exception as exc:  # noqa: BLE001
            log.warning("trend_breakdown: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if len(bars) < breakdown_lookback + 25:
            continue

        closes = [float(b.get("c") or 0.0) for b in bars]
        vols = [float(b.get("v") or 0.0) for b in bars]
        today = bars[-1]
        last_close = closes[-1]
        day_vol = vols[-1]

        if last_close < min_price or last_close > max_price:
            continue

        dollar_vol = last_close * day_vol
        if dollar_vol < min_dollar_vol:
            continue

        _, _, rvol = compute_rvol(bars)
        if rvol <= 0:
            continue

        sma20_series = _sma(closes, 20)
        sma50_series = _sma(closes, 50)
        if not sma20_series or not sma50_series:
            continue

        sma20 = sma20_series[-1]
        sma50 = sma50_series[-1]

        strong_downtrend = last_close < sma20 < sma50
        if not strong_downtrend:
            continue

        prior_slice = closes[-(breakdown_lookback + 1) : -1]
        if not prior_slice:
            continue
        prior_low = min(prior_slice)

        breakdown = prior_low > 0 and last_close < prior_low * 0.99
        if not breakdown:
            continue

        if rvol < min_rvol_breakdown:
            continue

        conv = 0.65 + (rvol - min_rvol_breakdown) * 0.1
        if ctx.trend == "bear":
            conv += 0.1
        conv = max(0.0, min(1.0, conv))

        sig = Signal(
            bot="trend_breakdown",
            symbol=symbol,
            direction="bear",  # type: ignore[arg-type]
            conviction=conv,
            reasons=[
                "strong_downtrend",
                f"breakdown_vs_{breakdown_lookback}d_low",
                f"rvol≈{rvol:.1f}",
            ],
            timeframe="swing",
            risk_tag="normal",
            price=last_close,
            extra={
                "sma20": sma20,
                "sma50": sma50,
                "prior_low": prior_low,
                "rvol": rvol,
                "dollar_vol": dollar_vol,
            },
        )
        bus.publish(sig)
