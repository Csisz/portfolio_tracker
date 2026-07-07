"""
Devizaarfolyam provider rendszer.

Az MNB hivatalos napi arfolyamforraskent megmarad, a portfolio aktualis
ertekelesehez pedig Yahoo Finance / Stooq piaci FX arfolyamot is tudunk
hasznalni.
"""
import csv
import html
import io
import json
import logging
import os
import xml.etree.ElementTree as ET
from datetime import datetime
from itertools import product

import requests

from services import cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# MNB SOAP konstansok
# ---------------------------------------------------------------------------

MNB_URL = "https://www.mnb.hu/arfolyamok.asmx"
MNB_SOAP_ACTION = "http://www.mnb.hu/webservices/MNBArfolyamServiceSoap/GetCurrentExchangeRates"

_MNB_ENDPOINTS = [
    "https://www.mnb.hu/arfolyamok.asmx",
    "http://www.mnb.hu/arfolyamok.asmx",
]
_MNB_SOAP_ACTIONS = [
    "http://www.mnb.hu/webservices/MNBArfolyamServiceSoap/GetCurrentExchangeRates",
    "http://www.mnb.hu/webservices/GetCurrentExchangeRates",
]

MNB_SOAP_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    ' xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soap:Body>"
    '<GetCurrentExchangeRates xmlns="http://www.mnb.hu/webservices/" />'
    "</soap:Body>"
    "</soap:Envelope>"
)

FX_CACHE_KEY_MARKET = "fx_rates:market"
FX_CACHE_KEY_OFFICIAL = "fx_rates:official"
FX_MARKET_CACHE_TTL = 300
FX_OFFICIAL_CACHE_TTL = 24 * 3600
FX_FILE_CACHE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fx_cache.json")

SUPPORTED_CURRENCIES = [
    "USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD",
    "PLN", "CZK", "SEK", "NOK", "DKK",
]

YAHOO_FX_TICKERS = {
    "EUR": "EURHUF=X",
    "USD": "USDHUF=X",
    "GBP": "GBPHUF=X",
    "CHF": "CHFHUF=X",
    "JPY": "JPYHUF=X",
}

STOOQ_FX_SYMBOLS = {
    "EUR": "eurhuf",
    "USD": "usdhuf",
    "GBP": "gbphuf",
    "CHF": "chfhuf",
    "JPY": "jpyhuf",
}


# ---------------------------------------------------------------------------
# SOAP + XML parserek
# ---------------------------------------------------------------------------

def parse_mnb_soap_response(soap_text: str) -> dict:
    try:
        root = ET.fromstring(soap_text)
    except ET.ParseError as e:
        logger.error("SOAP boritek parse hiba: %s", e)
        return {}

    result_text = None
    for elem in root.iter():
        if elem.tag.endswith("GetCurrentExchangeRatesResult"):
            result_text = elem.text
            break

    if not result_text:
        logger.error("GetCurrentExchangeRatesResult nem talalhato a SOAP valaszban")
        return {}

    inner_xml = html.unescape(result_text)
    return parse_mnb_current_fx_xml(inner_xml)


def parse_mnb_current_fx_xml(xml_text: str) -> dict:
    rates = {}
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter():
            if not elem.tag.endswith("Rate"):
                continue
            curr = elem.attrib.get("curr", "").strip().upper()
            unit_str = elem.attrib.get("unit", "1").strip()
            text = (elem.text or "").strip().replace(",", ".")
            if not curr or not text:
                continue
            try:
                value = float(text)
                unit = float(unit_str) if unit_str else 1.0
                rates[curr] = round(value / unit, 6)
            except ValueError:
                continue
    except ET.ParseError as e:
        logger.error("MNB belso XML parse hiba: %s", e)
    return rates


def _parse_mnb_rate_date_from_soap(soap_text: str) -> str | None:
    try:
        root = ET.fromstring(soap_text)
    except ET.ParseError:
        return None
    for elem in root.iter():
        if elem.tag.endswith("GetCurrentExchangeRatesResult") and elem.text:
            return _parse_mnb_rate_date(html.unescape(elem.text))
    return None


