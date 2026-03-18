from __future__ import annotations

from collections.abc import Mapping

from brokers.base import Exchange, Instrument, InstrumentType

_NON_TRADABLE_PREFIXES = (
    "NIFTY",
    "BANKNIFTY",
    "FINNIFTY",
    "MIDCPNIFTY",
    "SENSEX",
)
_NON_TRADABLE_TOKENS = (
    " INDEX",
    "ETF",
    "BEES",
    "LIQUID",
    "GOLD",
    "SILVER",
    "FUND",
)
_DERIVATIVE_MARKERS = (" FUT", " CE", " PE")


def is_nse_cash_equity_symbol(symbol: str, instrument: Instrument) -> bool:
    normalized = str(symbol or "").strip().upper()
    if not normalized:
        return False
    if instrument.exchange != Exchange.NSE or instrument.instrument_type != InstrumentType.EQ:
        return False
    if any(normalized.startswith(prefix) for prefix in _NON_TRADABLE_PREFIXES):
        return False
    if any(marker in normalized for marker in _DERIVATIVE_MARKERS):
        return False
    if any(token in normalized for token in _NON_TRADABLE_TOKENS):
        return False
    return normalized.isalnum()


def load_nse_equity_symbols(instrument_cache: Mapping[str, Instrument] | None) -> list[str]:
    if not instrument_cache:
        return []

    symbols = {
        str(symbol or "").strip().upper()
        for symbol, instrument in instrument_cache.items()
        if instrument and is_nse_cash_equity_symbol(symbol, instrument)
    }
    return sorted(symbols)


def get_cached_nse_equity_symbols(engine: object | None) -> list[str]:
    if not engine:
        return []
    cached = getattr(engine, "_nse_equity_symbols_cache", None)
    if cached:
        return [str(symbol).strip().upper() for symbol in cached if str(symbol).strip()]
    return []
