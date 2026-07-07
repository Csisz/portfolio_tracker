"""
Részvényárfolyam lekérés – yfinance elsőként, Stooq fallback minden tőzsdére.
"""
import csv
import io
import logging
import math
import numbers
import time
from datetime import datetime, timedelta

import requests
import yfinance as yf

from services import cache

logger = logging.getLogger(__name__)

PRICE_CACHE_TTL = 600   # 10 perc
PRICE_CACHE_PREFIX = "price:"
_HU_TICKER_ALIASES = {
    "OTP": "OTP.BD",
    "MOL": "MOL.BD",
    "RICHTER": "RICHTER.BD",
    "MTELEKOM": "MTELEKOM.BD",
    "4IG": "4IG.BD",
    "OPUS": "OPUS.BD",
    "ANY": "ANY.BD",
}


def normalize_ticker(ticker: str) -> str:
    """Canonical display ticker used for storage, lookup and cache keys."""
    t = str(ticker or "").strip().upper()
    return _HU_TICKER_ALIASES.get(t, t)

# ---------------------------------------------------------------------------
# Stooq ticker mapping – minden tőzsde
# ---------------------------------------------------------------------------

# Végződés → Stooq suffix + deviza
_SUFFIX_MAP = {
    ".BD": (".hu", "HUF"),   # Budapest
    ".DE": (".de", "EUR"),   # Frankfurt/XETRA
    ".L":  (".uk", "GBp"),   # London (pence)
    ".VI": (".at", "EUR"),   # Bécs
    ".PA": (".fr", "EUR"),   # Párizs
    ".AS": (".nl", "EUR"),   # Amszterdam
    ".MI": (".it", "EUR"),   # Milánó
    ".SW": (".ch", "CHF"),   # Zürich
    ".HK": (".hk", "HKD"),   # Hongkong
    ".KS": (".ko", "KRW"),   # Szöul
    ".T":  (".jp", "JPY"),   # Tokió
}


def _to_stooq(ticker: str) -> tuple[str, str] | tuple[None, None]:
    """
    Átalakítja a Yahoo tickert (stooq_symbol, currency) párra.
    US részvénynél (nincs pont) aapl → aapl.us, USD devizával.
    Visszaad (None, None)-t, ha nem ismert a tőzsde.
    """
    t = normalize_ticker(ticker)

    for suffix, (stooq_ext, currency) in _SUFFIX_MAP.items():
        if t.endswith(suffix):
            base = t[: -len(suffix)]
            return f"{base.lower()}{stooq_ext}", currency

    # US részvény: nincs pont a tickerben, és betűk/számok/kötőjel
    if "." not in t:
        clean = t.replace("-", ".").lower()  # BRK-B → brk.b
        return f"{clean}.us", "USD"

    return None, None


def _bd_to_stooq(ticker: str) -> str | None:
    """Backward compat: csak .BD tickereket kezel. Új kód _to_stooq()-t használjon."""
    t = normalize_ticker(ticker)
    if t.endswith(".BD"):
        return t[:-3].lower() + ".hu"
    return None


# ---------------------------------------------------------------------------
# Árfolyam lekérés
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _iso_from_index(idx) -> str | None:
    if idx is None:
        return None
    try:
        if hasattr(idx, "to_pydatetime"):
            dt = idx.to_pydatetime()
        elif hasattr(idx, "isoformat"):
            dt = idx
        else:
            text = str(idx)
            if not text or text.upper() == "N/D":
                return None
            return datetime.fromisoformat(text[:19]).isoformat(timespec="seconds")
        return dt.isoformat(timespec="seconds")
    except Exception:
        text = str(idx or "").strip()
        return text or None


def _valid_price(value) -> float | None:
    if value is None or not isinstance(value, (numbers.Number, str)):
        return None
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    return round(price, 4)


def _fast_info_value(fast_info, *names):
    for name in names:
        try:
            if isinstance(fast_info, dict) and fast_info.get(name) is not None:
                return fast_info.get(name)
            value = getattr(fast_info, name, None)
            if value is not None:
                return value
        except Exception:
            continue
    return None