def _parse_mnb_rate_date(xml_text: str) -> str | None:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None
    for elem in root.iter():
        if elem.tag.endswith("Day"):
            return elem.attrib.get("date")
    return None


# ---------------------------------------------------------------------------
# Provider lekeresek
# ---------------------------------------------------------------------------

def get_mnb_current_fx() -> tuple[dict, list]:
    attempt_errors = []

    for endpoint, soap_action in product(_MNB_ENDPOINTS, _MNB_SOAP_ACTIONS):
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
            "User-Agent": "portfolio-tracker/1.0",
        }
        try:
            resp = requests.post(
                endpoint,
                data=MNB_SOAP_BODY.encode("utf-8"),
                headers=headers,
                timeout=15,
            )
            if resp.status_code != 200:
                msg = f"endpoint={endpoint} soapAction={soap_action} status={resp.status_code}"
                logger.info("MNB probalkozas sikertelen: %s", msg)
                attempt_errors.append(f"HTTP {resp.status_code} ({endpoint})")
                continue

            rates = parse_mnb_soap_response(resp.text)
            if rates:
                logger.info("MNB sikeres: endpoint=%s action=%s", endpoint, soap_action)
                return rates, []

            attempt_errors.append(f"ures valasz ({endpoint})")

        except requests.exceptions.Timeout:
            msg = f"Timeout: {endpoint}"
            logger.info("MNB probalkozas sikertelen: %s", msg)
            attempt_errors.append(msg)
        except requests.exceptions.ConnectionError:
            msg = f"Kapcsolati hiba: {endpoint}"
            logger.info("MNB probalkozas sikertelen: %s", msg)
            attempt_errors.append(msg)
        except Exception as e:
            msg = f"{endpoint}: {e}"
            logger.warning("MNB lekeres hiba: %s", msg)
            attempt_errors.append(str(e))

    summary = "MNB nem elerheto. Probalkozasok: " + "; ".join(attempt_errors)
    logger.error(summary)
    return {}, [summary]


def get_official_mnb_fx() -> dict:
    cached = cache.get(FX_CACHE_KEY_OFFICIAL)
    if cached:
        return _clean_cached_result(cached, "MNB/cache")

    if not _provider_enabled("enable_mnb", True):
        return _empty_result("official", "MNB", ["MNB devizaarfolyam kikapcsolva."])

    rate_date = None
    rates, errors = _get_mnb_current_fx_with_date()
    if rates:
        rate_date = rates.pop("_rate_date", None)
        result = _provider_result(
            mode="official",
            rates=rates,
            source="MNB",
            timestamp=_ts(),
            rate_date=rate_date,
            errors=errors,
        )
        _store_fx_result(result, FX_CACHE_KEY_OFFICIAL, _official_ttl(), "official")
        return _clean_result(result)

    db_cached = _load_db_cache("official", errors)
    if db_cached:
        return db_cached

    file_cached = _load_file_cache("official")
    if file_cached and file_cached.get("fx"):
        file_cached["source"] = "MNB/cache"
        file_cached["errors"] = errors + ["Az utolso ismert hivatalos arfolyamot hasznaljuk."]
        return file_cached

    return _empty_result("official", "none", errors or ["MNB arfolyam nem elerheto."])


def _get_mnb_current_fx_with_date() -> tuple[dict, list]:
    attempt_errors = []
    for endpoint, soap_action in product(_MNB_ENDPOINTS, _MNB_SOAP_ACTIONS):
        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
            "User-Agent": "portfolio-tracker/1.0",
        }
        try:
            resp = requests.post(endpoint, data=MNB_SOAP_BODY.encode("utf-8"), headers=headers, timeout=15)
            if resp.status_code != 200:
                attempt_errors.append(f"HTTP {resp.status_code} ({endpoint})")
                continue
            rates = parse_mnb_soap_response(resp.text)
            if rates:
                rate_date = _parse_mnb_rate_date_from_soap(resp.text)
                if rate_date:
                    rates["_rate_date"] = rate_date
                return rates, []
            attempt_errors.append(f"ures valasz ({endpoint})")
        except requests.exceptions.Timeout:
            attempt_errors.append(f"Timeout: {endpoint}")
        except requests.exceptions.ConnectionError:
            attempt_errors.append(f"Kapcsolati hiba: {endpoint}")
        except Exception as e:
            attempt_errors.append(str(e))
    return {}, ["MNB nem elerheto. Probalkozasok: " + "; ".join(attempt_errors)]


