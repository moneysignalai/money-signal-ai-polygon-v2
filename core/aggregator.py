# core/aggregator.py
from __future__ import annotations

from collections import defaultdict
from typing import Dict, Iterable, List, Tuple

from .models import Signal, MarketContext


def _combine_convictions(convictions: Iterable[float]) -> float:
    """1 - Π(1 - p_i) style combination."""
    prob_not = 1.0
    for p in convictions:
        p = max(0.0, min(1.0, p))
        prob_not *= (1.0 - p)
    return 1.0 - prob_not


def aggregate_signals(signals: List[Signal], ctx: MarketContext, min_conviction: float = 0.4) -> List[Signal]:
    grouped: Dict[Tuple[str, str], List[Signal]] = defaultdict(list)
    for s in signals:
        grouped[(s.symbol, s.direction)].append(s)

    final: List[Signal] = []

    for (symbol, direction), group in grouped.items():
        convictions = [s.conviction for s in group]
        combined_conviction = _combine_convictions(convictions)
        reasons: List[str] = []
        price = None
        timeframe = group[0].timeframe
        risk_tag = group[0].risk_tag

        bots = sorted({s.bot for s in group})
        extra_combined: Dict[str, object] = {}

        for s in group:
            for r in s.reasons:
                if r not in reasons:
                    reasons.append(r)
            if s.price is not None:
                price = s.price
            for k, v in s.extra.items():
                extra_combined[f"{s.bot}.{k}"] = v

        # regime gating
        if ctx.risk_off and direction == "bull" and combined_conviction < 0.7:
            continue
        if ctx.trend == "bull" and direction == "bear" and combined_conviction < 0.6:
            continue

        if combined_conviction < min_conviction:
            continue

        merged = Signal(
            bot="+".join(bots),
            symbol=symbol,
            direction=direction,  # type: ignore[arg-type]
            conviction=combined_conviction,
            reasons=reasons,
            timeframe=timeframe,
            risk_tag=risk_tag,
            price=price,
            extra=extra_combined,
        )
        final.append(merged)

    return final
