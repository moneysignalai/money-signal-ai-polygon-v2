# core/status_report_v2.py
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from .alerting import TelegramConfig, send_telegram_message


@dataclass
class BotRunRecord:
    last_runtime: float = 0.0
    last_ok: bool = True
    last_error: Optional[str] = None
    last_ts: float = 0.0
    runs: int = 0
    errors: int = 0


@dataclass
class StatusReporter:
    tg_config: TelegramConfig
    report_interval_seconds: int = 600
    _bots: Dict[str, BotRunRecord] = field(default_factory=dict)
    _last_report_ts: float = field(default_factory=lambda: 0.0)

    def record_success(self, bot_name: str, runtime: float) -> None:
        now = time.time()
        rec = self._bots.get(bot_name) or BotRunRecord()
        rec.last_runtime = runtime
        rec.last_ok = True
        rec.last_error = None
        rec.last_ts = now
        rec.runs += 1
        self._bots[bot_name] = rec

    def record_error(self, bot_name: str, error: Exception, runtime: float) -> None:
        now = time.time()
        rec = self._bots.get(bot_name) or BotRunRecord()
        rec.last_runtime = runtime
        rec.last_ok = False
        rec.last_error = str(error)
        rec.last_ts = now
        rec.runs += 1
        rec.errors += 1
        self._bots[bot_name] = rec

    def maybe_report(self) -> None:
        now = time.time()
        if self._last_report_ts and (now - self._last_report_ts) < self.report_interval_seconds:
            return
        self._last_report_ts = now

        if not self._bots:
            text = "🩺 v2 Status: no bot runs recorded yet."
            send_telegram_message(self.tg_config, text)
            return

        lines = ["🩺 *v2 Bot Status Heartbeat*"]

        for bot_name, rec in sorted(self._bots.items()):
            status_emoji = "✅" if rec.last_ok else "❌"
            rt_ms = int(rec.last_runtime * 1000)
            line = f"{status_emoji} `{bot_name}` — last {rt_ms} ms, runs={rec.runs}, errors={rec.errors}"
            if rec.last_error:
                err = rec.last_error
                if len(err) > 80:
                    err = err[:77] + "..."
                line += f"\n  ↳ _{err}_"
            lines.append(line)

        text = "\n".join(lines)
        send_telegram_message(self.tg_config, text)