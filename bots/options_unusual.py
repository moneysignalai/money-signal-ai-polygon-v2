# bots/options_unusual.py

import logging
from datetime import date, datetime

from core.models import Signal
from core.polygon_client import PolygonClient

log = logging.getLogger(__name__)

_MAX_CONTRACTS_PER_UNDERLYING = 25


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
        log.info("UNUSUAL: no contracts returned for %s", underlying)
    return results


def _fetch_latest_minute_agg(client: PolygonClient, option_ticker: str) -> dict | None:
    """
    Get the latest 1‑minute aggregate for an option contract.

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
        return None
    return results[0]


def run(
    client,
    bus,
    ctx,
    *,
    universe: tuple[str, ...],
    min_notional: float,
    min_size: int,
    max_dte: int,
) -> None:
    """
    UNUSUAL options v2 (approximation).

    Criteria (per contract):
      - DTE <= max_dte
      - volume >= min_size
      - notional = last * volume * 100 >= min_notional

    NOTE: We do NOT have true sweep prints here; this is "big flow" via
    large volume + notional.
    """
    today = date.today()

    for underlying in universe:
        try:
            contracts = _fetch_contracts_for_underlying(client, underlying)
        except Exception as exc:  # noqa: BLE001
            log.warning("UNUSUAL: failed to fetch contracts for %s: %s", underlying, exc)
            continue

        for contract in contracts:
            ticker = contract.get("ticker")
            if not ticker:
                continue

            ctype = (contract.get("contract_type") or contract.get("type") or "").lower()
            if ctype not in ("call", "put"):
                continue

            exp_str = contract.get("expiration_date")
            if not exp_str:
                continue

            try:
                expiry = datetime.fromisoformat(exp_str).date()
            except Exception:  # noqa: BLE001
                continue

            dte = (expiry - today).days
            if dte < 0 or dte > max_dte:
                continue

            try:
                agg = _fetch_latest_minute_agg(client, ticker)
            except Exception as exc:  # noqa: BLE001
                log.debug("UNUSUAL: agg error for %s: %s", ticker, exc)
                continue

            if not agg:
                continue

            last = float(agg.get("c") or 0.0)
            volume = int(agg.get("v") or 0)

            if last <= 0:
                continue
            if volume < min_size:
                continue

            notional = last * volume * 100.0
            if notional < min_notional:
                continue

            direction = "bull" if ctype == "call" else "bear"

            # Conviction scaled by notional and how close DTE is to front side
            dte_factor = max(0.3, min(1.0, (max_dte - dte + 1) / max_dte))
            raw_conv = 0.4 + (notional / max(min_notional * 3.0, 1.0)) * dte_factor
            conviction = max(0.6, min(raw_conv, 0.99))

            reasons = [
                "unusual_flow",
                f"volume≈{volume}",
                f"notional≈${int(notional):,}",
                f"dte≈{dte}",
                f"underlying={underlying}",
            ]

            extra = {
                "underlying": underlying,
                "option_ticker": ticker,
                "last": last,
                "volume": volume,
                "notional": notional,
                "dte": dte,
                "expiration_date": expiry.isoformat(),
                "kind": "unusual_v2",
            }

            # NOTE: first positional arg is the signal type
            sig = Signal(
                "UNUSUAL",
                symbol=ticker,
                direction=direction,
                conviction=round(conviction, 2),
                reasons=reasons,
                extra=extra,
            )
            bus.publish(sig)

        log.info("UNUSUAL: processed %d contracts for %s", len(contracts), underlying)