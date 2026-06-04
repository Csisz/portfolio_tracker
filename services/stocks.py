"""
Részvényárfolyam lekérés – yfinance elsőként, Stooq fallback minden tőzsdére.
"""
import csv
import io
import logging
from datetime import datetime

import requests
import yfinance as yf

from services import cache

logger = logging.getLogger(__name__)

PRICE_CACHE_TTL = 600   # 10 perc
PRICE_CACHE_PREFIX = "price:"

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
    t = ticker.strip().upper()

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
    t = ticker.strip().upper()
    if t.endswith(".BD"):
        return t[:-3].lower() + ".hu"
    return None


# ---------------------------------------------------------------------------
# Árfolyam lekérés
# ---------------------------------------------------------------------------

def get_last_price(ticker: str) -> tuple[float | None, str | None, str, bool]:
    """
    Visszaadja: (ár, deviza, forrás, stale)
    stale=True: elavult, cache-ből vagy symbols_cache-ből jön

    Sorrend:
    A) price cache (TTL-n belül)
    B) yfinance fast_info
    C) yfinance history(5d)
    D) Stooq fallback (minden ismert tőzsdére)
    E) symbols cache last_price (stale=True)
    """
    key = PRICE_CACHE_PREFIX + ticker.upper()

    # A) Memory cache
    cached = cache.get(key)
    if cached:
        return cached["price"], cached["currency"], "Yahoo Finance/cache", False

    # B+C) yfinance
    price, currency = _fetch_price_yfinance(ticker)
    if price is not None:
        cache.set(key, {"price": price, "currency": currency}, PRICE_CACHE_TTL)
        return price, currency, "Yahoo Finance", False

    # D) Stooq fallback – minden tőzsdére
    stooq_sym, stooq_currency = _to_stooq(ticker)
    if stooq_sym:
        price, currency = _fetch_price_stooq(stooq_sym, stooq_currency)
        if price is not None:
            cache.set(key, {"price": price, "currency": currency}, PRICE_CACHE_TTL)
            return price, currency, "Stooq", False

    # E) Stale ár a symbols cache-ből
    stale_price, stale_currency = _get_stale_price(ticker)
    if stale_price is not None:
        return stale_price, stale_currency, "stale", True

    return None, None, "none", False


def _fetch_price_yfinance(ticker: str) -> tuple[float | None, str | None]:
    t = yf.Ticker(ticker)

    # fast_info.last_price
    try:
        fi = t.fast_info
        p = fi.last_price
        c = getattr(fi, "currency", None)
        if p and float(p) > 0:
            return round(float(p), 4), str(c).upper() if c else None
    except Exception:
        pass

    # history fallback
    try:
        hist = t.history(period="5d")
        if not hist.empty:
            p = float(hist["Close"].iloc[-1])
            c = getattr(t.fast_info, "currency", None)
            if p > 0:
                return round(p, 4), str(c).upper() if c else None
    except Exception:
        pass

    return None, None


def _fetch_price_stooq(stooq_symbol: str, expected_currency: str = None) -> tuple[float | None, str | None]:
    """Stooq CSV árfolyam lekérés, pl. stooq_symbol='aapl.us'."""
    url = f"https://stooq.com/q/l/?s={stooq_symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "portfolio-tracker/1.0"})
        resp.raise_for_status()
        reader = csv.DictReader(io.StringIO(resp.text))
        for row in reader:
            close = row.get("Close", "").strip()
            if close and close != "N/D":
                price = float(close)
                if price > 0:
                    return round(price, 4), expected_currency
    except Exception as e:
        logger.warning("Stooq lekérés hiba (%s): %s", stooq_symbol, e)
    return None, None


def _get_stale_price(ticker: str) -> tuple[float | None, str | None]:
    """Utolsó ismert ár a symbols_cache-ből (stale fallback)."""
    try:
        from services.symbol_resolver import get_cached_symbols
        symbols = get_cached_symbols()
        sym = next(
            (s for s in symbols if s.get("ticker", "").upper() == ticker.upper()),
            None,
        )
        if sym and sym.get("last_price"):
            return float(sym["last_price"]), sym.get("currency")
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------------------
# Batch lekérés
# ---------------------------------------------------------------------------

def get_prices_for_tickers(tickers: list[str]) -> dict:
    prices = {}
    errors = []
    any_live = False
    any_cached = False

    for ticker in tickers:
        try:
            price, currency, source, stale = get_last_price(ticker)
            if price is not None:
                entry = {
                    "price": price,
                    "currency": currency or "USD",
                    "source": source,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                if stale:
                    entry["stale"] = True
                prices[ticker] = entry

                if stale or "cache" in source:
                    any_cached = True
                else:
                    any_live = True
            else:
                errors.append({"ticker": ticker, "message": "Árfolyam most nem elérhető."})
        except Exception as e:
            msg = str(e)
            if "Too Many Requests" in msg or "429" in msg or "rate limit" in msg.lower():
                msg = "Yahoo Finance rate limit – kérlek várj néhány percet."
            logger.error("Árfolyam lekérés hiba %s: %s", ticker, e)
            errors.append({"ticker": ticker, "message": msg})

    if any_live and any_cached:
        overall_source = "Yahoo Finance/részleges cache"
    elif any_cached:
        overall_source = "Yahoo Finance/cache"
    elif any_live:
        overall_source = "Yahoo Finance"
    else:
        overall_source = "none"

    return {
        "prices": prices,
        "errors": errors,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "source": overall_source,
    }


def get_ticker_info(ticker: str) -> dict | None:
    """Lekéri egy ticker alapadatait validáláshoz (kézi hozzáadásnál)."""
    try:
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
