"""
Részvényárfolyam lekérés tesztek – Stooq mapping, fallback sorrend.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import csv
import io
from unittest.mock import patch, MagicMock

from services.stocks import _bd_to_stooq, _to_stooq, _fetch_price_stooq, get_historical_price, get_last_price, get_prices_for_tickers


# ===========================================================================
# Stooq ticker mapping
# ===========================================================================

def test_stooq_mapping_otp():
    assert _bd_to_stooq("OTP.BD") == "otp.hu"


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
        price, currency = _fetch_price_stooq("otp.hu", "HUF")
    assert price == 26000.0
    assert currency == "HUF"


def test_fetch_price_stooq_nd_returns_none():
    with patch("services.stocks.requests.get", return_value=_mock_stooq_response(_STOOQ_CSV_ND)):
        price, currency = _fetch_price_stooq("otp.hu", "HUF")
    assert price is None


def test_fetch_price_stooq_zero_returns_none():
    with patch("services.stocks.requests.get", return_value=_mock_stooq_response(_STOOQ_CSV_ZERO)):
        price, currency = _fetch_price_stooq("otp.hu", "HUF")
    assert price is None


def test_fetch_price_stooq_network_error():
    import requests as req
    with patch("services.stocks.requests.get", side_effect=req.exceptions.ConnectionError):
        price, currency = _fetch_price_stooq("otp.hu", "HUF")
    assert price is None


# ===========================================================================
# Fallback sorrend: Yahoo fail → Stooq
# ===========================================================================

def test_get_last_price_falls_back_to_stooq_on_yahoo_fail():
    """Ha yfinance nem ad árat .BD tickerre, Stooqra esik vissza."""
    # cache ürítés
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=(None, None)), \
         patch("services.stocks._fetch_price_stooq", return_value=(26000.0, "HUF")) as mock_stooq:
        price, currency, source, stale = get_last_price("OTP.BD")

    mock_stooq.assert_called_once_with("otp.hu", "HUF")
    assert price == 26000.0
    assert currency == "HUF"
    assert source == "Stooq"
    assert stale is False


def test_get_last_price_stooq_called_for_us_ticker():
    """US tickernél (pl. AAPL) Stooq aapl.us-szal hívódik, ha Yahoo nem ad árat."""
    from services import cache as svc_cache
    svc_cache.delete("price:AAPL")

    with patch("services.stocks._fetch_price_yfinance", return_value=(None, None)), \
         patch("services.stocks._fetch_price_stooq", return_value=(195.0, "USD")) as mock_stooq:
        price, currency, source, stale = get_last_price("AAPL")

    mock_stooq.assert_called_once_with("aapl.us", "USD")
    assert price == 195.0
    assert currency == "USD"
    assert source == "Stooq"


def test_get_last_price_yahoo_wins_over_stooq():
    """Ha yfinance ad árat, Stooq nem hívódik."""
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=(27500.0, "HUF")), \
         patch("services.stocks._fetch_price_stooq") as mock_stooq:
        price, currency, source, stale = get_last_price("OTP.BD")

    mock_stooq.assert_not_called()
    assert price == 27500.0
    assert source == "Yahoo Finance"


def test_get_last_price_returns_4_tuple():
    """get_last_price() mindig 4-elemet ad vissza."""
    from services import cache as svc_cache
    svc_cache.delete("price:AAPL")

    with patch("services.stocks._fetch_price_yfinance", return_value=(195.0, "USD")):
        result = get_last_price("AAPL")

    assert len(result) == 4
    price, currency, source, stale = result
    assert stale is False


def test_get_last_price_stale_fallback():
    """Ha sem Yahoo, sem Stooq nem ad árat, stale cache-ből jön."""
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=(None, None)), \
         patch("services.stocks._fetch_price_stooq", return_value=(None, None)), \
         patch("services.stocks._get_stale_price", return_value=(25000.0, "HUF")):
        price, currency, source, stale = get_last_price("OTP.BD")

    assert price == 25000.0
    assert stale is True
    assert source == "stale"


# ===========================================================================
# get_prices_for_tickers – stale flag átadása
# ===========================================================================

def test_prices_for_tickers_stale_flag_in_response():
    from services import cache as svc_cache
    svc_cache.delete("price:OTP.BD")

    with patch("services.stocks._fetch_price_yfinance", return_value=(None, None)), \
         patch("services.stocks._fetch_price_stooq", return_value=(None, None)), \
         patch("services.stocks._get_stale_price", return_value=(24000.0, "HUF")):
        result = get_prices_for_tickers(["OTP.BD"])

    assert "OTP.BD" in result["prices"]
    assert result["prices"]["OTP.BD"]["stale"] is True


def test_prices_for_tickers_no_stale_flag_when_live():
    from services import cache as svc_cache
    svc_cache.delete("price:AAPL")

    with patch("services.stocks._fetch_price_yfinance", return_value=(195.0, "USD")):
        result = get_prices_for_tickers(["AAPL"])

    assert "AAPL" in result["prices"]
    assert result["prices"]["AAPL"].get("stale") is not True


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


def test_get_historical_price_rejects_previous_day_outside_7_days():
    import pandas as pd

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame(
        {"Close": [180.0]},
        index=pd.to_datetime(["2024-01-07"]),
    )

    with patch("services.stocks.yf.Ticker", return_value=fake_ticker):
        result = get_historical_price("AAPL", "2024-01-15")

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
