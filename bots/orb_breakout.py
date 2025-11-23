# bots/orb_breakout.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Tuple

from core.models import Signal, MarketContext
from core.bus import SignalBus
from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)


RTH_OPEN_HOUR = 9
RTH_OPEN_MINUTE = 30
ORB_MINUTES = 15


def _fetch_minute_aggs_today(client: PolygonClient, symbol: str) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=12)
    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/minute/{start.date()}/{now.date()}"
    params = {"sort": "asc", "limit": 5000}
    data = client.get(path, params)
    return data.get("results") or []


def _orb_range(bars: List[dict]) -> Tuple[float, float] | None:
    if not bars:
        return None

    highs = []
    lows = []

    for bar in bars:
        ts_ms = int(bar.get("t") or 0)
        dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
        est_dt = dt - timedelta(hours=5)  # crude UTC->ET

        if est_dt.hour < RTH_OPEN_HOUR or (est_dt.hour == RTH_OPEN_HOUR and est_dt.minute < RTH_OPEN_MINUTE):
            continue

        minutes_since_open = (est_dt.hour * 60 + est_dt.minute) - (RTH_OPEN_HOUR * 60 + RTH_OPEN_MINUTE)
        if 0 <= minutes_since_open < ORB_MINUTES:
            highs.append(float(bar.get("h") or 0.0))
            lows.append(float(bar.get("l") or 0.0))

    if not highs or not lows:
        return None

    return max(highs), min(lows)


def run(
    client: PolygonClient,
    bus: SignalBus,
    ctx: MarketContext,
    *,
    universe: Iterable[str],
    min_r_break: float = 0.002,
) -> None:
    for symbol in universe:
        try:
            bars = _fetch_minute_aggs_today(client, symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("orb_breakout: failed to fetch bars for %s: %s", symbol, exc)
            continue

        if not bars:
            continue

        orb = _orb_range(bars)
        if not orb:
            continue

        orb_high, orb_low = orb
        last_bar = bars[-1]
        close = float(last_bar.get("c") or 0.0)

        if close <= 0:
            continue

        direction = "neutral"
        breakout_amt = 0.0

        if close > orb_high * (1 + min_r_break):
            direction = "bull"
            breakout_amt = (close - orb_high) / orb_high
        elif close < orb_low * (1 - min_r_break):
            direction = "bear"
            breakout_amt = (orb_low - close) / orb_low

        if direction == "neutral":
            continue

        conv = 0.6 + breakout_amt * 5.0
        if ctx.trend == "bull" and direction == "bull":
            conv += 0.1
        if ctx.trend == "bear" and direction == "bear":
            conv += 0.1
        conv = max(0.0, min(1.0, conv))

        sig = Signal(
            bot="orb_breakout",
            symbol=symbol,
            direction=direction,  # type: ignore[arg-type]
            conviction=conv,
            reasons=[
                f"orb_break_{direction}",
                f"orb_high={orb_high:.2f}",
                f"orb_low={orb_low:.2f}",
            ],
            timeframe="intraday",
            risk_tag="aggressive",
            price=close,
            extra={
                "orb_high": orb_high,
                "orb_low": orb_low,
                "breakout_amt": breakout_amt,
            },
        )
        bus.publish(sig)
