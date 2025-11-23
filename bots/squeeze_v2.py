# bots/squeeze_v2.py
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
    days: int = 30,
) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days + 5)
    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start.date()}/{now.date()}"
    params = {"sort": "asc", "limit": 80}
    data = client.get(path, params)
    return data.get("results") or []


def _compute_rvol(bars: List[dict], lookback: int = 20) -> Tuple[float, float, float]:
    if len(bars) < lookback + 1:
        return 0.0, 0.0, 0.0
    today = bars[-1]
    hist = bars[-lookback - 1 : -1]
    vol_today = float(today.get("v") or 0.0)
    avg_vol = sum(float(b.get("v") or 0.0) for b in hist) / len(hist) if hist else 0.0
    if avg_vol <= 0:
        return vol_today, avg_vol, 0.0
    return vol_today, avg_vol, vol_today / avg_vol


def run(
    client: PolygonClient,
    bus: SignalBus,
    ctx: MarketContext,
    *,
    universe: Iterable[str],
    min_price: float,
    max_price: float,
    move_min_pct: float,
    intraday_min_pct: float,
    min_rvol: float,
    min_dollar_vol: float,
    max_dist_from_high_pct: float,
) -> None:
    """
    Short-squeeze style bot:
      • Big % move vs yesterday close
      • Strong move vs today's open
      • High RVOL
      • Price near recent highs
    """
    for symbol in universe:
        try:
            bars = _fetch_daily_aggs(client, symbol, days=30)
        except Exception as exc:  # noqa: BLE001
            log.warning("squeeze_v2: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if len(bars) < 6:
            continue

        prev = bars[-2]
        today = bars[-1]

        prev_close = float(prev.get("c") or 0.0)
        open_today = float(today.get("o") or 0.0)
        last_price = float(today.get("c") or 0.0)
        day_vol = float(today.get("v") or 0.0)

        if prev_close <= 0 or open_today <= 0 or last_price <= 0:
            continue

        if last_price < min_price or last_price > max_price:
            continue

        move_pct = (last_price - prev_close) / prev_close * 100.0
        intraday_pct = (last_price - open_today) / open_today * 100.0

        if move_pct < move_min_pct:
            continue
        if intraday_pct < intraday_min_pct:
            continue

        vol_today, avg_vol, rvol = _compute_rvol(bars, lookback=20)
        if rvol < min_rvol:
            continue

        dollar_vol = last_price * day_vol
        if dollar_vol < min_dollar_vol:
            continue

        closes = [float(b.get("c") or 0.0) for b in bars[-25:]]
        recent_high = max(closes) if closes else last_price
        dist_from_high_pct = (recent_high - last_price) / recent_high * 100.0 if recent_high > 0 else 0.0

        if dist_from_high_pct > max_dist_from_high_pct:
            continue

        conv = 0.7
        conv += min(0.15, (move_pct - move_min_pct) * 0.01)
        conv += min(0.1, (rvol - min_rvol) * 0.05)
        if ctx.trend == "bull":
            conv += 0.05
        conv = max(0.0, min(1.0, conv))

        sig = Signal(
            bot="squeeze",
            symbol=symbol,
            direction="bull",  # type: ignore[arg-type]
            conviction=conv,
            reasons=[
                "big_gap_and_trend",
                f"move≈{move_pct:.1f}%",
                f"intraday≈{intraday_pct:.1f}%",
                f"rvol≈{rvol:.1f}",
            ],
            timeframe="intraday",
            risk_tag="aggressive",
            price=last_price,
            extra={
                "prev_close": prev_close,
                "open_today": open_today,
                "move_pct": move_pct,
                "intraday_pct": intraday_pct,
                "rvol": rvol,
                "dollar_vol": dollar_vol,
                "recent_high": recent_high,
                "dist_from_high_pct": dist_from_high_pct,
            },
        )
        bus.publish(sig)
