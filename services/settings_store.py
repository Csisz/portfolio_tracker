"""
Beállítások kezelése – DB háttérrel, in-memory cache-sel és fallback defaultokkal.
"""
import logging
import time
from typing import Any

from services.db import get_all_settings_raw, get_setting_raw, set_setting

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alapértelmezett értékek
# ---------------------------------------------------------------------------
DEFAULTS: dict[str, tuple[str, str, str]] = {
    # (value, value_type, description)
    "app_name":               ("Portfólió Követő", "string",  "Alkalmazás neve"),
    "default_currency":       ("HUF",              "string",  "Alapértelmezett deviza"),
    "price_cache_minutes":    ("15",               "int",     "Részvényárfolyam cache ideje (perc)"),
    "auto_refresh_seconds":   ("300",              "int",     "Főoldali automatikus frissítés alapértelmezett ideje (másodperc; 0 = kikapcsolva)"),
    "alerts_enabled":         ("true",             "bool",    "Email riasztások engedélyezése"),
    "alert_cooldown_minutes": ("60",               "int",     "Riasztások alapértelmezett újraküldési várakozása (perc)"),
    "search_cache_minutes":   ("60",               "int",     "Keresési cache ideje (perc)"),
    "fx_cache_hours":         ("24",               "int",     "Devizaárfolyam cache ideje (óra)"),
    "fx_rate_mode":           ("market",           "choice",  "Devizaárfolyam mód: market = aktuális piaci árfolyam portfólióértékeléshez; official = MNB hivatalos napi árfolyam; auto = piaci, ha elérhető, különben MNB"),
    "enable_yahoo":           ("true",             "bool",    "Yahoo Finance provider engedélyezve"),
    "enable_stooq":           ("true",             "bool",    "Stooq fallback engedélyezve"),
    "enable_mnb":             ("true",             "bool",    "MNB devizaárfolyam engedélyezve"),
    "allow_registration":     ("false",            "bool",    "Önkiszolgáló regisztráció engedélyezve"),
    "excel_export_enabled":   ("true",             "bool",    "Excel export engedélyezve"),
    "maintenance_mode":       ("false",            "bool",    "Karbantartási mód (csak admin léphet be)"),
}

# In-memory cache: {key: (value, expires_at)}
_cache: dict[str, tuple[Any, float]] = {}
_CACHE_TTL = 60.0  # másodperc


def get_setting(key: str, default=None) -> str:
    """Visszaad egy beállítást. Először cache, majd DB, majd DEFAULTS."""
    now = time.monotonic()
    if key in _cache:
        val, expires = _cache[key]
        if now < expires:
            return val
        del _cache[key]

    try:
        row = get_setting_raw(key)
        if row and row.get("value") is not None:
            val = row["value"]
            _cache[key] = (val, now + _CACHE_TTL)
            return val
    except Exception as e:
        logger.warning("Beállítás olvasási hiba (%s): %s", key, e)

    # Fallback default
    if key in DEFAULTS:
        return DEFAULTS[key][0]
    return default


def get_bool(key: str) -> bool:
    return get_setting(key, "false").lower() in ("true", "1", "yes")


def get_int(key: str) -> int:
    try:
        return int(get_setting(key, "0"))
    except (ValueError, TypeError):
        if key in DEFAULTS:
            return int(DEFAULTS[key][0])
        return 0


def save_setting(key: str, value: str) -> bool:
    """Menti a beállítást DB-be és frissíti a cache-t."""
    try:
        vtype = DEFAULTS.get(key, ("", "string", ""))[1]
        desc = DEFAULTS.get(key, ("", "", ""))[2]
        set_setting(key, value, vtype, desc)
        _cache[key] = (value, time.monotonic() + _CACHE_TTL)
        return True
    except Exception as e:
        logger.error("Beállítás mentési hiba (%s): %s", key, e)
        return False


def invalidate_cache(key: str = None):
    """Cache törlés – kulcs nélkül mindent töröl."""
    if key:
        _cache.pop(key, None)
    else:
        _cache.clear()


def init_default_settings():
    """Feltölti a settings táblát az alapértelmezett értékekkel, ha még nincsenek benne."""
    for key, (value, vtype, desc) in DEFAULTS.items():
        try:
            existing = get_setting_raw(key)
            if not existing:
                set_setting(key, value, vtype, desc)
        except Exception as e:
            logger.warning("Default beállítás init hiba (%s): %s", key, e)


def get_all_settings_with_defaults() -> list[dict]:
    """
    Visszaadja az összes beállítást a DEFAULTS-szal kiegészítve.
    DB-ben tárolt értékek felülírják a defaultokat.
    """
    db_rows = {r["key"]: r for r in _get_all_safe()}
    result = []
    for key, (def_val, vtype, desc) in DEFAULTS.items():
        if key in db_rows:
            result.append(db_rows[key])
        else:
            result.append({
                "key": key, "value": def_val,
                "value_type": vtype, "description": desc, "updated_at": None,
            })
    return result


def _get_all_safe() -> list[dict]:
    try:
        return get_all_settings_raw()
    except Exception:
        return []