def get_market_fx() -> dict:
    cached = cache.get(FX_CACHE_KEY_MARKET)
    if cached:
        return _clean_cached_result(cached, cached.get("source", "market/cache"))

    errors = []
    yahoo = get_yahoo_fx()
    errors.extend(yahoo.get("errors", []))
    rates = dict(yahoo.get("rates", {}))
    source_parts = []
    quote_times = []
    any_delayed = bool(yahoo.get("delayed"))
    if yahoo.get("quote_time"):
        quote_times.append(yahoo["quote_time"])
    if rates:
        source_parts.append("Yahoo Finance")

    missing = [c for c in STOOQ_FX_SYMBOLS if c not in rates]
    if missing:
        stooq = get_stooq_fx(missing)
        errors.extend(stooq.get("errors", []))
        if stooq.get("rates"):
            rates.update(stooq["rates"])
            source_parts.append("Stooq")
            any_delayed = any_delayed or bool(stooq.get("delayed"))
            if stooq.get("quote_time"):
                quote_times.append(stooq["quote_time"])

    if rates:
        result = _provider_result(
            mode="market",
            rates=rates,
            source=" / ".join(source_parts) + " FX",
            timestamp=_ts(),
            errors=errors,
            quote_time=max(quote_times) if quote_times else None,
            delayed=any_delayed,
            stale=False,
        )
        _store_fx_result(result, FX_CACHE_KEY_MARKET, FX_MARKET_CACHE_TTL, "market")
        return _clean_result(result)

    db_cached = _load_db_cache("market", errors)
    if db_cached:
        return db_cached

    file_cached = _load_file_cache("market")
    if file_cached and file_cached.get("fx"):
        file_cached["source"] = "market/cache"
        file_cached["errors"] = errors + ["Az utolso ismert piaci arfolyamot hasznaljuk."]
        return file_cached

    return _empty_result("market", "none", errors or ["Piaci devizaarfolyam nem elerheto."])


def get_yahoo_fx() -> dict:
    if not _provider_enabled("enable_yahoo", True):
        return {"rates": {}, "errors": ["Yahoo Finance provider kikapcsolva."], "source": "Yahoo Finance"}

    try:
        import yfinance as yf
    except Exception as e:
        return {"rates": {}, "errors": [f"yfinance import hiba: {e}"], "source": "Yahoo Finance"}

    rates = {}
    errors = []
    quote_times = []
    any_delayed = False
    for currency, ticker in YAHOO_FX_TICKERS.items():
        try:
            data = _yahoo_last_price(yf, ticker)
            value = data.get("price") if data else None
            if value and value > 0:
                rates[currency] = round(float(value), 6)
                if data.get("quote_time"):
                    quote_times.append(data["quote_time"])
                any_delayed = any_delayed or bool(data.get("delayed"))
            else:
                errors.append(f"Yahoo {ticker}: nincs arfolyam")
        except Exception as e:
            errors.append(f"Yahoo {ticker}: {e}")
    return {
        "rates": rates,
        "errors": errors,
        "source": "Yahoo Finance",
        "quote_time": max(quote_times) if quote_times else None,
        "delayed": any_delayed,
        "stale": False,
        "received_at": _ts(),
    }


def _idx_iso(idx) -> str | None:
    try:
        if hasattr(idx, "to_pydatetime"):
            return idx.to_pydatetime().isoformat(timespec="seconds")
        if hasattr(idx, "isoformat"):
            return idx.isoformat(timespec="seconds")
        return str(idx).replace(" ", "T")
    except Exception:
        return None


