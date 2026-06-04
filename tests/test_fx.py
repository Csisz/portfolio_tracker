"""
MNB FX parser és devizaárfolyam tesztek.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from unittest.mock import patch, MagicMock
import services.fx as fx_module
from services.fx import (
    parse_mnb_current_fx_xml,
    parse_mnb_soap_response,
    currency_to_huf_rate,
    _build_fx_dict,
    MNB_URL,
    MNB_SOAP_BODY,
    get_mnb_current_fx,
)

# ---------------------------------------------------------------------------
# Tesztadatok – belső XML (nem SOAP burok)
# ---------------------------------------------------------------------------
MNB_SAMPLE_XML = """<?xml version="1.0" encoding="utf-8"?>
<MNBCurrentExchangeRates>
  <Day date="2026-06-04">
    <Rate unit="1" curr="AUD">234,56</Rate>
    <Rate unit="1" curr="CHF">410,20</Rate>
    <Rate unit="1" curr="EUR">395,40</Rate>
    <Rate unit="1" curr="USD">362,80</Rate>
    <Rate unit="1" curr="GBP">458,90</Rate>
    <Rate unit="100" curr="JPY">248,50</Rate>
    <Rate unit="1" curr="PLN">89,12</Rate>
  </Day>
</MNBCurrentExchangeRates>"""

# ---------------------------------------------------------------------------
# SOAP boríték (mintha az MNB szerverről jönne)
# ---------------------------------------------------------------------------
# A belső XML HTML-escape-elve van a SOAP Result elemben
_INNER_ESCAPED = (
    "&lt;MNBCurrentExchangeRates&gt;"
    "&lt;Day date=\"2026-06-04\"&gt;"
    "&lt;Rate unit=\"1\" curr=\"EUR\"&gt;395,40&lt;/Rate&gt;"
    "&lt;Rate unit=\"1\" curr=\"USD\"&gt;362,80&lt;/Rate&gt;"
    "&lt;Rate unit=\"1\" curr=\"GBP\"&gt;458,90&lt;/Rate&gt;"
    "&lt;Rate unit=\"100\" curr=\"JPY\"&gt;248,50&lt;/Rate&gt;"
    "&lt;Rate unit=\"1\" curr=\"CHF\"&gt;410,20&lt;/Rate&gt;"
    "&lt;/Day&gt;"
    "&lt;/MNBCurrentExchangeRates&gt;"
)

MNB_SOAP_RESPONSE = f"""<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
               xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <soap:Body>
    <GetCurrentExchangeRatesResponse xmlns="http://www.mnb.hu/webservices/">
      <GetCurrentExchangeRatesResult>{_INNER_ESCAPED}</GetCurrentExchangeRatesResult>
    </GetCurrentExchangeRatesResponse>
  </soap:Body>
</soap:Envelope>"""


# ===========================================================================
# Belső XML parser tesztek
# ===========================================================================

def test_parse_mnb_eur_usd():
    rates = parse_mnb_current_fx_xml(MNB_SAMPLE_XML)
    assert "EUR" in rates
    assert "USD" in rates
    assert abs(rates["EUR"] - 395.40) < 0.01
    assert abs(rates["USD"] - 362.80) < 0.01


def test_parse_mnb_decimal_comma():
    rates = parse_mnb_current_fx_xml(MNB_SAMPLE_XML)
    assert abs(rates["GBP"] - 458.90) < 0.01


def test_parse_mnb_unit_100_jpy():
    rates = parse_mnb_current_fx_xml(MNB_SAMPLE_XML)
    # unit=100 → 248.50 / 100 = 2.485
    assert "JPY" in rates
    assert abs(rates["JPY"] - 2.485) < 0.001


def test_parse_mnb_pln():
    rates = parse_mnb_current_fx_xml(MNB_SAMPLE_XML)
    assert abs(rates["PLN"] - 89.12) < 0.01


def test_parse_empty_xml():
    rates = parse_mnb_current_fx_xml("<MNBCurrentExchangeRates/>")
    assert rates == {}


def test_parse_invalid_xml():
    rates = parse_mnb_current_fx_xml("not xml at all <<<")
    assert rates == {}


# ===========================================================================
# SOAP válasz parser tesztek
# ===========================================================================

def test_parse_mnb_soap_response_eur_usd():
    rates = parse_mnb_soap_response(MNB_SOAP_RESPONSE)
    assert "EUR" in rates
    assert "USD" in rates
    assert abs(rates["EUR"] - 395.40) < 0.01
    assert abs(rates["USD"] - 362.80) < 0.01


def test_parse_mnb_soap_response_unit_100():
    rates = parse_mnb_soap_response(MNB_SOAP_RESPONSE)
    assert "JPY" in rates
    assert abs(rates["JPY"] - 2.485) < 0.001


def test_parse_mnb_soap_response_empty_envelope():
    soap = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body><GetCurrentExchangeRatesResponse /></soap:Body>
</soap:Envelope>"""
    rates = parse_mnb_soap_response(soap)
    assert rates == {}


def test_parse_mnb_soap_response_bad_xml():
    rates = parse_mnb_soap_response("this is not xml")
    assert rates == {}


