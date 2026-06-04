"""
Devizaárfolyam lekérés – elsődleges forrás: MNB SOAP webservice.

Az MNB GetCurrentExchangeRates SOAP POST-tal hívandó, nem REST GET-tel.
Ha az első kombináció 404-et ad, végigpróbálja az összes endpoint/SOAPAction párt.
"""
import html
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

# Elsődleges URL és action – a tesztek erre hivatkoznak
MNB_URL = "https://www.mnb.hu/arfolyamok.asmx"
MNB_SOAP_ACTION = "http://www.mnb.hu/webservices/MNBArfolyamServiceSoap/GetCurrentExchangeRates"

# Összes endpoint + SOAPAction kombináció (sorrendben próbálja)
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

FX_CACHE_KEY = "fx_rates"
FX_CACHE_TTL = 3600  # 1 óra
FX_FILE_CACHE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fx_cache.json")

SUPPORTED_CURRENCIES = [
    "USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD",
    "PLN", "CZK", "SEK", "NOK", "DKK",
]


# ---------------------------------------------------------------------------
# SOAP + XML parserek
# ---------------------------------------------------------------------------

def parse_mnb_soap_response(soap_text: str) -> dict:
    """
    Feldolgozza az MNB teljes SOAP válaszát.
    A GetCurrentExchangeRatesResult elem HTML-escape-elt belső XML-t tartalmaz.
    Visszaadja: { "EUR": 392.45, "USD": 361.12, ... }
    """
    try:
        root = ET.fromstring(soap_text)
    except ET.ParseError as e:
        logger.error("SOAP boríték parse hiba: %s", e)
        return {}

    result_text = None
    for elem in root.iter():
        if elem.tag.endswith("GetCurrentExchangeRatesResult"):
            result_text = elem.text
            break

    if not result_text:
        logger.error("GetCurrentExchangeRatesResult nem található a SOAP válaszban")
        return {}

    inner_xml = html.unescape(result_text)
    return parse_mnb_current_fx_xml(inner_xml)


def parse_mnb_current_fx_xml(xml_text: str) -> dict:
    """
    Feldolgozza az MNB belső XML-t (MNBCurrentExchangeRates).
    Namespace-független Rate elem keresés.
    Visszaadja: { "EUR": 392.45, "USD": 361.12, ... } (1 egységre normálva)
    """
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
        logger.error("MNB belső XML parse hiba: %s", e)
    return rates


# ---------------------------------------------------------------------------
# Lekérés – multi-endpoint retry
# ---------------------------------------------------------------------------

def get_mnb_current_fx() -> tuple[dict, list]:
    """
    Lekéri az MNB aktuális devizaárfolyamait SOAP POST hívással.
    Végigpróbálja az összes endpoint × SOAPAction kombinációt.
    Visszaadja: (rates_dict, errors_list)
    """
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
                logger.info("MNB próbálkozás sikertelen: %s", msg)
                attempt_errors.append(f"HTTP {resp.status_code} ({endpoint})")
                continue

            rates = parse_mnb_soap_response(resp.text)
            if rates:
                logger.info("MNB sikeres: endpoint=%s action=%s", endpoint, soap_action)
                return rates, []

            attempt_errors.append(f"Üres válasz ({endpoint})")

        except requests.exceptions.Timeout:
            msg = f"Timeout: {endpoint}"
            logger.info("MNB próbálkozás sikertelen: %s", msg)
            attempt_errors.append(msg)
        except requests.exceptions.ConnectionError:
            msg = f"Kapcsolati hiba: {endpoint}"
            logger.info("MNB próbálkozás sikertelen: %s", msg)
            attempt_errors.append(msg)
        except Exception as e:
            msg = f"{endpoint}: {e}"
            logger.warning("MNB lekérés hiba: %s", msg)
            attempt_errors.append(str(e))

    summary = "MNB nem elérhető. Próbálkozások: " + "; ".join(attempt_errors)
    logger.error(summary)
    return {}, [summary]


# ---------------------------------------------------------------------------
# Cache kezelés
# ---------------------------------------------------------------------------

def _load_file_cache() -> dict | None:
    try:
        if os.path.exists(FX_FILE_CACHE):
            with open(FX_FILE_CACHE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None


def _save_file_cache(data: dict):
    tmp = FX_FILE_CACHE + ".tmp"
    try:
        clean = {k: v for k, v in data.items() if k != "_raw"}
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(clean, f, ensure_ascii=False, indent=2)
        os.replace(tmp, FX_FILE_CACHE)
    except Exception as e:
        logger.warning("FX fájl cache mentési hiba: %s", e)


# ---------------------------------------------------------------------------
# Fő API
# ---------------------------------------------------------------------------

def get_fx_rates() -> dict:
    """
    Visszaadja az aktuális devizaárfolyamokat.
    Struktúra:
      {
        "fx": {"EUR/HUF": 392.12, "USD/HUF": 361.44, ...},
        "errors": [],
        "source": "MNB",
        "timestamp": "2026-06-04T10:47:48"
      }
    """
    # 1. Memory cache
    cached = cache.get(FX_CACHE_KEY)
    if cached:
        result = dict(cached)
        result.pop("_raw", None)
        result["source"] = "MNB/cache"
        return result

    # 2. Friss MNB SOAP lekérés
    rates, errors = get_mnb_current_fx()

    if rates:
        fx = _build_fx_dict(rates)
        now = datetime.now()
        result = {
            "fx": fx,
            "errors": errors,
            "source": "MNB",
            "timestamp": now.isoformat(timespec="seconds"),
            "date": now.date().isoformat(),
            "_raw": rates,
        }
        cache.set(FX_CACHE_KEY, result, FX_CACHE_TTL)
        _save_file_cache(result)
        clean = dict(result)
        clean.pop("_raw", None)
        return clean

    # 3. Fájl cache fallback
    file_cached = _load_file_cache()
    if file_cached and file_cached.get("fx"):
        file_cached = dict(file_cached)
        file_cached["source"] = "cache"
        file_cached["errors"] = errors + ["Az utolsó ismert árfolyamot használjuk."]
        return file_cached

    # 4. Semmi nincs
    now = datetime.now()
    return {
        "fx": {},
        "errors": errors or ["Devizaárfolyamok nem elérhetők."],
        "source": "none",
        "timestamp": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
    }


def _build_fx_dict(rates: dict) -> dict:
    fx = {}
    for curr in SUPPORTED_CURRENCIES:
        if curr in rates:
            fx[f"{curr}/HUF"] = round(rates[curr], 4)

    # Fordított árfolyamok (HUF/deviza)
    if "EUR" in rates and rates["EUR"] > 0:
        fx["HUF/EUR"] = round(1 / rates["EUR"], 8)
    if "USD" in rates and rates["USD"] > 0:
        fx["HUF/USD"] = round(1 / rates["USD"], 8)

    # Keresztárfolyamok
    if "EUR" in rates and "USD" in rates and rates["EUR"] > 0:
        fx["USD/EUR"] = round(rates["USD"] / rates["EUR"], 6)
        fx["EUR/USD"] = round(rates["EUR"] / rates["USD"], 6)
    if "EUR" in rates and "GBP" in rates and rates["EUR"] > 0:
        fx["GBP/EUR"] = round(rates["GBP"] / rates["EUR"], 6)

    return fx


def currency_to_huf_rate(currency: str, fx: dict) -> float | None:
    """
    Visszaadja, hogy 1 egység adott deviza hány HUF.
    GBp/GBX (London pence) esetén GBP/100.
    """
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
