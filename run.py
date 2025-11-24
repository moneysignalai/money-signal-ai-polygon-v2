# run.py
from __future__ import annotations

import logging
import time
from time import perf_counter

from config import get_settings
from core.alerting import TelegramConfig, Dispatcher
from core.aggregator import aggregate_signals
from core.bus import SignalBus
from core.context import compute_market_context
from core.option_picker import pick_simple_option_for_signal
from core.polygon_client import PolygonClient
from core.status_report_v2 import StatusReporter

from bots.options_cheap_lottos import run as run_cheap_lottos
from bots.options_unusual import run as run_unusual
from bots.volume_monster import run as run_volume_monster
from bots.orb_breakout import run as run_orb_breakout
from bots.dark_pool_radar import run as run_dark_pool_radar
from bots.trend_swing import run as run_trend_swing
from bots.trend_breakdown import run as run_trend_breakdown
from bots.squeeze_v2 import run as run_squeeze_v2
from bots.squeeze_down_v2 import run as run_squeeze_down_v2
from bots.earnings_momentum import run as run_earnings_momentum

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("runner")


def _run_bot_safely(bot_name: str, fn, status_reporter: StatusReporter, *args, **kwargs) -> None:
    start = perf_counter()
    try:
        fn(*args, **kwargs)
        runtime = perf_counter() - start
        status_reporter.record_success(bot_name, runtime)
    except Exception as exc:  # noqa: BLE001
        runtime = perf_counter() - start
        status_reporter.record_error(bot_name, exc, runtime)
        log.exception("Bot %s failed: %s", bot_name, exc)


def main() -> None:
    settings = get_settings()
    client = PolygonClient(api_key=settings.polygon_api_key)
    bus = SignalBus()

    dispatcher = Dispatcher(
        tg_config=TelegramConfig(
            bot_token=settings.telegram_bot_token,
            chat_id=settings.telegram_chat_id,
        ),
        min_alert_interval_seconds=settings.min_alert_interval_seconds,
    )

    status_tg_config = TelegramConfig(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_status_chat_id or settings.telegram_chat_id,
    )

    status_reporter = StatusReporter(
        tg_config=status_tg_config,
        report_interval_seconds=600,  # heartbeat every 10 min
    )

    log.info("Starting v2 scanner loop...")

    while True:
        try:
            ctx = compute_market_context(client)
            log.info(
                "Market context: trend=%s vol=%s risk_off=%s",
                ctx.trend,
                ctx.vol_regime,
                ctx.risk_off,
            )

            # 1) Run all bots safely, recording status
            _run_bot_safely(
                "cheap_lottos",
                run_cheap_lottos,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_notional=settings.cheap_min_notional,
                max_premium=settings.cheap_max_premium,
                min_volume=settings.cheap_min_volume,
            )

            _run_bot_safely(
                "unusual_sweeps",
                run_unusual,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_notional=settings.unusual_min_notional,
                min_size=settings.unusual_min_size,
                max_dte=settings.unusual_max_dte,
            )

            _run_bot_safely(
                "volume_monster",
                run_volume_monster,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_rvol=settings.volume_min_rvol,
                min_dollar_vol=settings.volume_min_dollar_vol,
            )

            _run_bot_safely(
                "orb_breakout",
                run_orb_breakout,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_r_break=0.002,
            )

            _run_bot_safely(
                "dark_pool_radar",
                run_dark_pool_radar,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_notional=settings.dark_pool_min_notional,
                lookback_minutes=10,
            )

            _run_bot_safely(
                "trend_breakout+swing",
                run_trend_swing,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_price=settings.trend_min_price,
                max_price=settings.trend_max_price,
                min_dollar_vol=settings.trend_min_dollar_vol,
                breakout_lookback=settings.trend_breakout_lookback,
                min_rvol_trend=settings.trend_min_rvol_trend,
                min_rvol_pullback=settings.trend_min_rvol_pullback,
            )

            _run_bot_safely(
                "trend_breakdown",
                run_trend_breakdown,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,  # ✅ FIXED: now a tuple, not a float
                min_price=settings.trend_breakdown_min_price,
                max_price=settings.trend_breakdown_max_price,
                min_dollar_vol=settings.trend_breakdown_min_dollar_vol,
                breakdown_lookback=settings.trend_breakdown_lookback,
                min_rvol_breakdown=settings.trend_breakdown_min_rvol,
            )

            _run_bot_safely(
                "squeeze_up",
                run_squeeze_v2,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_price=settings.squeeze_min_price,
                max_price=settings.squeeze_max_price,
                move_min_pct=settings.squeeze_move_min_pct,
                intraday_min_pct=settings.squeeze_intraday_min_pct,
                min_rvol=settings.squeeze_min_rvol,
                min_dollar_vol=settings.squeeze_min_dollar_vol,
                max_dist_from_high_pct=settings.squeeze_max_dist_from_high_pct,
            )

            _run_bot_safely(
                "squeeze_down",
                run_squeeze_down_v2,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_price=settings.squeeze_down_min_price,
                max_price=settings.squeeze_down_max_price,
                move_min_down_pct=settings.squeeze_down_move_min_pct,
                intraday_min_down_pct=settings.squeeze_down_intraday_min_pct,
                min_rvol=settings.squeeze_down_min_rvol,
                min_dollar_vol=settings.squeeze_down_min_dollar_vol,
                max_dist_from_low_pct=settings.squeeze_down_max_dist_from_low_pct,
            )

            _run_bot_safely(
                "earnings_momentum",
                run_earnings_momentum,
                status_reporter,
                client,
                bus,
                ctx,
                universe=settings.underlying_universe,
                min_price=settings.earnings_min_price,
                max_price=settings.earnings_max_price,
                gap_min_pct=settings.earnings_gap_min_pct,
                move_min_pct=settings.earnings_move_min_pct,
                min_rvol=settings.earnings_min_rvol,
                min_dollar_vol=settings.earnings_min_dollar_vol,
            )

            # 2) Aggregate & dispatch
            raw_signals = bus.drain()
            if raw_signals:
                log.info("Collected %s raw signals", len(raw_signals))

            final_signals = aggregate_signals(raw_signals, ctx)

            # Attach options plays to stock signals
            if settings.option_picker_enabled:
                for sig in final_signals:
                    # crude: only attach if symbol looks like underlying (no space)
                    if " " in sig.symbol:
                        continue
                    opt = pick_simple_option_for_signal(
                        sig,
                        client,
                        target_dte=settings.option_picker_target_dte,
                        min_dte=settings.option_picker_min_dte,
                        max_dte=settings.option_picker_max_dte,
                    )
                    if opt:
                        sig.extra["options_play"] = opt

            for sig in final_signals:
                dispatcher.dispatch(sig, ctx)

            # 3) Status heartbeat
            status_reporter.maybe_report()

        except Exception as exc:  # noqa: BLE001
            log.exception("Error in main loop: %s", exc)

        log.info("Sleeping %s seconds...", settings.scan_interval_seconds)
        time.sleep(settings.scan_interval_seconds)


if __name__ == "__main__":
    main()