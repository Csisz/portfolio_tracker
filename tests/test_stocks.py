"""
Részvényárfolyam lekérés tesztek – Stooq mapping, fallback sorrend.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import csv
import io
from unittest.mock import patch, MagicMock

from services.stocks import (
    _bd_to_stooq, _to_stooq, _fetch_price_stooq, _fetch_price_yfinance, get_historical_price,
    get_last_price, get_prices_for_tickers, normalize_ticker,
)


def _quote(price, currency="HUF", source="Yahoo Finance", quote_time="2026-06-04T10:05:00+02:00", stale=False, delayed=False):
    return {
        "price": price,
        "currency": currency,
        "source": source,
        "quote_time": quote_time,
        "received_at": "2026-06-04T10:05:06+02:00",
        "stale": stale,
        "delayed": delayed,
        "market_state": "REGULAR",
        "timestamp": quote_time,
    }


# ===========================================================================
# Stooq ticker mapping
# ===========================================================================

def test_stooq_mapping_otp():
    assert _bd_to_stooq("OTP.BD") == "otp.hu"


def test_normalize_hungarian_blue_chips():
    assert normalize_ticker("OTP") == "OTP.BD"
    assert normalize_ticker("otp") == "OTP.BD"
    assert normalize_ticker("MOL") == "MOL.BD"
    assert normalize_ticker("MOL.BD") == "MOL.BD"


def test_stooq_mapping_mol():
    assert _bd_to_stooq("MOL.BD") == "mol.hu"


def test_stooq_mapping_richter():
    assert _bd_to_stooq("RICHTER.BD") == "richter.hu"


def test_stooq_mapping_mtelekom():
    assert _bd_to_stooq("MTELEKOM.BD") == "mtelekom.hu"


def test_stooq_mapping_opus():
    assert _bd_to_stooq("OPUS.BD") == "opus.hu"


def test_stooq_mapping_4ig():
    assert _bd_to_stooq("4IG.BD") == "4ig.hu"


def test_stooq_mapping_lowercase_input():
    assert _bd_to_stooq("otp.bd") == "otp.hu"


def test_stooq_mapping_non_bd_returns_none():
    assert _bd_to_stooq("AAPL") is None
    assert _bd_to_stooq("BMW.DE") is None
    assert _bd_to_stooq("MSFT") is None


# ===========================================================================
# Stooq CSV parser
# ===========================================================================

_STOOQ_CSV_OK = "Symbol,Date,Time,Open,High,Low,Close,Volume\nOTP.HU,2026-06-04,17:05:03,26350,26350,26000,26000,163861\n"
_STOOQ_CSV_ND = "Symbol,Date,Time,Open,High,Low,Close,Volume\nN/D,N/D,N/D,N/D,N/D,N/D,N/D,N/D\n"
_STOOQ_CSV_ZERO = "Symbol,Date,Time,Open,High,Low,Close,Volume\nOTP.HU,2026-06-04,17:05:03,0,0,0,0,0\n"


def _mock_stooq_response(csv_text):
    resp = MagicMock()
    resp.text = csv_text
    resp.raise_for_status = MagicMock()
    return resp


def test_fetch_price_stooq_ok():
    with patch("services.stocks.requests.get", return_value=_mock_stooq_response(_STOOQ_CSV_OK)):
        quote = _fetch_price_stooq("otp.hu", "HUF")
    assert quote["price"] == 26000.0
    assert quote["currency"] == "HUF"
    assert quote["quote_time"] == "2026-06-04T17:05:03"
    assert quote["delayed"] is True


def test_fetch_price_stooq_nd_returns_none():
    with patch("services.stocks.requests.get", return_value=_mock_stooq_response(_STOOQ_CSV_ND)):
        quote = _fetch_price_stooq("otp.hu", "HUF")
    assert quote is None


def test_fetch_price_stooq_zero_returns_none():
    with patch("services.stocks.requests.get", return_value=_mock_stooq_response(_STOOQ_CSV_ZERO)):
        quote = _fetch_price_stooq("otp.hu", "HUF")
    assert quote is None


def test_fetch_price_stooq_network_error():
    import requests as req
    with patch("services.stocks.requests.get", side_effect=req.exceptions.ConnectionError):
        quote = _fetch_price_stooq("otp.hu", "HUF")
    assert quote is None


# ===========================================================================
# Fallback sorrend: Yahoo fail → Stooq
# ===========================================================================

def test_get_last_price_falls_back_to_stooq_on_yahoo_fail():
    """Ha yfinance nem ad árat .BD tickerre, Stooqra esik vissza."""
    # cache ürítés
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=None), \
         patch("services.stocks._fetch_price_stooq", return_value=_quote(26000.0, "HUF", "Stooq - késleltetett", delayed=True)) as mock_stooq:
        quote = get_last_price("OTP.BD")

    mock_stooq.assert_called_once_with("otp.hu", "HUF")
    assert quote["price"] == 26000.0
    assert quote["currency"] == "HUF"
    assert quote["source"] == "Stooq - késleltetett"


def test_get_last_price_normalizes_bare_hungarian_ticker():
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=None), \
         patch("services.stocks._fetch_price_stooq", return_value=_quote(26000.0, "HUF", "Stooq - késleltetett", delayed=True)) as mock_stooq:
        quote = get_last_price("OTP")

    mock_stooq.assert_called_once_with("otp.hu", "HUF")
    assert quote["price"] == 26000.0
    assert quote["currency"] == "HUF"
    assert quote["source"] == "Stooq - késleltetett"
    assert quote["stale"] is False


def test_get_last_price_stooq_called_for_us_ticker():
    """US tickernél (pl. AAPL) Stooq aapl.us-szal hívódik, ha Yahoo nem ad árat."""
    from services import cache as svc_cache
    svc_cache.delete("price:AAPL")

    with patch("services.stocks._fetch_price_yfinance", return_value=None), \
         patch("services.stocks._fetch_price_stooq", return_value=_quote(195.0, "USD", "Stooq - késleltetett", delayed=True)) as mock_stooq:
        quote = get_last_price("AAPL")

    mock_stooq.assert_called_once_with("aapl.us", "USD")
    assert quote["price"] == 195.0
    assert quote["currency"] == "USD"
    assert quote["source"] == "Stooq - késleltetett"


def test_get_last_price_yahoo_wins_over_stooq():
    """Ha yfinance ad árat, Stooq nem hívódik."""
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=_quote(27500.0, "HUF", "Yahoo Finance")), \
         patch("services.stocks._fetch_price_stooq") as mock_stooq:
        quote = get_last_price("OTP.BD")

    mock_stooq.assert_not_called()
    assert quote["price"] == 27500.0
    assert quote["source"] == "Yahoo Finance"


def test_get_last_price_uses_memory_cache_when_not_forced():
    from services import cache as svc_cache
    svc_cache.set("price:AAPL", _quote(100.0, "USD", "Yahoo Finance", quote_time="2026-06-04T10:00:00+02:00"), 600)

    with patch("services.stocks._fetch_price_yfinance") as mock_yahoo:
        quote = get_last_price("AAPL")

    mock_yahoo.assert_not_called()
    assert quote["price"] == 100.0
    assert quote["currency"] == "USD"
    assert quote["source"] == "Yahoo Finance / cache"
    assert quote["quote_time"] == "2026-06-04T10:00:00+02:00"
    assert quote["stale"] is False
    svc_cache.delete("price:AAPL")


def test_get_last_price_force_refresh_skips_memory_cache():
    from services import cache as svc_cache
    svc_cache.set("price:AAPL", _quote(100.0, "USD", "Yahoo Finance"), 600)

    with patch("services.stocks._fetch_price_yfinance", return_value=_quote(200.0, "USD", "Yahoo Finance")) as mock_yahoo, \
         patch("services.stocks._fetch_price_stooq") as mock_stooq:
        quote = get_last_price("AAPL", force_refresh=True)

    mock_yahoo.assert_called_once_with("AAPL")
    mock_stooq.assert_not_called()
    assert quote["price"] == 200.0
    assert quote["currency"] == "USD"
    assert quote["source"] == "Yahoo Finance"
    assert quote["stale"] is False
    svc_cache.delete("price:AAPL")


def test_get_last_price_returns_4_tuple():
    """get_last_price() mindig 4-elemet ad vissza."""
    from services import cache as svc_cache
    svc_cache.delete("price:AAPL")

    with patch("services.stocks._fetch_price_yfinance", return_value=_quote(195.0, "USD")):
        result = get_last_price("AAPL")

    assert {"price", "currency", "source", "quote_time", "received_at", "stale", "delayed", "market_state"}.issubset(result)
    assert result["stale"] is False


def test_get_last_price_stale_fallback():
    """Ha sem Yahoo, sem Stooq nem ad árat, stale cache-ből jön."""
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=None), \
         patch("services.stocks._fetch_price_stooq", return_value=None), \
         patch("services.stocks._get_stale_price", return_value=(25000.0, "HUF", "2026-06-03T17:05:00")):
        quote = get_last_price("OTP.BD")

    assert quote["price"] == 25000.0
    assert quote["stale"] is True
    assert quote["delayed"] is True
    assert quote["source"] == "Utolsó ismert árfolyam"
    assert quote["quote_time"] == "2026-06-03T17:05:00"


# ===========================================================================
# get_prices_for_tickers – stale flag átadása
# ===========================================================================

def test_prices_for_tickers_stale_flag_in_response():
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=None), \
         patch("services.stocks._fetch_price_stooq", return_value=None), \
         patch("services.stocks._get_stale_price", return_value=(24000.0, "HUF", "2026-06-03T17:05:00")):
        result = get_prices_for_tickers(["OTP.BD"])

    assert "OTP.BD" in result["prices"]
    assert result["prices"]["OTP.BD"]["stale"] is True
    assert result["prices"]["OTP.BD"]["timestamp"] == "2026-06-03T17:05:00"
    assert result["prices"]["OTP.BD"]["quote_time"] == "2026-06-03T17:05:00"


def test_prices_for_tickers_passes_force_refresh_to_each_lookup():
    with patch("services.stocks.get_last_price", return_value=_quote(195.0, "USD", "Yahoo Finance")) as mocked:
        result = get_prices_for_tickers(["AAPL"], force_refresh=True)

    mocked.assert_called_once_with("AAPL", force_refresh=True)
    assert result["prices"]["AAPL"]["price"] == 195.0


def test_prices_for_tickers_no_stale_flag_when_live():
    from services import cache as svc_cache
    svc_cache.delete("price:AAPL")

    with patch("services.stocks._fetch_price_yfinance", return_value=_quote(195.0, "USD")):
        result = get_prices_for_tickers(["AAPL"])

    assert "AAPL" in result["prices"]
    assert result["prices"]["AAPL"].get("stale") is not True


def test_yfinance_intraday_1m_uses_last_close_and_index_quote_time():
    import pandas as pd

    fake_ticker = MagicMock()
    fake_ticker.fast_info.currency = "USD"
    fake_ticker.fast_info.market_state = "REGULAR"
    fake_ticker.history.return_value = pd.DataFrame(
        {"Close": [194.0, None, 195.5]},
        index=pd.to_datetime(["2026-06-04 15:30:00+00:00", "2026-06-04 15:31:00+00:00", "2026-06-04 15:32:00+00:00"]),
    )

    with patch("services.stocks.yf.Ticker", return_value=fake_ticker):
        quote = _fetch_price_yfinance("AAPL")

    fake_ticker.history.assert_called_with(period="1d", interval="1m", auto_adjust=False, prepost=False)
    assert quote["price"] == 195.5
    assert quote["quote_time"].startswith("2026-06-04T15:32:00")
    assert quote["delayed"] is False


def test_yfinance_empty_1m_tries_5m():
    import pandas as pd

    fake_ticker = MagicMock()
    fake_ticker.fast_info.currency = "USD"
    fake_ticker.fast_info.market_state = "REGULAR"
    empty = pd.DataFrame()
    five_min = pd.DataFrame({"Close": [196.0]}, index=pd.to_datetime(["2026-06-04 15:35:00+00:00"]))
    fake_ticker.history.side_effect = [empty, five_min]

    with patch("services.stocks.yf.Ticker", return_value=fake_ticker):
        quote = _fetch_price_yfinance("AAPL")

    assert fake_ticker.history.call_args_list[0].kwargs["interval"] == "1m"
    assert fake_ticker.history.call_args_list[1].kwargs["interval"] == "5m"
    assert quote["price"] == 196.0


def test_yfinance_daily_fallback_is_delayed():
    import pandas as pd

    fake_ticker = MagicMock()
    fake_ticker.fast_info.currency = "USD"
    fake_ticker.fast_info.market_state = "CLOSED"
    empty = pd.DataFrame()
    daily = pd.DataFrame({"Close": [190.0]}, index=pd.to_datetime(["2026-06-03"]))
    fake_ticker.history.side_effect = [empty, empty, daily]
    fake_ticker.fast_info.last_price = None

    with patch("services.stocks.yf.Ticker", return_value=fake_ticker):
        quote = _fetch_price_yfinance("AAPL")

    assert quote["price"] == 190.0
    assert quote["delayed"] is True
    assert "záróár" in quote["source"]


def test_get_historical_price_uses_previous_trading_day():
    import pandas as pd

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame(
        {"Close": [180.0, 185.5]},
        index=pd.to_datetime(["2024-01-11", "2024-01-12"]),
    )
    fake_ticker.fast_info.currency = "USD"

    with patch("services.stocks.yf.Ticker", return_value=fake_ticker):
        result = get_historical_price("AAPL", "2024-01-15")

    assert result["ok"] is True
    assert result["price"] == 185.5
    assert result["used_date"] == "2024-01-12"
    assert result["currency"] == "USD"


def test_get_historical_price_invalid_date():
    result = get_historical_price("AAPL", "not-a-date")
    assert result["ok"] is False


# ===========================================================================
# _to_stooq – általános mapping
# ===========================================================================

def test_to_stooq_us_aapl():
    sym, cur = _to_stooq("AAPL")
    assert sym == "aapl.us"
    assert cur == "USD"


def test_to_stooq_us_msft():
    sym, cur = _to_stooq("MSFT")
    assert sym == "msft.us"
    assert cur == "USD"


def test_to_stooq_us_tsla():
    sym, cur = _to_stooq("TSLA")
    assert sym == "tsla.us"
    assert cur == "USD"


def test_to_stooq_de_bmw():
    sym, cur = _to_stooq("BMW.DE")
    assert sym == "bmw.de"
    assert cur == "EUR"


def test_to_stooq_l_shell():
    sym, cur = _to_stooq("SHEL.L")
    assert sym == "shel.uk"
    assert cur == "GBp"


def test_to_stooq_hu_otp():
    sym, cur = _to_stooq("OTP.BD")
    assert sym == "otp.hu"
    assert cur == "HUF"


def test_to_stooq_us_with_dash():
    sym, cur = _to_stooq("BRK-B")
    assert sym == "brk.b.us"
    assert cur == "USD"


def test_to_stooq_unknown_suffix():
    sym, cur = _to_stooq("WEIRD.XZ")
    assert sym is None
    assert cur is None
