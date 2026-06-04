"""
Ticker keresés – háromrétegű logika:
1. Symbols cache (korábban használt tickerek)
2. Helyi fallback lista
3. Yahoo Finance külső keresés
"""
import json
import logging
import os
from datetime import datetime

import yfinance as yf

from services import cache

logger = logging.getLogger(__name__)

SYMBOLS_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "symbols_cache.json")
SEARCH_CACHE_TTL = 3600  # 1 óra

# ---------------------------------------------------------------------------
# HELYI FALLBACK LISTA
# ---------------------------------------------------------------------------
LOCAL_FALLBACK: list[dict] = [
    # Magyar részvények (BÉT)
    {"ticker": "OTP.BD",      "name": "OTP Bank",              "currency": "HUF", "exchange": "BUD", "aliases": ["otp", "otp bank", "otpb"]},
    {"ticker": "MOL.BD",      "name": "MOL Magyar Olaj- és Gázipari Nyrt.", "currency": "HUF", "exchange": "BUD", "aliases": ["mol"]},
    {"ticker": "RICHTER.BD",  "name": "Richter Gedeon Nyrt.",  "currency": "HUF", "exchange": "BUD", "aliases": ["richter", "gedeon"]},
    {"ticker": "MTELEKOM.BD", "name": "Magyar Telekom Nyrt.",  "currency": "HUF", "exchange": "BUD", "aliases": ["telekom", "magyar telekom", "mtelekom", "t-mobile hu"]},
    {"ticker": "4iG.BD",      "name": "4iG Nyrt.",             "currency": "HUF", "exchange": "BUD", "aliases": ["4ig", "4ig nyrt"]},
    {"ticker": "OPUS.BD",     "name": "Opus Global Nyrt.",     "currency": "HUF", "exchange": "BUD", "aliases": ["opus", "opus global"]},
    {"ticker": "ANY.BD",      "name": "ANY Biztonsági Nyomda", "currency": "HUF", "exchange": "BUD", "aliases": ["any", "biztonsagi nyomda"]},
    # Amerikai részvények
    {"ticker": "AAPL",  "name": "Apple Inc.",             "currency": "USD", "exchange": "NASDAQ", "aliases": ["apple", "apple inc"]},
    {"ticker": "MSFT",  "name": "Microsoft Corporation",  "currency": "USD", "exchange": "NASDAQ", "aliases": ["microsoft", "ms"]},
    {"ticker": "TSLA",  "name": "Tesla Inc.",             "currency": "USD", "exchange": "NASDAQ", "aliases": ["tesla"]},
    {"ticker": "NVDA",  "name": "NVIDIA Corporation",     "currency": "USD", "exchange": "NASDAQ", "aliases": ["nvidia", "nvda"]},
    {"ticker": "AMZN",  "name": "Amazon.com Inc.",        "currency": "USD", "exchange": "NASDAQ", "aliases": ["amazon"]},
    {"ticker": "GOOGL", "name": "Alphabet Inc.",          "currency": "USD", "exchange": "NASDAQ", "aliases": ["google", "alphabet", "googl"]},
    {"ticker": "META",  "name": "Meta Platforms Inc.",    "currency": "USD", "exchange": "NASDAQ", "aliases": ["meta", "facebook", "fb"]},
    {"ticker": "AMGN",  "name": "Amgen Inc.",             "currency": "USD", "exchange": "NASDAQ", "aliases": ["amgen"]},
    {"ticker": "JPM",   "name": "JPMorgan Chase & Co.",   "currency": "USD", "exchange": "NYSE",   "aliases": ["jpmorgan", "jp morgan", "jpm"]},
    {"ticker": "V",     "name": "Visa Inc.",              "currency": "USD", "exchange": "NYSE",   "aliases": ["visa"]},
    {"ticker": "BRK-B", "name": "Berkshire Hathaway",     "currency": "USD", "exchange": "NYSE",   "aliases": ["berkshire", "buffett"]},
    # Európai részvények
    {"ticker": "BMW.DE",  "name": "Bayerische Motoren Werke AG", "currency": "EUR", "exchange": "XETRA", "aliases": ["bmw", "bayerische motoren"]},
    {"ticker": "VOW3.DE", "name": "Volkswagen AG",               "currency": "EUR", "exchange": "XETRA", "aliases": ["volkswagen", "vw", "vow"]},
    {"ticker": "SAP.DE",  "name": "SAP SE",                      "currency": "EUR", "exchange": "XETRA", "aliases": ["sap"]},
    {"ticker": "SIE.DE",  "name": "Siemens AG",                  "currency": "EUR", "exchange": "XETRA", "aliases": ["siemens"]},
    {"ticker": "SHEL.L",  "name": "Shell plc",                   "currency": "GBp", "exchange": "LSE",   "aliases": ["shell", "shel", "royal dutch"]},
    {"ticker": "HSBA.L",  "name": "HSBC Holdings plc",           "currency": "GBp", "exchange": "LSE",   "aliases": ["hsbc"]},
    {"ticker": "AZN.L",   "name": "AstraZeneca PLC",             "currency": "GBp", "exchange": "LSE",   "aliases": ["astrazeneca", "azn"]},
    {"ticker": "OMV.VI",  "name": "OMV AG",                      "currency": "EUR", "exchange": "WBAG",  "aliases": ["omv"]},
    # Egyéb
    {"ticker": "005930.KS", "name": "Samsung Electronics",       "currency": "KRW", "exchange": "KRX",   "aliases": ["samsung"]},
]

