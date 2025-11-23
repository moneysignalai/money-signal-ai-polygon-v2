# bots/dark_pool_radar.py
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

from core.models import Signal, MarketContext
from core.bus import SignalBus
from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)


def _fetch_recent_trades(
    client: PolygonClient,
    symbol: str,
    lookback_minutes: int = 10,
) -> List[dict]:
    now = datetime.now(timezone.utc)
    start = now - timedelta(minutes=lookback_minutes)

    path = f"/v3/trades/{symbol.upper()}"
    params = {
        "limit": 500,
        "sort": "desc",
        # add timestamp filters if desired, based on your Polygon plan
        # "timestamp.gte": int(start.timestamp() * 1_000_000_000),
    }

    data = client.get(path, params)
    return data.get("results") or []


def _is_dark_pool_trade(trade: dict) -> bool:
    conds = trade.get("conditions") or []
    # Placeholder — adjust to your real dark-pool condition/venue filters
    return 12 in conds or 37 in conds


def run(
    client: PolygonClient,
    bus: SignalBus,
    ctx: MarketContext,
    *,
    universe: Iterable[str],
    min_notional: float,
    lookback_minutes: int = 10,
) -> None:
    for symbol in universe:
        try:
            trades = _fetch_recent_trades(client, symbol, lookback_minutes=lookback_minutes)
        except Exception as exc:  # noqa: BLE001
            log.warning("dark_pool_radar: failed to fetch trades for %s: %s", symbol, exc)
            continue

        if not trades:
            continue

        total_notional = 0.0
        last_price = 0.0

        for tr in trades:
            if not _is_dark_pool_trade(tr):
                continue
            price = float(tr.get("p") or 0.0)
            size = float(tr.get("s") or 0.0)
            if price <= 0 or size <= 0:
                continue
            total_notional += price * size
            if last_price == 0.0:
                last_price = price

        if total_notional < min_notional or last_price <= 0:
            continue

        direction = "neutral"
        conv = 0.6
        if ctx.trend == "bull":
            direction = "bull"
            conv += 0.1
        elif ctx.trend == "bear":
            direction = "bear"
            conv += 0.1

        conv = max(0.0, min(1.0, conv))

        sig = Signal(
            bot="dark_pool_radar",
            symbol=symbol,
            direction=direction,  # type: ignore[arg-type]
            conviction=conv,
            reasons=[
                "dark_pool_notional",
                f"notional≈{total_notional:,.0f}",
            ],
            timeframe="intraday",
            risk_tag="aggressive",
            price=last_price,
            extra={
                "total_notional": total_notional,
                "lookback_minutes": lookback_minutes,
            },
        )
        bus.publish(sig)