def _quote(price, currency, source, *, quote_time=None, stale=False, delayed=False, market_state=None, received_at=None):
    return {
        "price": price,
        "currency": str(currency).upper() if currency else None,
        "source": source,
        "quote_time": quote_time,
        "received_at": received_at or _ts(),
        "stale": bool(stale),
        "delayed": bool(delayed),
        "market_state": market_state or "UNKNOWN",
        "timestamp": quote_time,
    }


def _quote_from_history(ticker_obj, source: str, *, period: str, interval: str, delayed: bool):
    hist = ticker_obj.history(period=period, interval=interval, auto_adjust=False, prepost=False)
    if hist is None or getattr(hist, "empty", False) or "Close" not in hist:
        return None
    close_rows = hist["Close"].dropna()
    if close_rows.empty:
        return None
    for idx, close in reversed(list(close_rows.items())):
        price = _valid_price(close)
        if price is None:
            continue
        fast_info = None
        try:
            fast_info = ticker_obj.fast_info
        except Exception:
            fast_info = None
        currency = _fast_info_value(fast_info, "currency")
        market_state = _fast_info_value(fast_info, "market_state", "marketState") or ("CLOSED" if delayed else "UNKNOWN")
        return _quote(
            price,
            currency,
            source,
            quote_time=_iso_from_index(idx),
            delayed=delayed,
            market_state=str(market_state).upper() if market_state else "UNKNOWN",
        )
    return None


def get_last_price(ticker: str, force_refresh: bool = False) -> dict:
    """
    Visszaadja az egységes árfolyamobjektumot.
    stale=True: elavult, cache-ből vagy symbols_cache-ből jön

    Sorrend:
    A) price cache (TTL-n belül, kivéve force_refresh=True)
    B) yfinance intraday 1m
    C) yfinance intraday 5m
    D) yfinance fast_info
    E) yfinance daily close
    F) Stooq fallback (minden ismert tőzsdére)
    G) symbols cache last_price (stale=True)
    """
    clean_ticker = normalize_ticker(ticker)
    key = PRICE_CACHE_PREFIX + clean_ticker

    # A) Memory cache
    cached = None if force_refresh else cache.get(key)
    if cached:
        cached_quote = dict(cached)
        cached_quote["source"] = (cached_quote.get("source") or "Árfolyam") + " / cache"
        cached_quote["stale"] = bool(cached_quote.get("stale", False))
        cached_quote["timestamp"] = cached_quote.get("quote_time")
        return cached_quote

    # B+C) yfinance
    quote = _fetch_price_yfinance(clean_ticker)
    if quote and quote.get("price") is not None:
        cache.set(key, quote, PRICE_CACHE_TTL)
        return quote

    # D) Stooq fallback – minden tőzsdére
    stooq_sym, stooq_currency = _to_stooq(clean_ticker)
    if stooq_sym:
        quote = _fetch_price_stooq(stooq_sym, stooq_currency)
        if quote and quote.get("price") is not None:
            cache.set(key, quote, PRICE_CACHE_TTL)
            return quote

    # E) Stale ár a symbols cache-ből
    stale_price, stale_currency, stale_time = _get_stale_price(clean_ticker)
    if stale_price is not None:
        return _quote(
            stale_price,
            stale_currency,
            "Utolsó ismert árfolyam",
            quote_time=stale_time,
            stale=True,
            delayed=True,
            market_state="UNKNOWN",
            received_at=None,
        )

    return _quote(None, None, "none", stale=False, delayed=False)


