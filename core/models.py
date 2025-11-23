# core/models.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Literal, Optional

Direction = Literal["bull", "bear", "neutral"]


@dataclass
class Signal:
    bot: str
    symbol: str
    direction: Direction
    conviction: float  # 0.0 – 1.0
    reasons: List[str] = field(default_factory=list)
    timeframe: str = "intraday"  # "scalp" | "swing" | ...
    risk_tag: str = "normal"     # "lotto" | "aggressive" | "normal" | "conservative"
    price: Optional[float] = None
    extra: Dict[str, object] = field(default_factory=dict)
    ts: datetime = field(default_factory=datetime.utcnow)


@dataclass
class MarketContext:
    trend: Literal["bull", "bear", "chop"] = "chop"
    vol_regime: Literal["low", "normal", "high"] = "normal"
    risk_off: bool = False
    computed_at: datetime = field(default_factory=datetime.utcnow)
