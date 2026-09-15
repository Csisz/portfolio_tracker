"""
Ticker keresés / fallback tesztek – internet nélkül (Yahoo mock).
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch

import services.symbol_resolver as sr


def _tickers(results):
    return [r["ticker"] for r in results]


@pytest.mark.parametrize("symbol", ["HU0000722590", " hu0000722590 "])
def test_hungarian_isin_recognition_and_normalization(symbol):
    assert sr.is_hungarian_isin(symbol) is True
    assert sr.normalize_isin(symbol) == "HU0000722590"


@pytest.mark.parametrize("symbol", ["AAPL", "HU000072259", "HU000072259X", "DE0000722590"])
def test_non_hungarian_or_malformed_isin_is_not_recognized(symbol):
    assert sr.is_hungarian_isin(symbol) is False


def test_isin_search_uses_bamosz_and_never_yahoo():
    fund = {
        "symbol": "HU0000722590",
        "name": "Teszt Alap – B sorozat",
        "price": 2.98626,
        "currency": "EUR",
        "quote_date": "2026-09-10",
        "source": "BAMOSZ",
    }
    with patch("services.symbol_resolver.fetch_fund_quote", return_value=fund) as bamosz, \
         patch("services.symbol_resolver._search_yahoo") as yahoo:
        result = sr.search(" hu0000722590 ")

    bamosz.assert_called_once_with("HU0000722590")
    yahoo.assert_not_called()
    assert result["results"][0]["ticker"] == "HU0000722590"
    assert result["results"][0]["currency"] == "EUR"
    assert result["results"][0]["type"] == "FUND"


def test_malformed_hungarian_isin_search_never_calls_yahoo():
    with patch("services.symbol_resolver._search_yahoo") as yahoo:
        result = sr.search("HU000072259")

    yahoo.assert_not_called()
    assert result["results"] == []
    assert "Érvénytelen magyar ISIN" in result["errors"][0]


# ---- Helyi fallback keresés ----

def test_search_otp_exact():
    local = sr._search_local("OTP")
    assert any(r["ticker"] == "OTP.BD" for r in local)


def test_search_otp_lowercase():
    local = sr._search_local("otp")
    assert any(r["ticker"] == "OTP.BD" for r in local)


def test_search_telekom():
    local = sr._search_local("Telekom")
    assert any(r["ticker"] == "MTELEKOM.BD" for r in local)


def test_search_apple():
    local = sr._search_local("Apple")
    assert any(r["ticker"] == "AAPL" for r in local)


def test_search_bmw():
    local = sr._search_local("BMW")
    assert any(r["ticker"] == "BMW.DE" for r in local)


def test_search_richter():
    local = sr._search_local("richter")
    assert any(r["ticker"] == "RICHTER.BD" for r in local)


def test_search_meta_facebook():
    local = sr._search_local("facebook")
    assert any(r["ticker"] == "META" for r in local)


# ---- Unified search: Yahoo rate limit esetén ne dobjon 500-t ----

def _yahoo_raises(query, **kw):
    raise Exception("Too Many Requests. Rate limited. Try after a while.")


def test_search_otp_no_crash_on_yahoo_error():
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        result = sr.search("OTP")
    assert isinstance(result, dict)
    assert "results" in result
    assert any(r["ticker"] == "OTP.BD" for r in result["results"])


def test_search_apple_no_crash_on_yahoo_error():
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        result = sr.search("Apple")
    assert any(r["ticker"] == "AAPL" for r in result["results"])


def test_search_unknown_returns_empty_not_error():
    with patch("services.symbol_resolver._search_yahoo", return_value=([], [])):
        result = sr.search("XYZXYZXYZ123")
    assert isinstance(result, dict)
    assert "results" in result
    # suffix javaslatok jöhetnek, de nem 500


def test_search_bmw_with_suffix_hint():
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        result = sr.search("BMW")
    tickers = _tickers(result["results"])
    assert "BMW.DE" in tickers


def test_search_errors_field_present():
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        result = sr.search("OTP")
    assert "errors" in result
    assert "timestamp" in result


# ---- Suffix javaslatok ----

def test_suffix_suggestions_short_word():
    suggestions = sr._suffix_suggestions("BMW")
    tickers = [s["ticker"] for s in suggestions]
    assert "BMW.DE" in tickers
    assert "BMW.BD" in tickers


def test_suffix_suggestions_long_word_no_result():
    suggestions = sr._suffix_suggestions("TOOLONG_TICKER")
    assert suggestions == []


def test_suffix_suggestions_with_dot_no_result():
    suggestions = sr._suffix_suggestions("OTP.BD")
    assert suggestions == []


@pytest.mark.parametrize("query", ["Samsung", "Samsung Electronics", "SAMEQ", "SAMEQ.F", "SSU", "SSU.DE", "SSU.F", "SMSN"])
def test_samsung_aliases_surface_usable_frankfurt_eur_gdr(query):
    with patch("services.symbol_resolver._search_yahoo", return_value=([], [])):
        result = sr.search(query)
    samsung = next(item for item in result["results"] if item["ticker"] == "SSU.F")
    assert samsung["name"] == "Samsung Electronics Co. Ltd. GDR"
    assert samsung["exchange"] == "Frankfurt"
    assert samsung["currency"] == "EUR"


def test_smsn_is_not_globally_rewritten_to_samsung_provider_ticker():
    from services.stocks import normalize_ticker

    assert normalize_ticker("SMSN") == "SMSN"
    assert normalize_ticker("SSU.DE") == "SSU.DE"
