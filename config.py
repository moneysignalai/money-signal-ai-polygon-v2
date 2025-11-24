# config.py
import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    polygon_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str          # alerts
    telegram_status_chat_id: str | None = None  # optional separate status channel

    scan_interval_seconds: int = 60

    # Base universe for all equity/option bots
    underlying_universe: tuple = ("SPY", "QQQ", "TSLA", "NVDA")

    # Cheap lotto options
    cheap_min_notional: float = 50_000.0
    cheap_max_premium: float = 1.50
    cheap_min_volume: int = 100

    # UNUSUAL sweeps
    unusual_min_notional: float = 100_000.0
    unusual_min_size: int = 50
    unusual_max_dte: int = 45

    # Volume Monster
    volume_min_rvol: float = 3.0
    volume_min_dollar_vol: float = 5_000_000.0

    # Dark pool radar
    dark_pool_min_notional: float = 500_000.0

    # Trend + Swing (bullish)
    trend_min_price: float = 5.0
    trend_max_price: float = 500.0
    trend_min_dollar_vol: float = 5_000_000.0
    trend_breakout_lookback: int = 20
    trend_min_rvol_trend: float = 1.5
    trend_min_rvol_pullback: float = 1.0

    # Trend breakdown (bearish)
    trend_breakdown_min_price: float = 5.0
    trend_breakdown_max_price: float = 500.0
    trend_breakdown_min_dollar_vol: float = 5_000_000.0
    trend_breakdown_lookback: int = 20
    trend_breakdown_min_rvol: float = 1.5

    # Squeeze up (shorts dying to the upside)
    squeeze_min_price: float = 5.0
    squeeze_max_price: float = 500.0
    squeeze_move_min_pct: float = 15.0
    squeeze_intraday_min_pct: float = 8.0
    squeeze_min_rvol: float = 3.0
    squeeze_min_dollar_vol: float = 20_000_000.0
    squeeze_max_dist_from_high_pct: float = 10.0

    # Squeeze down (short gamma unwind down)
    squeeze_down_min_price: float = 5.0
    squeeze_down_max_price: float = 500.0
    squeeze_down_move_min_pct: float = 12.0
    squeeze_down_intraday_min_pct: float = 7.0
    squeeze_down_min_rvol: float = 3.0
    squeeze_down_min_dollar_vol: float = 20_000_000.0
    squeeze_down_max_dist_from_low_pct: float = 10.0

    # Earnings momentum / fade
    earnings_min_price: float = 5.0
    earnings_max_price: float = 500.0
    earnings_gap_min_pct: float = 5.0
    earnings_move_min_pct: float = 8.0
    earnings_min_rvol: float = 2.0
    earnings_min_dollar_vol: float = 10_000_000.0

    # Option picker (now ON again)
    option_picker_enabled: bool = True
    option_picker_target_dte: int = 30
    option_picker_min_dte: int = 7
    option_picker_max_dte: int = 60

    # Dedup / throttling
    min_alert_interval_seconds: int = 600


def get_settings() -> Settings:
    api_key = os.getenv("POLYGON_API_KEY") or os.getenv("POLYGON_KEY")
    if not api_key:
        raise RuntimeError("Missing POLYGON_API_KEY (or POLYGON_KEY) env variable")

    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    tg_status = os.getenv("TELEGRAM_STATUS_CHAT_ID")

    if not (tg_token and tg_chat):
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID env variables")

    return Settings(
        polygon_api_key=api_key,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat,
        telegram_status_chat_id=tg_status,
    )
