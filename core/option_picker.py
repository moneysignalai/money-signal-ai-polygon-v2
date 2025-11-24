# core/option_picker.py
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Dict, Any

from .models import Signal
from .polygon_client import PolygonClient

log = logging.getLogger(__name__)

# Keep HTTP load reasonable
_MAX_CONTRACTS_PER_UNDERLYING = 25


@dataclass
class _ScoredContract:
    ticker: str
    cp: str               # "C" or "P"
    strike: float
    expiry: date
    dte: int
    score: float          # lower = better
    underlying_price: float


def _fetch_contracts_for_underlying(client: PolygonClient, underlying: str) -> list[dict]:
    """
    Use Polygon v3 reference contracts to get a small, recent set of options
    for the given underlying.

    Docs: /v3/reference/options/contracts
    """
    path = "/v3/reference/options/contracts"
    params = {
        "underlying_ticker": underlying.upper(),
        "expired": "false",
        "order": "asc",
        "sort": "expiration_date",
        "limit": _MAX_CONTRACTS_PER_UNDERLYING,
    }
    data = client.get(path, params)
    results = data.get("results") or []
    if not results:
        log.info("option_picker: no contracts returned for %s", underlying)
    return results


def _fetch_underlying_price(client: PolygonClient, underlying: str) -> float:
    """
    Get a current-ish underlying price via stock snapshot.

    Docs: /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}
    """
    path = f"/v2/snapshot/locale/us/markets/stocks/tickers/{underlying.upper()}"
    data = client.get(path, {})
    ticker_data = data.get("ticker") or {}

    last_trade = ticker_data.get("lastTrade") or {}
    day = ticker_data.get("day") or {}

    price = float(
        last_trade.get("p")
        or day.get("c")
        or 0.0
    )
    return price


def _days_to_expiry(expiry: date) -> int:
    today = date.today()
    return (expiry - today).days


def _score_candidate(
    dte: int,
    target_dte: int,
    strike: float,
    underlying_price: float,
) -> float:
    """
    Lower score = better match.
    Combines DTE distance and moneyness.
    """
    if underlying_price <= 0:
        return abs(dte - target_dte)

    dte_penalty = abs(dte - target_dte) / max(target_dte, 1)
    moneyness = abs(strike - underlying_price) / underlying_price
    return dte_penalty * 0.7 + moneyness * 0.3


def _pick_best_contract(
    contracts: list[dict],
    underlying_price: float,
    *,
    desired_cp: str,
    target_dte: int,
    min_dte: int,
    max_dte: int,
) -> Optional[_ScoredContract]:
    """
    From a small list of contracts, pick the best one matching:
      - call/put (desired_cp)
      - DTE within [min_dte, max_dte]
      - nearest to target_dte and ATM-ish
    """
    best: Optional[_ScoredContract] = None

    for contract in contracts:
        ticker = contract.get("ticker")
        if not ticker:
            continue

        # contract_type: "call" / "put"
        ctype = (contract.get("contract_type") or contract.get("type") or "").lower()
        cp = "C" if ctype == "call" else "P" if ctype == "put" else None
        if cp is None or cp != desired_cp:
            continue

        strike = contract.get("strike_price")
        if strike is None:
            continue

        exp_str = contract.get("expiration_date")
        if not exp_str:
            continue

        try:
            expiry = datetime.fromisoformat(exp_str).date()
        except Exception:  # noqa: BLE001
            continue

        dte = _days_to_expiry(expiry)
        if dte < min_dte or dte > max_dte:
            continue

        score = _score_candidate(dte, target_dte, float(strike), underlying_price)
        candidate = _ScoredContract(
            ticker=ticker,
            cp=cp,
            strike=float(strike),
            expiry=expiry,
            dte=dte,
            score=score,
            underlying_price=underlying_price,
        )

        if best is None or score < best.score:
            best = candidate

    return best


def _fetch_last_price_for_option(client: PolygonClient, option_ticker: str) -> float:
    """
    Use the latest 1-min aggregate for the chosen option.

    Docs: /v2/aggs/ticker/{ticker}/range/1/min/{from}/{to}
    """
    today = date.today().isoformat()
    path = f"/v2/aggs/ticker/{option_ticker}/range/1/min/{today}/{today}"
    params = {
        "limit": 1,
        "sort": "desc",
    }

    data = client.get(path, params)
    results = data.get("results") or []
    if not results:
        return 0.0

    agg = results[0]
    return float(agg.get("c") or 0.0)


def pick_simple_option_for_signal(
    signal: Signal,
    client: PolygonClient,
    *,
    target_dte: int = 30,
    min_dte: int = 7,
    max_dte: int = 60,
) -> Optional[Dict[str, Any]]:
    """
    Pick a simple CALL or PUT for a given stock signal using:
      - /v3/reference/options/contracts
      - /v2/snapshot/... for underlying
      - /v2/aggs/... for chosen contract

    - For bullish signals → CALL
    - For bearish signals → PUT
    """
    # Only attach to "stocky" symbols (no spaces, not already option ticker etc.)
    if " " in signal.symbol:
        return None

    if signal.direction not in ("bull", "bear"):
        return None

    underlying = signal.symbol.upper()
    desired_cp = "C" if signal.direction == "bull" else "P"

    try:
        underlying_price = _fetch_underlying_price(client, underlying)
    except Exception as exc:  # noqa: BLE001
        log.warning("option_picker: failed to fetch underlying price for %s: %s", underlying, exc)
        return None

    if underlying_price <= 0:
        log.info("option_picker: no valid underlying price for %s", underlying)
        return None

    try:
        contracts = _fetch_contracts_for_underlying(client, underlying)
    except Exception as exc:  # noqa: BLE001
        log.warning("option_picker: failed to fetch contracts for %s: %s", underlying, exc)
        return None

    best = _pick_best_contract(
        contracts,
        underlying_price,
        desired_cp=desired_cp,
        target_dte=target_dte,
        min_dte=min_dte,
        max_dte=max_dte,
    )
    if not best:
        return None

    # Fetch last price for the chosen contract only (1 extra HTTP call)
    try:
        last_price = _fetch_last_price_for_option(client, best.ticker)
    except Exception as exc:  # noqa: BLE001
        log.debug("option_picker: failed to fetch last price for %s: %s", best.ticker, exc)
        last_price = 0.0

    exp_str = f"{best.expiry.month}/{best.expiry.day}"
    display = f"{underlying} {int(best.strike)}{best.cp} {exp_str}"

    return {
        "ticker": best.ticker,
        "display": display,
        "cp": best.cp,
        "strike": best.strike,
        "expiry": best.expiry.isoformat(),
        "dte": best.dte,
        "underlying_price": best.underlying_price,
        "last_price": last_price if last_price > 0 else None,
    }
