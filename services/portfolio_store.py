"""
Portfolio.json olvasás/írás – robusztus, atomic write.
"""
import json
import logging
import os
import shutil
from datetime import datetime

logger = logging.getLogger(__name__)

DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portfolio.json")

DEFAULT_PORTFOLIO = [
    {"ticker": "OTP.BD",     "name": "OTP Bank",  "qty": 10, "currency": "HUF", "exchange": "BUD"},
    {"ticker": "MOL.BD",     "name": "MOL",        "qty": 5,  "currency": "HUF", "exchange": "BUD"},
    {"ticker": "RICHTER.BD", "name": "Richter",    "qty": 8,  "currency": "HUF", "exchange": "BUD"},
    {"ticker": "AAPL",       "name": "Apple",      "qty": 3,  "currency": "USD", "exchange": "NASDAQ"},
    {"ticker": "MSFT",       "name": "Microsoft",  "qty": 2,  "currency": "USD", "exchange": "NASDAQ"},
]


def _validate_item(item: dict) -> dict:
    """Biztosítja, hogy egy portfólió elem tartalmazza a kötelező mezőket."""
    return {
        "ticker":          str(item.get("ticker", "")).strip().upper(),
        "name":            str(item.get("name") or item.get("ticker", "")),
        "qty":             float(item.get("qty", 0)),
        "currency":        str(item.get("currency", "")).upper() or None,
        "exchange":        str(item.get("exchange", "")),
        "source":          str(item.get("source", "unknown")),
        "manually_added":  bool(item.get("manually_added", False)),
        "last_price":      item.get("last_price"),
        "last_price_time": item.get("last_price_time"),
        "purchase_price":  item.get("purchase_price"),
        "purchase_date":   str(item.get("purchase_date") or "").strip() or None,
        "purchase_cost":   item.get("purchase_cost"),
        "display_order":   item.get("display_order"),
        "purchase_price_source": str(item.get("purchase_price_source") or "").strip() or None,
    }


def load_portfolio() -> list[dict]:
    if not os.path.exists(DATA_FILE):
        return [_validate_item(i) for i in DEFAULT_PORTFOLIO]

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        logger.error("Portfolio JSON sérült: %s – backup készítése", e)
        _backup_broken()
        return [_validate_item(i) for i in DEFAULT_PORTFOLIO]
    except Exception as e:
        logger.error("Portfolio olvasási hiba: %s", e)
        return [_validate_item(i) for i in DEFAULT_PORTFOLIO]

    if not isinstance(raw, list):
        logger.warning("Portfolio.json nem lista – üres portfólióval indulunk")
        return []

    result = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        ticker = str(item.get("ticker", "")).strip()
        if not ticker:
            continue
        try:
            result.append(_validate_item(item))
        except Exception as e:
            logger.warning("Portfólió elem kihagyva (%s): %s", item, e)

    return result


def save_portfolio(portfolio: list[dict]):
    validated = [_validate_item(i) for i in portfolio]
    tmp = DATA_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(validated, f, ensure_ascii=False, indent=2)
        os.replace(tmp, DATA_FILE)
    except Exception as e:
        logger.error("Portfolio mentési hiba: %s", e)
        raise


def _backup_broken():
    broken = DATA_FILE.replace(".json", ".broken.json")
    try:
        shutil.copy2(DATA_FILE, broken)
        logger.info("Sérült portfólió mentve: %s", broken)
    except Exception:
        pass
