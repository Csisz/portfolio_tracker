"""
MNB devizaárfolyam kapcsolat debug script.

Futtatás a projekt gyökéréből:
    python scripts/test_mnb.py

Végigpróbálja az összes MNB endpoint / SOAPAction kombinációt,
és kiírja, melyik működött + az EUR/HUF és USD/HUF árfolyamokat.
"""
import html
import sys
import os
import xml.etree.ElementTree as ET
from itertools import product

# A projekt gyökeréből importáljuk a services modult
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import requests
except ImportError:
    print("HIBA: 'requests' csomag nincs telepítve. Futtasd: pip install requests")
    sys.exit(1)

ENDPOINTS = [
    "https://www.mnb.hu/arfolyamok.asmx",
    "http://www.mnb.hu/arfolyamok.asmx",
]
SOAP_ACTIONS = [
    "http://www.mnb.hu/webservices/MNBArfolyamServiceSoap/GetCurrentExchangeRates",
    "http://www.mnb.hu/webservices/GetCurrentExchangeRates",
]
SOAP_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
    ' xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
    ' xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
    "<soap:Body>"
    '<GetCurrentExchangeRates xmlns="http://www.mnb.hu/webservices/" />'
    "</soap:Body>"
    "</soap:Envelope>"
)


def parse_rates(soap_text: str) -> dict:
    """SOAP válaszból kinyeri az árfolyamokat."""
    try:
        root = ET.fromstring(soap_text)
    except ET.ParseError as e:
        print(f"  ✗ SOAP parse hiba: {e}")
        return {}

    result_text = None
    for elem in root.iter():
        if elem.tag.endswith("GetCurrentExchangeRatesResult"):
            result_text = elem.text
            break

    if not result_text:
        print("  ✗ GetCurrentExchangeRatesResult nem található")
        return {}

    inner_xml = html.unescape(result_text)
    rates = {}
    try:
        inner_root = ET.fromstring(inner_xml)
        for elem in inner_root.iter():
            if not elem.tag.endswith("Rate"):
                continue
            curr = elem.attrib.get("curr", "").strip().upper()
            unit = float(elem.attrib.get("unit", "1") or "1")
            text = (elem.text or "").strip().replace(",", ".")
            if curr and text:
                try:
                    rates[curr] = round(float(text) / unit, 4)
                except ValueError:
                    pass
    except ET.ParseError as e:
        print(f"  ✗ Belső XML parse hiba: {e}")

    return rates


def main():
    print("=" * 60)
    print("MNB devizaárfolyam kapcsolat teszt")
    print("=" * 60)
    print()

    combos = list(product(ENDPOINTS, SOAP_ACTIONS))
    print(f"Összesen {len(combos)} kombináció próbálása:\n")

    success_rates = None
    success_info = None

    for i, (endpoint, soap_action) in enumerate(combos, 1):
        print(f"[{i}/{len(combos)}] Próbálás...")
        print(f"  Endpoint  : {endpoint}")
        print(f"  SOAPAction: {soap_action}")

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "SOAPAction": f'"{soap_action}"',
            "User-Agent": "portfolio-tracker/1.0",
        }

        try:
            resp = requests.post(
                endpoint,
                data=SOAP_BODY.encode("utf-8"),
                headers=headers,
                timeout=15,
            )
            print(f"  HTTP státusz: {resp.status_code}")

            if resp.status_code != 200:
                print(f"  ✗ Nem 200-as válasz\n")
                continue

            print(f"  Válasz első 500 karakter:")
            print(f"  {resp.text[:500]!r}")
            print()

            rates = parse_rates(resp.text)
            if rates:
                print(f"  ✓ SIKERES! {len(rates)} deviza árfolyam kinyerve.")
                success_rates = rates
                success_info = (endpoint, soap_action)
                break
            else:
                print(f"  ✗ Válasz érkezett, de árfolyam nem nyerhető ki\n")

        except requests.exceptions.Timeout:
            print(f"  ✗ Timeout (15s)\n")
        except requests.exceptions.ConnectionError as e:
            print(f"  ✗ Kapcsolati hiba: {e}\n")
        except Exception as e:
            print(f"  ✗ Egyéb hiba: {e}\n")

    print()
    print("=" * 60)

    if success_rates:
        print(f"✓ SIKERES KOMBINÁCIÓ:")
        print(f"  Endpoint  : {success_info[0]}")
        print(f"  SOAPAction: {success_info[1]}")
        print()
        print("Árfolyamok (HUF-ban 1 egységre):")
        for key in ["EUR", "USD", "GBP", "CHF", "JPY", "PLN", "CZK"]:
            if key in success_rates:
                print(f"  {key}/HUF = {success_rates[key]}")
        print()
        if "EUR" in success_rates and "USD" in success_rates:
            usd_eur = round(success_rates["USD"] / success_rates["EUR"], 6)
            print(f"  USD/EUR = {usd_eur}")
    else:
        print("✗ MINDEN KOMBINÁCIÓ SIKERTELEN")
        print()
        print("Lehetséges okok:")
        print("  - Nincs internet kapcsolat")
        print("  - Az MNB szerver átmenetileg nem elérhető")
        print("  - Tűzfal blokkolja a kimenő kéréseket")
        print()
        print("Ellenőrizd manuálisan:")
        print("  Invoke-WebRequest https://www.mnb.hu/arfolyamok.asmx -Method GET")

    print("=" * 60)


if __name__ == "__main__":
    main()
