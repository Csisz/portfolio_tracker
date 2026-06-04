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
