# bots/options_unusual.py
from __future__ import annotations

import logging
from typing import Iterable, List

from core.models import Signal, MarketContext
from core.bus import SignalBus
from core.polygon_client import PolygonClient
from core.options_utils import parse_polygon_option_ticker, days_to_expiry, format_option_label

log = logging.getLogger(__name__)


def _scan_underlying_unusual(
    client: PolygonClient,
    underlying: str,
    *,
    min_notional: float,
    min_size: int,
    max_dte: int,
) -> Iterable[Signal]:
    path = "/v3/snapshot/options"
    params = {
        "underlying_ticker": underlying,
        "limit": 200,
        "sort": "day.volume",
    }

    try:
        data = client.get(path, params)
    except Exception as exc:  # noqa: BLE001
        log.warning("UNUSUAL: failed to fetch options for %s: %s", underlying, exc)
        return []

    results = data.get("results") or []
    signals: List[Signal] = []

    for row in results:
        day = row.get("day") or {}
        last_quote = row.get("last_quote") or {}
        underlying_info = row.get("underlying_asset") or {}

        volume = int(day.get("volume") or 0)
        price = float(day.get("close") or last_quote.get("bid") or 0.0)
        contract = row.get("ticker") or ""

        if volume <= 0 or price <= 0:
            continue

        size = volume  # snapshot aggregate volume
        notional = price * size * 100.0

        if size < min_size:
            continue
        if notional < min_notional:
            continue

        parsed = parse_polygon_option_ticker(contract)
        dte = days_to_expiry(parsed.expiry)
        if dte is None or dte < 0 or dte > max_dte:
            continue

        cp = parsed.cp
        direction = "bull" if cp == "C" else "bear" if cp == "P" else "neutral"

        underlying_price = float(underlying_info.get("price") or 0.0) if underlying_info else None
        label = format_option_label(parsed)

        base_conv = 0.65
        if notional >= min_notional * 2:
            base_conv += 0.1
        if size >= min_size * 3:
            base_conv += 0.05

        base_conv = max(0.0, min(1.0, base_conv))

        reasons = [
            "unusual_sized_flow",
            f"notional≈{notional:,.0f}",
            f"dte={dte}",
            f"underlying={underlying}",
        ]

        sig = Signal(
            bot="unusual_sweeps",
            symbol=label,
            direction=direction,  # type: ignore[arg-type]
            conviction=base_conv,
            reasons=reasons,
            timeframe="intraday",
            risk_tag="aggressive",
            price=price,
            extra={
                "contract_ticker": contract,
                "underlying": underlying,
                "volume": volume,
                "size": size,
                "notional": notional,
                "dte": dte,
                "cp": cp,
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
    min_size: int,
    max_dte: int,
) -> None:
    for underlying in universe:
        for sig in _scan_underlying_unusual(
            client,
            underlying,
            min_notional=min_notional,
            min_size=min_size,
            max_dte=max_dte,
        ):
            if ctx.risk_off and sig.direction == "bull":
                sig.conviction *= 0.9
            bus.publish(sig)