def _yahoo_history_quote(obj, *, period: str, interval: str, delayed: bool) -> dict | None:
    hist = obj.history(period=period, interval=interval, auto_adjust=False, prepost=False)
    if hist is None or getattr(hist, "empty", False) or "Close" not in hist:
        return None
    close = hist["Close"].dropna()
    if close.empty:
        return None
    idx = close.index[-1]
    value = float(close.iloc[-1])
    if value <= 0:
        return None
    return {"price": value, "quote_time": _idx_iso(idx), "delayed": delayed}


def _yahoo_last_price(yf, ticker: str) -> dict | None:
    obj = yf.Ticker(ticker)
    for period, interval in [("1d", "1m"), ("5d", "5m")]:
        try:
            quote = _yahoo_history_quote(obj, period=period, interval=interval, delayed=False)
            if quote:
                return quote
        except Exception:
            pass
    try:
        fast_info = getattr(obj, "fast_info", None)
        value = None
        if fast_info:
            if isinstance(fast_info, dict):
                value = fast_info.get("last_price") or fast_info.get("lastPrice")
            else:
                value = getattr(fast_info, "last_price", None) or getattr(fast_info, "lastPrice", None)
        if value:
            return {"price": float(value), "quote_time": None, "delayed": False}
    except Exception:
        pass

    return _yahoo_history_quote(obj, period="5d", interval="1d", delayed=True)


def get_stooq_fx(currencies: list[str] | None = None) -> dict:
    if not _provider_enabled("enable_stooq", True):
        return {"rates": {}, "errors": ["Stooq provider kikapcsolva."], "source": "Stooq FX"}

    currencies = currencies or list(STOOQ_FX_SYMBOLS.keys())
    rates = {}
    errors = []
    quote_times = []
    for currency in currencies:
        symbol = STOOQ_FX_SYMBOLS.get(currency)
        if not symbol:
            continue
        try:
            data = _fetch_stooq_close(symbol)
            value = data.get("price") if data else None
            if value and value > 0:
                rates[currency] = round(float(value), 6)
                if data.get("quote_time"):
                    quote_times.append(data["quote_time"])
            else:
                errors.append(f"Stooq {symbol}: nincs arfolyam")
        except Exception as e:
            errors.append(f"Stooq {symbol}: {e}")
    return {"rates": rates, "errors": errors, "source": "Stooq FX", "quote_time": max(quote_times) if quote_times else None, "delayed": True, "stale": False, "received_at": _ts()}


def _fetch_stooq_close(symbol: str) -> dict | None:
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    resp = requests.get(url, timeout=10, headers={"User-Agent": "portfolio-tracker/1.0"})
    resp.raise_for_status()
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        close = (row.get("Close") or "").strip()
        if close and close.upper() != "N/D":
            date = (row.get("Date") or "").strip()
            time_text = (row.get("Time") or "").strip()
            quote_time = f"{date}T{time_text}" if date.upper() != "N/D" and time_text.upper() != "N/D" else None
            return {"price": float(close), "quote_time": quote_time, "delayed": True}
    return None


# ---------------------------------------------------------------------------
# Fo API
# ---------------------------------------------------------------------------

def get_fx_rates(mode: str = "market") -> dict:
    requested_mode = _normalize_mode(mode)
    official = get_official_mnb_fx() if requested_mode in ("official", "market", "auto") else _empty_result("official", "none", [])

    if requested_mode == "official":
        return _compose_response("official", "official", official, None, official, official.get("errors", []))

    market = get_market_fx()
    errors = []
    errors.extend(market.get("errors", []))
    if official.get("errors"):
        errors.extend(official["errors"])

    if market.get("fx"):
        return _compose_response(requested_mode, "market", market, market, official, errors)

    if requested_mode == "auto" and official.get("fx"):
        errors.append("A piaci devizaárfolyam jelenleg nem érhető el. A HUF-átváltáshoz az MNB legutóbbi hivatalos napi árfolyamát használjuk.")
        return _compose_response("auto", "official", official, market, official, errors)

    if requested_mode == "market" and official.get("fx"):
        errors.append("A piaci devizaárfolyam jelenleg nem érhető el. A HUF-átváltáshoz az MNB legutóbbi hivatalos napi árfolyamát használjuk.")
        return _compose_response("market", "official", official, market, official, errors)

    db_cached = _load_db_cache("official", errors) or _load_db_cache("market", errors)
    if db_cached:
        errors.append("Az utolso ismert adatbazis-cache arfolyamot hasznaljuk.")
        return _compose_response(requested_mode, "cache", db_cached, market, official, errors)

    return _compose_response(requested_mode, "none", _empty_result("none", "none", errors), market, official, errors)


