# core/aggregator.py
from __future__ import annotations

import logging
from collections import defaultdict
from typing import List

from core.models import Signal

log = logging.getLogger(__name__)


def aggregate_signals(signals: List[Signal], ctx) -> List[Signal]:
    """
    Very lightweight, robust signal aggregator.

    - Groups signals by (symbol, direction)
    - Within each group:
        * picks the highest-conviction signal as the "winner"
        * merges reasons (deduped, order-preserving)
        * merges extra dicts (later keys overwrite earlier)
    - Does NOT depend on any optional attributes like `timeframe`.

    `ctx` is currently unused, but kept in the signature so you can add
    regime-aware logic later without changing callers.
    """
    if not signals:
        return []

    grouped: dict[tuple[str, str], list[Signal]] = defaultdict(list)

    for sig in signals:
        key = (sig.symbol, sig.direction)
        grouped[key].append(sig)

    final: List[Signal] = []

    for (symbol, direction), group in grouped.items():
        if not group:
            continue

        # pick highest-conviction signal in the group
        best = max(group, key=lambda s: getattr(s, "conviction", 0.0))

        # merge reasons, preserving order and removing duplicates
        merged_reasons: list[str] = []
        for s in group:
            for r in getattr(s, "reasons", []) or []:
                if r not in merged_reasons:
                    merged_reasons.append(r)

        # merge extra dicts (later signals can overwrite keys)
        merged_extra: dict = {}
        for s in group:
            extra = getattr(s, "extra", {}) or {}
            if isinstance(extra, dict):
                merged_extra.update(extra)

        # mutate the chosen signal in-place so we don't need to know
        # the exact __init__ signature of Signal.
        best.reasons = merged_reasons
        best.extra = merged_extra

        final.append(best)

    log.debug("aggregate_signals: input=%d, output=%d", len(signals), len(final))
    return final