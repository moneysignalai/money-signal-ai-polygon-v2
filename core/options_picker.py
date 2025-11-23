# core/option_picker.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .models import Signal
from .options_utils import parse_polygon_option_ticker, days_to_expiry, format_option_label
from .polygon_client import PolygonClient

log = logging.getLogger(__name__)


def _fetch_option_snapshots(
    client: PolygonClient,
    underlying: str,
    limit: int = 500,
) -> list[dict]:
    path = "/v3/snapshot/options"
    params = {
        "underlying_ticker": underlying.upper(),
        "limit": limit,
        "sort": "day.volume",
    }
    data = client.get(path, params)
    return data.get("results") or []


def _score_candidate(
    dte: int,
    target_dte: int,
    strike: float,
    underlying_price: float,
) -> float:
    """Lower score is better."""
    if underlying_price <= 0:
        return abs(dte - target_dte)

    dte_penalty = abs(dte - target_dte) / max(target_dte, 1)
    moneyness = abs(strike - underlying_price) / underlying_price
    return dte_penalty * 0.7 + moneyness * 0.3


def pick_simple_option_for_signal(
    signal: Signal,
    client: PolygonClient,
    *,
    target_dte: int = 30,
    min_dte: int = 7,
    max_dte: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    For a stock-level Signal (symbol like 'NVDA'), pick a simple
    directional option:
      - Bull signal => CALL
      - Bear signal => PUT
      - Nearest-to-ATM, DTE near target_dte
    """
    if " " in signal.symbol:
        return None
    if signal.direction not in ("bull", "bear"):
        return None

    underlying = signal.symbol.upper()
    desired_type = "C" if signal.direction == "bull" else "P"

    try:
        snaps = _fetch_option_snapshots(client, underlying, limit=500)
    except Exception as exc:  # noqa: BLE001
        log.warning("option_picker: failed to fetch options for %s: %s", underlying, exc)
        return None

    best_row = None
    best_score = None

    for row in snaps:
        ticker = row.get("ticker") or ""
        parsed = parse_polygon_option_ticker(ticker)
        cp = parsed.cp
        expiry = parsed.expiry
        strike = parsed.strike

        if cp != desired_type:
            continue
        if expiry is None or strike is None:
            continue

        dte = days_to_expiry(expiry)
        if dte is None or dte < min_dte or dte > max_dte:
            continue

        underlying_info = row.get("underlying_asset") or {}
        underlying_price = float(underlying_info.get("price") or 0.0)
        if underlying_price <= 0:
            continue

        score = _score_candidate(dte, target_dte, strike, underlying_price)
        if best_score is None or score < best_score:
            best_score = score
            best_row = row

    if not best_row:
        return None

    ticker = best_row.get("ticker")
    parsed = parse_polygon_option_ticker(ticker)
    dte = days_to_expiry(parsed.expiry) if parsed.expiry else None
    underlying_info = best_row.get("underlying_asset") or {}
    underlying_price = float(underlying_info.get("price") or 0.0)
    day = best_row.get("day") or {}
    last_price = float(day.get("close") or 0.0)

    display = format_option_label(parsed)

    return {
        "ticker": ticker,
        "display": display,
        "cp": parsed.cp,
        "strike": parsed.strike,
        "expiry": parsed.expiry.isoformat() if parsed.expiry else None,
        "dte": dte,
        "underlying_price": underlying_price,
        "last_price": last_price,
    }
