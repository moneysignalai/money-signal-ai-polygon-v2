# bots/volume_monster.py
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
    days: int = 21,
) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days + 2)

    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start.date()}/{now.date()}"
    params = {"sort": "asc", "limit": 120}

    data = client.get(path, params)
    return data.get("results") or []


def compute_rvol(bars: List[dict]) -> Tuple[float, float, float]:
    if not bars:
        return 0.0, 0.0, 0.0

    today_bar = bars[-1]
    today_vol = float(today_bar.get("v") or 0.0)

    hist = bars[:-1]
    if not hist:
        return today_vol, 0.0, 0.0

    avg_vol = sum(float(b.get("v") or 0.0) for b in hist) / len(hist)
    if avg_vol <= 0:
        return today_vol, avg_vol, 0.0

    rvol = today_vol / avg_vol
    return today_vol, avg_vol, rvol


def _direction_from_day_bar(bar: dict) -> str:
    o = float(bar.get("o") or 0.0)
    c = float(bar.get("c") or 0.0)
    if c > o * 1.002:
        return "bull"
    if c < o * 0.998:
        return "bear"
    return "neutral"


def run(
    client: PolygonClient,
    bus: SignalBus,
    ctx: MarketContext,
    *,
    universe: Iterable[str],
    min_rvol: float,
    min_dollar_vol: float,
) -> None:
    for symbol in universe:
        try:
            bars = _fetch_daily_aggs(client, symbol, days=21)
        except Exception as exc:  # noqa: BLE001
            log.warning("volume_monster: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if len(bars) < 3:
            continue

        today_vol, avg_vol, rvol = compute_rvol(bars)
        if rvol < min_rvol:
            continue

        last_bar = bars[-1]
        close = float(last_bar.get("c") or 0.0)
        dollar_vol = close * today_vol

        if dollar_vol < min_dollar_vol:
            continue

        direction = _direction_from_day_bar(last_bar)

        conv = min(1.0, 0.5 + (rvol - min_rvol) * 0.1)
        if ctx.trend == "bull" and direction == "bull":
            conv += 0.1
        if ctx.trend == "bear" and direction == "bear":
            conv += 0.1
        conv = max(0.0, min(1.0, conv))

        sig = Signal(
            bot="volume_monster",
            symbol=symbol,
            direction=direction,  # type: ignore[arg-type]
            conviction=conv,
            reasons=[
                f"rvol≈{rvol:.1f}",
                f"dollar_vol≈{dollar_vol:,.0f}",
            ],
            timeframe="intraday",
            risk_tag="normal",
            price=close,
            extra={
                "today_vol": today_vol,
                "avg_vol": avg_vol,
                "dollar_vol": dollar_vol,
            },
        )
        bus.publish(sig)
