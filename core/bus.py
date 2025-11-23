# core/bus.py
from __future__ import annotations

from typing import List

from .models import Signal


class SignalBus:
    """In-memory bus where bots publish signals for the current scan cycle."""

    def __init__(self) -> None:
        self._signals: List[Signal] = []

    def publish(self, signal: Signal) -> None:
        self._signals.append(signal)

    def drain(self) -> List[Signal]:
        signals = self._signals
        self._signals = []
        return signals
