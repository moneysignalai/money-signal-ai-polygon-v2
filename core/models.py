# core/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

Direction = Literal["bull", "bear", "neutral"]


@dataclass
class Signal:
    """
    Core signal object passed around the bus and into the dispatcher.

    kind        – high‑level category, e.g. "CHEAP_LOTTOS", "UNUSUAL", "TREND_BREAKDOWN"
    symbol      – ticker for the instrument this signal is about
    direction   – "bull", "bear", or "neutral"
    conviction  – 0–1 float, higher = stronger conviction
    reasons     – human‑readable bullet points explaining why this fired
    extra       – any additional structured metadata
    """

    kind: str
    symbol: str
    direction: Direction
    conviction: float
    reasons: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarketContext:
    """
    Snapshot of overall market regime that bots / aggregator can use
    to tune behavior.

    NOTE: keep this in sync with core.context.MarketContext. At runtime
    we mostly treat it as a structural type.
    """

    as_of: datetime
    trend: str          # e.g. "bull", "bear", "range"
    vol_regime: str     # e.g. "low", "normal", "high"
    risk_off: bool      # true if broad risk‑off conditions detected


@dataclass
class OptionPlay:
    """
    Optional attached options idea for a given stock signal.
    Used by core.option_picker and attached into Signal.extra["options_play"].
    """

    ticker: str
    expiry: str                 # ISO date string, e.g. "2025-12-19"
    strike: float
    kind: Literal["call", "put"]
    side: Literal["buy", "sell"] = "buy"
    size: Optional[int] = None  # contracts
    last: Optional[float] = None
    delta: Optional[float] = None
    notes: Optional[str] = None