# core/alerting.py
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, Tuple

import requests

from .models import Signal, MarketContext

log = logging.getLogger(__name__)


@dataclass
class TelegramConfig:
    bot_token: str
    chat_id: str


def _format_signal_text(signal: Signal, ctx: MarketContext) -> str:
    dir_emoji = {"bull": "🟢", "bear": "🔴", "neutral": "⚪"}.get(signal.direction, "⚪")
    risk_emoji = {
        "lotto": "🎲",
        "aggressive": "⚠️",
        "normal": "📈",
        "conservative": "🛡️",
    }.get(signal.risk_tag, "📈")

    reasons_str = " • ".join(signal.reasons) if signal.reasons else "n/a"
    price_str = f"{signal.price:.2f}" if signal.price is not None else "n/a"

    header = f"{dir_emoji} {signal.bot.upper()} — {signal.symbol}"
    line1 = f"{risk_emoji} Direction: **{signal.direction.upper()}**  (Conviction: {signal.conviction:.2f})"
    line2 = f"⏱ Timeframe: {signal.timeframe} | Market: {ctx.trend}/{ctx.vol_regime}{' (RISK-OFF)' if ctx.risk_off else ''}"
    line3 = f"💰 Last: {price_str}"
    line4 = f"📌 Reasons: {reasons_str}"

    lines = [header, line1, line2, line3, line4]

    opt_play = signal.extra.get("options_play") if isinstance(signal.extra, dict) else None
    if isinstance(opt_play, dict) and opt_play.get("display"):
        lines.append(f"🎯 Options: {opt_play['display']}")

    return "\n".join(lines)


def send_telegram_message(cfg: TelegramConfig, text: str) -> None:
    url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
    payload = {
        "chat_id": cfg.chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=5.0)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        log.error("Failed to send Telegram message: %s", exc)


@dataclass
class Dispatcher:
    tg_config: TelegramConfig
    min_alert_interval_seconds: int
    last_sent: Dict[Tuple[str, str, str], float] = field(default_factory=dict)

    def _should_send(self, signal: Signal) -> bool:
        key = (signal.symbol, signal.direction, signal.bot)
        now = time.time()
        last = self.last_sent.get(key)

        if last is not None and (now - last) < self.min_alert_interval_seconds:
            return False

        self.last_sent[key] = now
        return True

    def dispatch(self, signal: Signal, ctx: MarketContext) -> None:
        if not self._should_send(signal):
            return

        text = _format_signal_text(signal, ctx)
        send_telegram_message(self.tg_config, text)