# ===========================================================================
# MNB URL és HTTP metódus ellenőrzés
# ===========================================================================

def test_mnb_url_is_base_asmx_not_rest():
    """MNB_URL ne tartalmazzon GetCurrentExchangeRates path-t."""
    assert "GetCurrentExchangeRates" not in MNB_URL
    assert MNB_URL == "https://www.mnb.hu/arfolyamok.asmx"


def test_mnb_soap_body_contains_method():
    """A SOAP body tartalmazza a GetCurrentExchangeRates metódushívást."""
    assert "GetCurrentExchangeRates" in MNB_SOAP_BODY


def _make_mock_resp(text=MNB_SOAP_RESPONSE):
    """200-as HTTP választ szimuláló mock – status_code explicit 200."""
    mock_resp = MagicMock()
    mock_resp.text = text
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    return mock_resp


def test_get_mnb_current_fx_uses_post():
    """get_mnb_current_fx() POST-tal hívja az MNB-t, nem GET-tel.
    200-as válasz esetén az első kombinációnál megáll (assert_called_once)."""
    with patch("services.fx.requests.post", return_value=_make_mock_resp()) as mock_post, \
         patch("services.fx.requests.get") as mock_get:
        rates, errors = get_mnb_current_fx()

    mock_post.assert_called_once()   # megállt az első 200-as válasznál
    mock_get.assert_not_called()
    assert "EUR" in rates
    assert not errors


def test_get_mnb_current_fx_post_url():
    """Az első POST hívás az elsődleges https .asmx URL-t célozza."""
    with patch("services.fx.requests.post", return_value=_make_mock_resp()) as mock_post:
        get_mnb_current_fx()

    # Az első (és egyetlen, mert 200 volt) hívás URL-je
    first_call_url = mock_post.call_args_list[0][0][0]
    assert first_call_url == "https://www.mnb.hu/arfolyamok.asmx"
    assert "GetCurrentExchangeRates" not in first_call_url


def test_get_mnb_current_fx_soap_action_header():
    """A SOAPAction header helyesen van beállítva az első hívásban."""
    with patch("services.fx.requests.post", return_value=_make_mock_resp()) as mock_post:
        get_mnb_current_fx()

    headers = mock_post.call_args_list[0][1]["headers"]
    assert "SOAPAction" in headers
    assert "GetCurrentExchangeRates" in headers["SOAPAction"]


def test_get_mnb_current_fx_retries_all_combos_on_failure():
    """Ha minden válasz nem-200, mind a 4 kombinációt megpróbálja."""
    bad_resp = MagicMock()
    bad_resp.status_code = 404
    bad_resp.raise_for_status = MagicMock()

    with patch("services.fx.requests.post", return_value=bad_resp) as mock_post:
        rates, errors = get_mnb_current_fx()

    assert mock_post.call_count == 4   # 2 endpoint × 2 SOAPAction
    assert rates == {}
    assert errors


def test_get_mnb_current_fx_timeout_returns_error():
    """Timeout esetén strukturált hibát ad vissza, nem kivételt dob."""
    import requests as req
    with patch("services.fx.requests.post", side_effect=req.exceptions.Timeout):
        rates, errors = get_mnb_current_fx()
    assert rates == {}
    assert len(errors) > 0
    assert any("timeout" in e.lower() or "időben" in e.lower() for e in errors)


def test_get_mnb_current_fx_connection_error():
    import requests as req
    with patch("services.fx.requests.post", side_effect=req.exceptions.ConnectionError):
        rates, errors = get_mnb_current_fx()
    assert rates == {}
    assert len(errors) > 0


# ===========================================================================
# FX szótár építés
# ===========================================================================

def test_build_fx_dict_usd_eur_cross():
    rates = parse_mnb_current_fx_xml(MNB_SAMPLE_XML)
    fx = _build_fx_dict(rates)
    assert "EUR/HUF" in fx
    assert "USD/HUF" in fx
    assert "USD/EUR" in fx
    expected_usd_eur = round(362.80 / 395.40, 6)
    assert abs(fx["USD/EUR"] - expected_usd_eur) < 0.0001


def test_build_fx_dict_eur_usd_cross():
    rates = {"EUR": 395.40, "USD": 362.80}
    fx = _build_fx_dict(rates)
    assert "EUR/USD" in fx
    assert abs(fx["EUR/USD"] - round(395.40 / 362.80, 6)) < 0.0001


# ===========================================================================
# currency_to_huf_rate
# ===========================================================================

def test_currency_to_huf_huf():
    assert currency_to_huf_rate("HUF", {}) == 1.0


def test_currency_to_huf_usd():
    fx = {"USD/HUF": 362.80}
    assert currency_to_huf_rate("USD", fx) == 362.80


def test_currency_to_huf_gbx_pence():
    fx = {"GBP/HUF": 458.90}
    rate = currency_to_huf_rate("GBX", fx)
    assert abs(rate - 4.589) < 0.001


def test_currency_to_huf_gbp_lowercase():
    fx = {"GBP/HUF": 458.90}
    rate = currency_to_huf_rate("gbp", fx)
    assert abs(rate - 458.90) < 0.01


def test_currency_to_huf_unknown():
    assert currency_to_huf_rate("XYZ", {}) is None