# Suffix javaslatok tőzsde alapján
SUFFIX_HINTS = {
    "BD":  "Budapesti Értéktőzsde (BÉT)",
    "DE":  "Frankfurt (XETRA)",
    "L":   "London (LSE)",
    "VI":  "Bécs (WBAG)",
    "PA":  "Párizs (Euronext)",
    "AS":  "Amszterdam (Euronext)",
    "MI":  "Milánó (Borsa Italiana)",
    "SW":  "Zürich (SIX)",
    "KS":  "Szöul (KRX)",
    "T":   "Tokió (TSE)",
    "HK":  "Hongkong (HKEX)",
}


# ---------------------------------------------------------------------------
# SYMBOLS CACHE (fájl alapú, tartós)
# ---------------------------------------------------------------------------

def _load_symbols_cache() -> list[dict]:
    try:
        if os.path.exists(SYMBOLS_FILE):
            with open(SYMBOLS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _save_symbols_cache(symbols: list[dict]):
    try:
        tmp = SYMBOLS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(symbols, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SYMBOLS_FILE)
    except Exception as e:
        logger.warning("Symbols cache mentési hiba: %s", e)


def get_cached_symbols() -> list[dict]:
    return _load_symbols_cache()


def upsert_symbol(entry: dict):
    """Hozzáad vagy frissít egy tickert a symbols_cache.json-ban."""
    symbols = _load_symbols_cache()
    ticker = entry.get("ticker", "").upper()
    existing = next((s for s in symbols if s.get("ticker", "").upper() == ticker), None)
    if existing:
        existing.update(entry)
        existing["last_seen"] = datetime.now().isoformat(timespec="seconds")
    else:
        entry["last_seen"] = datetime.now().isoformat(timespec="seconds")
        symbols.append(entry)
    _save_symbols_cache(symbols)


# ---------------------------------------------------------------------------
# KERESÉSI LOGIKA
# ---------------------------------------------------------------------------

def _normalize(s: str) -> str:
    return s.lower().strip()


def _search_local(query: str) -> list[dict]:
    """Keres a helyi fallback listában és a symbols cache-ben."""
    q = _normalize(query)
    results = []
    seen = set()

    # 1. Symbols cache
    for sym in _load_symbols_cache():
        ticker = sym.get("ticker", "")
        name = sym.get("name", "")
        aliases = sym.get("query_aliases", [])
        if (q in _normalize(ticker) or q in _normalize(name)
                or any(q in _normalize(a) for a in aliases)):
            if ticker not in seen:
                results.append({
                    "ticker": ticker,
                    "name": name,
                    "currency": sym.get("currency", ""),
                    "exchange": sym.get("exchange", ""),
                    "type": "EQUITY",
                    "source": "cache",
                })
                seen.add(ticker)

    # 2. Helyi fallback
    for item in LOCAL_FALLBACK:
        ticker = item["ticker"]
        name = item["name"]
        aliases = item.get("aliases", [])
        if (q in _normalize(ticker) or q in _normalize(name)
                or any(q in _normalize(a) for a in aliases)):
            if ticker not in seen:
                results.append({
                    "ticker": ticker,
                    "name": name,
                    "currency": item.get("currency", ""),
                    "exchange": item.get("exchange", ""),
                    "type": "EQUITY",
                    "source": "local",
                })
                seen.add(ticker)

    return results


def _search_yahoo(query: str) -> tuple[list[dict], list[str]]:
    """Yahoo Finance keresés – rate limit esetén üres listát ad vissza."""
    cache_key = "search:" + _normalize(query)
    cached = cache.get(cache_key)
    if cached is not None:
        return cached, []

    try:
        s = yf.Search(query, max_results=8, raise_errors=False)
        quotes = []
        for attr in ("quotes", "_response", "response"):
            try:
                val = getattr(s, attr, None)
                if isinstance(val, list):
                    quotes = val
                    break
                if isinstance(val, dict):
                    quotes = val.get("quotes", [])
                    if quotes:
                        break
            except Exception:
                pass

        found = []
        for q in quotes:
            if not isinstance(q, dict):
                continue
            if q.get("quoteType") not in ("EQUITY", "ETF"):
                continue
            found.append({
                "ticker":   q.get("symbol", ""),
                "name":     q.get("longname") or q.get("shortname") or "",
                "exchange": q.get("exchange", ""),
                "currency": q.get("currency", ""),
                "type":     q.get("quoteType", "EQUITY"),
                "source":   "yahoo",
            })

        cache.set(cache_key, found, SEARCH_CACHE_TTL)
        return found, []

    except Exception as e:
        msg = str(e)
        if "Too Many Requests" in msg or "429" in msg:
            warn = "A Yahoo kereső most nem elérhető (rate limit). Helyi találatok és kézi hozzáadás elérhető."
        else:
            warn = f"Külső kereső hiba: {msg}"
        logger.warning("Yahoo search hiba '%s': %s", query, e)
        return [], [warn]


def _suffix_suggestions(query: str) -> list[dict]:
    """Ha a keresés egyszerű betűszó, generál suffix-es javaslatokat."""
    q = query.strip().upper()
    # Csak ha rövid, szimpla szó, nincs már benne pont
    if len(q) > 6 or "." in q or " " in q:
        return []
    suggestions = []
    seen_tickers = set()
    for suffix, exch_name in SUFFIX_HINTS.items():
        candidate = f"{q}.{suffix}"
        if candidate not in seen_tickers:
            suggestions.append({
                "ticker":   candidate,
                "name":     f"{q} – {exch_name}",
                "currency": "",
                "exchange": exch_name,
                "type":     "EQUITY",
                "source":   "suffix_hint",
            })
            seen_tickers.add(candidate)
    return suggestions


def search(query: str) -> dict:
    """
    Unified search. Visszaadja:
    {
      "results": [...],
      "errors": [...],
      "timestamp": "...",
      "source": "cache/local/yahoo/suffix_hint"
    }
    """
    if not query or len(query.strip()) < 1:
        return {"results": [], "errors": [], "timestamp": _ts(), "source": "none"}

    local_results = _search_local(query)
    yahoo_results, yahoo_errors = _search_yahoo(query)

    # Összefűzés, duplikátum szűrés
    seen = set()
    combined = []
    for item in local_results + yahoo_results:
        t = item.get("ticker", "")
        if t and t not in seen:
            combined.append(item)
            seen.add(t)

    # Ha nincs semmi találat, adjunk suffix javaslatokat
    if not combined:
        combined = _suffix_suggestions(query)

    # Forrás meghatározása
    sources = set(r.get("source", "") for r in combined)
    source_label = "/".join(sorted(sources)) if sources else "none"

    return {
        "results": combined,
        "errors": yahoo_errors,
        "timestamp": _ts(),
        "source": source_label,
    }


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")