def get_official_mnb_fx_legacy() -> dict:
    return get_official_mnb_fx()


def _compose_response(requested_mode: str, used_mode: str, selected: dict, market: dict | None,
                      official: dict | None, errors: list) -> dict:
    clean_selected = _clean_result(selected or {})
    result = {
        "mode": used_mode,
        "requested_mode": requested_mode,
        "fx": clean_selected.get("fx", {}),
        "market": _section(market),
        "official": _section(official),
        "errors": _dedupe_errors(errors),
        "source": clean_selected.get("source", "none"),
        "timestamp": clean_selected.get("timestamp", _ts()),
        "quote_time": clean_selected.get("quote_time"),
        "received_at": clean_selected.get("received_at") or clean_selected.get("timestamp", _ts()),
        "stale": bool(clean_selected.get("stale", False)),
        "delayed": bool(clean_selected.get("delayed", False)),
    }
    if clean_selected.get("date"):
        result["date"] = clean_selected["date"]
    return result


def _section(result: dict | None) -> dict:
    if not result:
        return {}
    section = dict(result.get("fx") or {})
    section.update({
        "source": result.get("source", ""),
        "timestamp": result.get("timestamp", ""),
        "quote_time": result.get("quote_time", ""),
        "received_at": result.get("received_at", ""),
        "stale": bool(result.get("stale", False)),
        "delayed": bool(result.get("delayed", False)),
    })
    if result.get("date"):
        section["date"] = result["date"]
    if result.get("rate_date"):
        section["rate_date"] = result["rate_date"]
    return section


# ---------------------------------------------------------------------------
# Cache kezeles
# ---------------------------------------------------------------------------

def _load_file_cache(mode: str | None = None) -> dict | None:
    try:
        if os.path.exists(FX_FILE_CACHE):
            with open(FX_FILE_CACHE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if mode and isinstance(data.get("_modes"), dict) and data["_modes"].get(mode):
                return data["_modes"][mode]
            if data.get("fx"):
                return data
    except Exception:
        pass
    return None


def _save_file_cache(data: dict, mode: str):
    tmp = FX_FILE_CACHE + ".tmp"
    try:
        clean = _clean_result(data)
        existing = _load_raw_file_cache()
        modes = existing.get("_modes", {}) if isinstance(existing, dict) else {}
        modes[mode] = clean
        clean["_modes"] = modes
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FX_FILE_CACHE)
    except Exception as e:
        logger.warning("FX fajl cache mentesi hiba: %s", e)


