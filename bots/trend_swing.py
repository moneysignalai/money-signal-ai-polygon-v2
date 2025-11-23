# bots/trend_swing.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Tuple

from core.models import Signal, MarketContext
from core.bus import SignalBus
from core.polygon_client import PolygonClient

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


def _compute_rvol(bars: List[dict], lookback: int = 20) -> Tuple[float, float, float]:
    if len(bars) < lookback + 1:
        return 0.0, 0.0, 0.0

    today = bars[-1]
    hist = bars[-lookback - 1 : -1]
    today_vol = float(today.get("v") or 0.0)
    avg_vol = sum(float(b.get("v") or 0.0) for b in hist) / len(hist) if hist else 0.0
    if avg_vol <= 0:
        return today_vol, avg_vol, 0.0
    return today_vol, avg_vol, today_vol / avg_vol


def run(
    client: PolygonClient,
    bus: SignalBus,
    ctx: MarketContext,
    *,
    universe: Iterable[str],
    min_price: float,
    max_price: float,
    min_dollar_vol: float,
    breakout_lookback: int,
    min_rvol_trend: float,
    min_rvol_pullback: float,
) -> None:
    for symbol in universe:
        try:
            bars = _fetch_daily_aggs(client, symbol, days=60)
        except Exception as exc:  # noqa: BLE001
            log.warning("trend_swing: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if len(bars) < breakout_lookback + 25:
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

        _, _, rvol = _compute_rvol(bars, lookback=20)
        if rvol <= 0:
            continue

        sma20_series = _sma(closes, 20)
        sma50_series = _sma(closes, 50)
        if not sma20_series or not sma50_series:
            continue

        sma20 = sma20_series[-1]
        sma50 = sma50_series[-1]

        strong_uptrend = last_close > sma20 > sma50
        if not strong_uptrend:
            continue

        # Trend breakout
        breakout_signals: List[Signal] = []
        lookback_slice = closes[-(breakout_lookback + 1) : -1]
        prior_high = max(lookback_slice) if lookback_slice else 0.0

        breakout_up = prior_high > 0 and last_close > prior_high * 1.01
        if breakout_up and rvol >= min_rvol_trend:
            conv = 0.65 + (rvol - min_rvol_trend) * 0.1
            if ctx.trend == "bull":
                conv += 0.1
            conv = max(0.0, min(1.0, conv))

            sig = Signal(
                bot="trend_breakout",
                symbol=symbol,
                direction="bull",  # type: ignore[arg-type]
                conviction=conv,
                reasons=[
                    "strong_uptrend",
                    f"breakout_vs_{breakout_lookback}d_high",
                    f"rvol≈{rvol:.1f}",
                ],
                timeframe="swing",
                risk_tag="normal",
                price=last_close,
                extra={
                    "sma20": sma20,
                    "sma50": sma50,
                    "prior_high": prior_high,
                    "rvol": rvol,
                    "dollar_vol": dollar_vol,
                },
            )
            breakout_signals.append(sig)

        # Swing pullback
        pullback_signals: List[Signal] = []
        near_sma20 = sma20 > 0 and (last_close >= sma20 * 0.97) and (last_close <= sma20 * 1.02)
        above_sma50 = last_close > sma50
        recent_high = max(closes[-30:])
        not_near_recent_low = last_close > min(closes[-30:]) * 1.05

        if near_sma20 and above_sma50 and not_near_recent_low and rvol >= min_rvol_pullback:
            conv = 0.6 + (rvol - min_rvol_pullback) * 0.08
            if ctx.trend == "bull":
                conv += 0.05
            conv = max(0.0, min(1.0, conv))

            sig = Signal(
                bot="swing_pullback",
                symbol=symbol,
                direction="bull",  # type: ignore[arg-type]
                conviction=conv,
                reasons=[
                    "pullback_to_sma20",
                    "above_sma50",
                    f"rvol≈{rvol:.1f}",
                ],
                timeframe="swing",
                risk_tag="normal",
                price=last_close,
                extra={
                    "sma20": sma20,
                    "sma50": sma50,
                    "recent_high": recent_high,
                    "rvol": rvol,
                    "dollar_vol": dollar_vol,
                },
            )
            pullback_signals.append(sig)

        for sig in breakout_signals + pullback_signals:
            bus.publish(sig)