def get_historical_price(ticker: str, requested_date: str) -> dict:
    """
    Lekeri a megadott naphoz tartozo vagy az azt megelozo legkozelebbi zaroarat.
    Visszateres JSON-kompatibilis dict, hogy az API kozvetlenul tovabbadhassa.
    """
    clean_ticker = normalize_ticker(ticker)
    if not clean_ticker:
        return {"ok": False, "error": "Ticker is required"}
    try:
        target = datetime.strptime(str(requested_date), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return {"ok": False, "error": "Invalid date"}

    start = target - timedelta(days=14)
    end = target + timedelta(days=1)
    try:
        t = yf.Ticker(clean_ticker)
        hist = t.history(start=start.isoformat(), end=end.isoformat(), interval="1d")
        if hist is None or hist.empty or "Close" not in hist:
            return {"ok": False, "error": "Historical price is not available"}

        close_rows = hist["Close"].dropna()
        if close_rows.empty:
            return {"ok": False, "error": "Historical price is not available"}

        selected_date = None
        selected_price = None
        for idx, close in close_rows.items():
            row_date = idx.date() if hasattr(idx, "date") else datetime.strptime(str(idx)[:10], "%Y-%m-%d").date()
            if row_date <= target:
                selected_date = row_date
                selected_price = float(close)

        if selected_price is None:
            return {"ok": False, "error": "Historical price is not available"}

        currency = None
        try:
            currency = getattr(t.fast_info, "currency", None)
        except Exception:
            pass
        if not currency:
            try:
                currency = (t.info or {}).get("currency")
            except Exception:
                pass

        return {
            "ok": True,
            "ticker": clean_ticker,
            "requested_date": target.isoformat(),
            "used_date": selected_date.isoformat(),
            "price": round(selected_price, 4),
            "currency": str(currency).upper() if currency else None,
            "source": "Yahoo Finance",
        }
    except Exception as exc:
        logger.warning("Historikus arfolyam lekeres hiba (%s, %s): %s", clean_ticker, requested_date, exc)
        return {"ok": False, "error": "Historical price is not available"}


def _fetch_price_yfinance(ticker: str) -> dict | None:
    t = yf.Ticker(ticker)

    for period, interval in [("1d", "1m"), ("5d", "5m")]:
        try:
            quote = _quote_from_history(
                t,
                "Yahoo Finance",
                period=period,
                interval=interval,
                delayed=False,
            )
            if quote:
                logger.info("quote ticker=%s provider=Yahoo Finance ok interval=%s quote_time=%s", ticker, interval, quote.get("quote_time"))
                return quote
        except Exception as exc:
            logger.info("quote ticker=%s provider=Yahoo Finance error interval=%s fallback=%s", ticker, interval, exc)

    # fast_info.last_price
    try:
        fi = t.fast_info
        p = _valid_price(_fast_info_value(fi, "last_price", "lastPrice"))
        c = _fast_info_value(fi, "currency")
        market_state = _fast_info_value(fi, "market_state", "marketState")
        quote_time = _fast_info_value(fi, "last_price_time", "lastPriceTime", "regular_market_time", "regularMarketTime")
        if quote_time and not isinstance(quote_time, str):
            try:
                quote_time = datetime.fromtimestamp(float(quote_time)).astimezone().isoformat(timespec="seconds")
            except Exception:
                quote_time = None
        if p is not None:
            return _quote(
                p,
                c,
                "Yahoo Finance",
                quote_time=quote_time,
                delayed=False,
                market_state=str(market_state).upper() if market_state else "UNKNOWN",
            )
    except Exception:
        pass

    # daily history fallback
    try:
        quote = _quote_from_history(
            t,
            "Yahoo Finance - utolsó záróár",
            period="5d",
            interval="1d",
            delayed=True,
        )
        if quote:
            return quote
    except Exception:
        pass

    return None


def _fetch_price_stooq(stooq_symbol: str, expected_currency: str = None) -> dict | None:
    """Stooq CSV árfolyam lekérés, pl. stooq_symbol='aapl.us'."""
    url = f"https://stooq.com/q/l/?s={stooq_symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "portfolio-tracker/1.0"})
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            close = row.get("Close", "").strip()
            if close and close != "N/D":
                price = _valid_price(close)
                if price is not None:
                    date = (row.get("Date") or "").strip()
                    time_text = (row.get("Time") or "").strip()
                    quote_time = None
                    if date and date.upper() != "N/D" and time_text and time_text.upper() != "N/D":
                        quote_time = f"{date}T{time_text}"
                    return _quote(
                        price,
                        expected_currency,
                        "Stooq - késleltetett",
                        quote_time=quote_time,
                        delayed=True,
                        market_state="CLOSED",
                    )
    except Exception as e:
        logger.warning("Stooq lekérés hiba (%s): %s", stooq_symbol, e)
    return None


def _get_stale_price(ticker: str) -> tuple[float | None, str | None, str | None]:
    """Utolsó ismert ár a symbols_cache-ből (stale fallback)."""
    normalized = normalize_ticker(ticker)
    try:
        from services import db
        symbols = db.search_symbols_db(normalized)
        sym = next(
            (s for s in symbols if normalize_ticker(s.get("ticker", "")) == normalized),
            None,
        )
        if sym and sym.get("last_price"):
            return float(sym["last_price"]), sym.get("last_price_currency") or sym.get("currency"), sym.get("last_price_time")
    except Exception:
        pass

    try:
        from services.symbol_resolver import get_cached_symbols
        symbols = get_cached_symbols()
        sym = next(
            (s for s in symbols if normalize_ticker(s.get("ticker", "")) == normalized),
            None,
        )
        if sym and sym.get("last_price"):
            return float(sym["last_price"]), sym.get("currency"), sym.get("last_price_time")
    except Exception:
        pass
    return None, None, None


# ---------------------------------------------------------------------------
# Batch lekérés
# ---------------------------------------------------------------------------

def get_prices_for_tickers(tickers: list[str], force_refresh: bool = False) -> dict:
    prices = {}
    errors = []
    any_live = False
    any_cached = False

    for ticker in tickers:
        original_ticker = str(ticker or "").strip().upper()
        response_ticker = original_ticker or normalize_ticker(ticker)
        try:
            start = time.perf_counter()
            entry = get_last_price(ticker, force_refresh=force_refresh)
            elapsed_ms = round((time.perf_counter() - start) * 1000)
            if entry.get("price") is not None:
                entry["currency"] = entry.get("currency") or "USD"
                entry["timestamp"] = entry.get("quote_time")
                prices[response_ticker] = entry
                logger.info(
                    "quote ticker=%s provider=%s ok elapsed_ms=%s quote_time=%s",
                    response_ticker, entry.get("source"), elapsed_ms, entry.get("quote_time")
                )

                if entry.get("stale") or "cache" in (entry.get("source") or ""):
                    any_cached = True
                else:
                    any_live = True
            else:
                errors.append({"ticker": response_ticker, "message": "Árfolyam most nem elérhető."})
        except Exception as e:
            msg = str(e)
            if "Too Many Requests" in msg or "429" in msg or "rate limit" in msg.lower():
                msg = "Yahoo Finance rate limit – kérlek várj néhány percet."
            logger.error("Árfolyam lekérés hiba %s: %s", response_ticker, e)
            errors.append({"ticker": response_ticker, "message": msg})

    if any_live and any_cached:
        overall_source = "Yahoo Finance/részleges cache"
    elif any_cached:
        overall_source = "Yahoo Finance/cache"
    elif any_live:
        overall_source = "Yahoo Finance"
    else:
        overall_source = "none"

    received_at = _ts()
    return {
        "prices": prices,
        "errors": errors,
        "received_at": received_at,
        "source": overall_source,
    }


def get_ticker_info(ticker: str) -> dict | None:
    """Lekéri egy ticker alapadatait validáláshoz (kézi hozzáadásnál)."""
    try:
        ticker = normalize_ticker(ticker)
        t = yf.Ticker(ticker)
        fi = t.fast_info
        price = getattr(fi, "last_price", None)
        currency = getattr(fi, "currency", None)
        exchange = getattr(fi, "exchange", None)

        info = {}
        try:
            full = t.info or {}
            info = {
                "name": full.get("longName") or full.get("shortName") or ticker,
                "currency": (full.get("currency") or currency or "").upper() or None,
                "exchange": full.get("exchange") or exchange or "",
            }
        except Exception:
            info = {
                "name": ticker,
                "currency": str(currency).upper() if currency else None,
                "exchange": str(exchange) if exchange else "",
            }

        if price and float(price) > 0:
            info["last_price"] = round(float(price), 4)
            return info
    except Exception as e:
        logger.warning("Ticker info lekérés hiba %s: %s", ticker, e)
    return None
