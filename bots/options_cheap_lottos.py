# bots/options_cheap_lottos.py
from __future__ import annotations

import logging
from typing import Iterable, List

from core.models import Signal, MarketContext
from core.bus import SignalBus
from core.polygon_client import PolygonClient
from core.options_utils import parse_polygon_option_ticker, format_option_label

log = logging.getLogger(__name__)


def _scan_underlying(
    client: PolygonClient,
    symbol: str,
    min_notional: float,
    max_premium: float,
    min_volume: int,
) -> Iterable[Signal]:
    """
    Very simple "cheap lotto" logic:
      - Pull option snapshot for an underlying
      - Look for cheap contracts with meaningful volume / notional
    """
    path = "/v3/snapshot/options"
    params = {
        "underlying_ticker": symbol,
        "limit": 50,
        "sort": "day.volume",
    }

    try:
        data = client.get(path, params)
    except Exception as exc:  # noqa: BLE001
        log.warning("Cheap lotto: failed to fetch options for %s: %s", symbol, exc)
        return []

    results = data.get("results") or []
    signals: List[Signal] = []

    for row in results:
        day = row.get("day") or {}
        last_quote = row.get("last_quote") or {}
        underlying = row.get("underlying_asset") or {}

        volume = int(day.get("volume") or 0)
        last_price = float(day.get("close") or last_quote.get("bid") or 0.0)

        if volume < min_volume:
            continue
        if last_price <= 0 or last_price > max_premium:
            continue

        notional = last_price * volume * 100.0
        if notional < min_notional:
            continue

        option_symbol = row.get("ticker") or "UNKNOWN"
        parsed = parse_polygon_option_ticker(option_symbol)
        cp = parsed.cp
        direction = "bull" if cp == "C" else "bear" if cp == "P" else "neutral"

        underlying_price = float(underlying.get("price") or 0.0) if underlying else None
        label = format_option_label(parsed)

        base_conv = 0.55
        if notional >= min_notional * 3:
            base_conv += 0.15
        if volume >= min_volume * 3:
            base_conv += 0.1

        base_conv = max(0.0, min(1.0, base_conv))

        sig = Signal(
            bot="cheap_lottos",
            symbol=label,  # pretty label
            direction=direction,  # type: ignore[arg-type]
            conviction=base_conv,
            reasons=[
                "cheap_premium",
                "elevated_volume",
                f"underlying={symbol}",
            ],
            timeframe="intraday",
            risk_tag="lotto",
            price=last_price,
            extra={
                "contract_ticker": option_symbol,
                "underlying": symbol,
                "volume": volume,
                "notional": notional,
                "underlying_price": underlying_price,
            },
        )
        signals.append(sig)

    return signals


def run(
    client: PolygonClient,
    bus: SignalBus,
    ctx: MarketContext,
    *,
    universe: Iterable[str],
    min_notional: float,
    max_premium: float,
    min_volume: int,
) -> None:
    for symbol in universe:
        for sig in _scan_underlying(
            client,
            symbol,
            min_notional=min_notional,
            max_premium=max_premium,
            min_volume=min_volume,
        ):
            if ctx.risk_off and sig.direction == "bull":
                sig.conviction *= 0.9
            bus.publish(sig)
