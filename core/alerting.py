# core/alerting.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Dict, Tuple

import requests

from core.models import Signal
from core.context import MarketContext

log = logging.getLogger(__name__)


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


def send_telegram_message(
    cfg: TelegramConfig,
    text: str,
    parse_mode: str = "Markdown",
) -> None:
    """
    Fire‑and‑forget Telegram send. Used by both Dispatcher and StatusReporter.
    """
    if not cfg.bot_token or not cfg.chat_id:
        log.debug("TelegramConfig missing bot_token or chat_id, skipping send.")
        return

    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    payload = {
        "chat_id": cfg.chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=5)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to send Telegram message: %s", exc)


class Dispatcher:
    """
    Simple alert dispatcher with per‑(symbol, direction, kind) cooldown.

    NOTE: We no longer rely on any non‑existent attributes like `signal.bot`
    or `signal.timeframe`. Throttling is keyed by:

        (signal.symbol, signal.direction, signal.kind)
    """

    def __init__(self, tg_config: TelegramConfig, min_alert_interval_seconds: int = 60):
        self.tg_config = tg_config
        self.min_alert_interval_seconds = max(
            0, int(min_alert_interval_seconds or 0)
        )
        # key -> last_sent_ts
        self._last_sent: Dict[Tuple[str, str, str], float] = {}

    # ---------- internal helpers ----------

    def _key_for_signal(self, signal: Signal) -> tuple[str, str, str]:
        symbol = getattr(signal, "symbol", "?")
        direction = getattr(signal, "direction", "neutral")
        kind = getattr(signal, "kind", "GENERIC")
        return (symbol, direction, kind)

    def _should_send(self, signal: Signal) -> bool:
        if self.min_alert_interval_seconds <= 0:
            return True

        now = time.time()
        key = self._key_for_signal(signal)
        last_ts = self._last_sent.get(key)

        if last_ts is not None and (now - last_ts) < self.min_alert_interval_seconds:
            # Still inside cooldown window
            log.debug("Throttling alert for %s (key=%s)", signal.symbol, key)
            return False

        self._last_sent[key] = now
        return True

    # ---------- formatting ----------

    def _format_signal(self, signal: Signal, ctx: MarketContext) -> str:
        direction_emoji = {
            "bull": "🟢",
            "bear": "🔴",
            "neutral": "⚪️",
        }.get(getattr(signal, "direction", "neutral"), "⚪️")

        header = f"{direction_emoji} *{signal.kind}* on *{signal.symbol}*"
        conv = getattr(signal, "conviction", None)
        if conv is not None:
            header += f"  _(conviction={conv:.2f})_"

        # reasons
        body_lines = []
        for r in getattr(signal, "reasons", []) or []:
            body_lines.append(f"• {r}")

        # optional options play
        extra = getattr(signal, "extra", {}) or {}
        opt = extra.get("options_play")
        if opt:
            try:
                leg = (
                    f"{opt.side.upper()} {opt.kind.upper()} "
                    f"{opt.ticker} {opt.expiry} {opt.strike:.2f}"
                )
                body_lines.append(f"• Options idea: `{leg}`")
            except Exception:  # noqa: BLE001
                # Be defensive; never let formatting kill the alert
                pass

        body = "\n".join(body_lines) if body_lines else "_No detailed reasons provided._"

        ctx_line = (
            f"\n\n_Market: trend={ctx.trend}, vol={ctx.vol_regime}, "
            f"risk_off={ctx.risk_off}_"
        )

        return f"{header}\n{body}{ctx_line}"

    # ---------- public API ----------

    def dispatch(self, signal: Signal, ctx: MarketContext) -> None:
        """
        Decide whether to send an alert for this signal and, if so,
        format and send it via Telegram.
        """
        if not self._should_send(signal):
            return

        text = self._format_signal(signal, ctx)
        send_telegram_message(self.tg_config, text)