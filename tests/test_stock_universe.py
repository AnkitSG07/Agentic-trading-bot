from types import SimpleNamespace

from brokers.base import Exchange, Instrument, InstrumentType
from data.stock_universe import get_cached_nse_equity_symbols, load_nse_equity_symbols


def test_load_nse_equity_symbols_filters_cash_equities_and_sorts_deterministically():
    instruments = {
        "RELIANCE": Instrument(symbol="RELIANCE", exchange=Exchange.NSE, instrument_type=InstrumentType.EQ),
        "NIFTY50": Instrument(symbol="NIFTY50", exchange=Exchange.NSE, instrument_type=InstrumentType.EQ),
        "BANKNIFTY24APR": Instrument(symbol="BANKNIFTY24APR", exchange=Exchange.NSE, instrument_type=InstrumentType.FUT),
        "HDFCBANK": Instrument(symbol="HDFCBANK", exchange=Exchange.NSE, instrument_type=InstrumentType.EQ),
        "GOLDBEES": Instrument(symbol="GOLDBEES", exchange=Exchange.NSE, instrument_type=InstrumentType.EQ),
        "SBIN": Instrument(symbol="SBIN", exchange=Exchange.BSE, instrument_type=InstrumentType.EQ),
    }

    assert load_nse_equity_symbols(instruments) == ["HDFCBANK", "RELIANCE"]


def test_get_cached_nse_equity_symbols_normalizes_non_empty_cache_only():
    engine = SimpleNamespace(_nse_equity_symbols_cache=[" reliance ", "", "sbin"])

    assert get_cached_nse_equity_symbols(engine) == ["RELIANCE", "SBIN"]
    assert get_cached_nse_equity_symbols(SimpleNamespace(_nse_equity_symbols_cache=[])) == []
