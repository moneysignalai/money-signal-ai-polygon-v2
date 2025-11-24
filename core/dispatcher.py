# core/dispatcher.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple, Any

from core.alerting import TelegramConfig, send_telegram_message
from core.context import MarketContext
from core.models import Signal

log = logging.getLogger(__name__)


@dataclass
class Dispatcher:
    """
    Responsible for turning Signals into human‑readable Telegram messages
    and handling basic throttling so we don’t spam the chat.

    - tg_config: Telegram bot/chat config
    - min_alert_interval_seconds: minimum seconds between alerts for the same
      (kind, symbol, direction) triple.
    """
    tg_config: TelegramConfig
    min_alert_interval_seconds: int = 60
    _last_sent: Dict[Tuple[str, str, str], float] = field(default_factory=dict)

    def dispatch(self, sig: Signal, ctx: MarketContext) -> None:
        """
        Send a single Signal to Telegram, subject to throttling.
        """
        key = (sig.kind, sig.symbol, sig.direction)
        now = time.time()
        last_ts = self._last_sent.get(key)

        if last_ts is not None and (now - last_ts) < self.min_alert_interval_seconds:
            log.debug(
                "Dispatcher: skipping %s %s %s due to throttle (%ss < %ss)",
                sig.kind,
                sig.symbol,
                sig.direction,
                int(now - last_ts),
                self.min_alert_interval_seconds,
            )
            return

        text = self._format_signal(sig, ctx)
        send_telegram_message(self.tg_config, text)
        self._last_sent[key] = now

    # --------------------------------------------------------------------- #
    # Internal helpers
    # --------------------------------------------------------------------- #

    def _format_signal(self, sig: Signal, ctx: MarketContext) -> str:
        """
        Turn a Signal + MarketContext into a Markdown message.
        This intentionally only relies on fields that we *know* exist on Signal.
        """
        direction_emoji = {
            "bull": "🟢",
            "bear": "🔴",
            "neutral": "⚪️",
        }.get(sig.direction, "⚪️")

        header = f"{direction_emoji} *{sig.kind}* — `{sig.symbol}`"

        conv_pct = int(round((sig.conviction or 0.0) * 100))
        ctx_line = (
            f"Conviction: *{conv_pct}%* | "
            f"Trend: `{ctx.trend}` | "
            f"Vol: `{ctx.vol_regime}` | "
            f"Risk‑off: *{ctx.risk_off}*"
        )

        # Reasons block
        reasons_block = ""
        if sig.reasons:
            bullets = "\n".join(f"• {r}" for r in sig.reasons)
            reasons_block = f"\n\n*Why:*\n{bullets}"

        # Extra metadata: we keep this simple and robust
        extra_block = self._format_extra(sig.extra or {})

        return f"{header}\n{ctx_line}{reasons_block}{extra_block}"

    def _format_extra(self, extra: Dict[str, Any]) -> str:
        """
        Render a small "Details" section from known extra keys.
        Handles both plain dicts and an OptionPlay instance/dict under 'options_play'.
        """
        lines: list[str] = []

        # Common numeric-ish fields some bots attach
        for key in ("underlying", "last", "volume", "notional", "dte", "expiration_date"):
            if key in extra:
                lines.append(f"- {key}: `{extra[key]}`")

        # Optional attached options play
        if "options_play" in extra:
            opt = extra["options_play"]
            summary = self._summarize_option_play(opt)
            if summary:
                lines.append(f"- Options idea: {summary}")

        if not lines:
            return ""

        return "\n\n*Details:*\n" + "\n".join(lines)

    @staticmethod
    def _summarize_option_play(opt: Any) -> str:
        """
        Try to summarize an OptionPlay (dataclass or dict) without being strict
        about its exact type.
        """
        # dataclass‑like
        if hasattr(opt, "ticker"):
            ticker = getattr(opt, "ticker", None)
            expiry = getattr(opt, "expiry", None)
            strike = getattr(opt, "strike", None)
            kind = getattr(opt, "kind", None)
        # dict‑like
        elif isinstance(opt, dict):
            ticker = opt.get("ticker")
            expiry = opt.get("expiry")
            strike = opt.get("strike")
            kind = opt.get("kind")
        else:
            return ""

        parts = []
        if ticker:
            parts.append(f"`{ticker}`")
        if kind:
            parts.append(kind.upper())
        if strike is not None:
            parts.append(str(strike))
        if expiry:
            parts.append(f"exp `{expiry}`")

        return " ".join(parts)