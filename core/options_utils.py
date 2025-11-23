# core/options_utils.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional


@dataclass
class ParsedOption:
    underlying: Optional[str]
    expiry: Optional[date]
    cp: Optional[str]  # "C" or "P"
    strike: Optional[float]


def parse_polygon_option_ticker(sym: str) -> ParsedOption:
    """
    Parse Polygon-style option ticker:
      "O:TSLA240118C00255000" or "TSLA240118C00255000"
    """
    if not sym:
        return ParsedOption(None, None, None, None)

    s = sym
    if s.startswith("O:"):
        s = s[2:]

    # underlying = leading letters until first digit
    idx = 0
    while idx < len(s) and not s[idx].isdigit():
        idx += 1

    underlying = s[:idx] or None
    rest = s[idx:]
    if len(rest) < 7:
        return ParsedOption(underlying, None, None, None)

    exp_raw = rest[:6]   # YYMMDD
    cp_char = rest[6]    # C or P
    strike_raw = rest[7:]

    try:
        yy = 2000 + int(exp_raw[0:2])
        mm = int(exp_raw[2:4])
        dd = int(exp_raw[4:6])
        expiry = date(yy, mm, dd)
    except Exception:
        expiry = None

    try:
        strike = int(strike_raw) / 1000.0 if strike_raw else None
    except Exception:
        strike = None

    if cp_char not in ("C", "P"):
        cp_char = None

    return ParsedOption(underlying, expiry, cp_char, strike)


def days_to_expiry(expiry: Optional[date]) -> Optional[int]:
    if not expiry:
        return None
    today = datetime.utcnow().date()
    return (expiry - today).days


def format_option_label(parsed: ParsedOption) -> str:
    """
    Build a clean label like: TSLA 255C 1/18
    Falls back gracefully if info is missing.
    """
    if not parsed.underlying:
        return "UNKNOWN"

    under = parsed.underlying.upper()
    cp = parsed.cp or "?"

    if parsed.strike is None:
        strike_str = "?"
    else:
        strike_val = parsed.strike
        strike_str = str(int(strike_val)) if float(strike_val).is_integer() else f"{strike_val}"

    if parsed.expiry:
        exp = parsed.expiry
        exp_str = f"{exp.month}/{exp.day}"
    else:
        exp_str = "?"

    return f"{under} {strike_str}{cp} {exp_str}"