def _load_raw_file_cache() -> dict:
    try:
        if os.path.exists(FX_FILE_CACHE):
            with open(FX_FILE_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _store_fx_result(result: dict, memory_key: str, ttl: int, mode: str):
    cache.set(memory_key, result, ttl)
    _save_file_cache(result, mode)
    try:
        from services.db import save_fx_rate_cache
        save_fx_rate_cache(
            mode=mode,
            fx=result.get("fx", {}),
            source=result.get("source", ""),
            fetched_at=result.get("timestamp", _ts()),
            rate_date=result.get("rate_date") or result.get("date"),
        )
    except Exception as e:
        logger.warning("FX DB cache mentesi hiba: %s", e)


def _load_db_cache(mode: str, errors: list | None = None) -> dict | None:
    try:
        from services.db import get_latest_fx_rate_cache
        cached = get_latest_fx_rate_cache(mode)
    except Exception:
        return None
    if not cached or not cached.get("fx"):
        return None
    cached_errors = list(errors or [])
    cached_errors.append("Az utolso ismert adatbazis-cache arfolyamot hasznaljuk.")
    return {
        "mode": mode,
        "fx": cached["fx"],
        "errors": cached_errors,
        "source": f"{cached.get('source') or 'cache'}/cache",
        "timestamp": cached.get("fetched_at") or _ts(),
        "received_at": cached.get("fetched_at") or _ts(),
        "quote_time": cached.get("rate_date") or cached.get("fetched_at"),
        "stale": True,
        "delayed": True,
        "date": cached.get("rate_date"),
        "rate_date": cached.get("rate_date"),
    }


# ---------------------------------------------------------------------------
# Segedfuggvenyek
# ---------------------------------------------------------------------------

def _provider_result(mode: str, rates: dict, source: str, timestamp: str, errors: list | None = None,
                     rate_date: str | None = None, quote_time: str | None = None,
                     delayed: bool = True, stale: bool = False) -> dict:
    qt = quote_time or (rate_date + "T00:00:00" if rate_date else timestamp)
    result = {
        "mode": mode,
        "fx": _build_fx_dict(rates),
        "errors": errors or [],
        "source": source,
        "timestamp": timestamp,
        "quote_time": qt,
        "received_at": timestamp,
        "stale": bool(stale),
        "delayed": bool(delayed),
        "date": rate_date or timestamp[:10],
        "rate_date": rate_date,
        "_raw": rates,
    }
    return result


def _empty_result(mode: str, source: str, errors: list) -> dict:
    return {
        "mode": mode,
        "fx": {},
        "errors": errors,
        "source": source,
        "timestamp": _ts(),
        "quote_time": None,
        "received_at": _ts(),
        "stale": False,
        "delayed": False,
        "date": datetime.now().date().isoformat(),
    }


def _clean_result(result: dict) -> dict:
    clean = dict(result or {})
    clean.pop("_raw", None)
    return clean


def _clean_cached_result(result: dict, source: str) -> dict:
    clean = _clean_result(result)
    clean["source"] = source
    return clean


def _normalize_mode(mode: str) -> str:
    mode = (mode or "market").strip().lower()
    return mode if mode in ("market", "official", "auto") else "market"


def _provider_enabled(key: str, default: bool) -> bool:
    try:
        from services.settings_store import get_bool
        return get_bool(key)
    except Exception:
        return default


def _official_ttl() -> int:
    try:
        from services.settings_store import get_int
        hours = get_int("fx_cache_hours") or 24
        return max(12, hours) * 3600
    except Exception:
        return FX_OFFICIAL_CACHE_TTL


def _dedupe_errors(errors: list) -> list:
    result = []
    seen = set()
    for err in errors or []:
        if not err:
            continue
        key = str(err)
        if key not in seen:
            seen.add(key)
            result.append(err)
    return result


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _build_fx_dict(rates: dict) -> dict:
    fx = {}
    for curr in SUPPORTED_CURRENCIES:
        if curr in rates:
            fx[f"{curr}/HUF"] = round(rates[curr], 4)

    if "EUR" in rates and rates["EUR"] > 0:
        fx["HUF/EUR"] = round(1 / rates["EUR"], 8)
    if "USD" in rates and rates["USD"] > 0:
        fx["HUF/USD"] = round(1 / rates["USD"], 8)

    if "EUR" in rates and "USD" in rates and rates["EUR"] > 0:
        fx["USD/EUR"] = round(rates["USD"] / rates["EUR"], 6)
        fx["EUR/USD"] = round(rates["EUR"] / rates["USD"], 6)
    if "EUR" in rates and "GBP" in rates and rates["EUR"] > 0:
        fx["GBP/EUR"] = round(rates["GBP"] / rates["EUR"], 6)

    return fx


def currency_to_huf_rate(currency: str, fx: dict) -> float | None:
    if not currency:
        return None
    raw = currency.strip()
    c = raw.upper()
    if c == "HUF":
        return 1.0
    if raw in ("GBp", "GBX") or c == "GBX":
        gbp_huf = fx.get("GBP/HUF")
        return round(gbp_huf / 100, 6) if gbp_huf else None
    return fx.get(f"{c}/HUF")
