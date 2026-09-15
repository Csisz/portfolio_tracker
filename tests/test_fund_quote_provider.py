from unittest.mock import MagicMock, patch

import pytest
import requests

from services.fund_quote_provider import (
    BAMOSZ_DOWNLOAD_URL,
    FundNotFoundError,
    FundProviderUnavailableError,
    FundQuoteUnavailableError,
    MalformedFundResponseError,
    UnsupportedFundCurrencyError,
    fetch_fund_quote,
    parse_bamosz_csv,
)


def _csv(currency="EUR", latest_price="2,98626"):
    return (
        '"Letöltés időpontja","2026.09.15. 12:00"\n'
        '"Alap neve:","Accorde Spartan Görög Részvényalap"\n'
        '"Befektetési sorozat megjelölése:","B sorozat"\n'
        f'"Befektetési jegy devizaneme:","{currency}"\n'
        '"Dátum","Árfolyam","NEÉ"\n'
        f'"2026/09/10","{latest_price}","51318915,71"\n'
        '"2026/09/09","2,900001","51000000"\n'
    ).encode("iso-8859-2")


def _response(payload):
    response = MagicMock()
    response.content = payload
    response.raise_for_status = MagicMock()
    return response


def test_parse_bamosz_eur_fund_preserves_nav_precision_and_metadata():
    quote = parse_bamosz_csv(_csv(), "HU0000722590")

    assert quote == {
        "symbol": "HU0000722590",
        "name": "Accorde Spartan Görög Részvényalap – B sorozat",
        "price": 2.98626,
        "currency": "EUR",
        "quote_date": "2026-09-10",
        "source": "BAMOSZ",
        "source_url": "https://www.bamosz.hu/alapoldal?isin=HU0000722590",
    }


def test_parse_bamosz_huf_fund():
    quote = parse_bamosz_csv(_csv(currency="HUF", latest_price="2388,996"), "HU0000722582")

    assert quote["currency"] == "HUF"
    assert quote["price"] == 2388.996


def test_parse_bamosz_historical_quote_uses_latest_nav_on_or_before_date():
    quote = parse_bamosz_csv(
        _csv(),
        "HU0000722590",
        as_of_date="2026-09-09",
    )

    assert quote["quote_date"] == "2026-09-09"
    assert quote["price"] == 2.900001


def test_fetch_fund_quote_uses_official_csv_endpoint_and_safe_headers():
    with patch(
        "services.fund_quote_provider.requests.post",
        return_value=_response(_csv()),
    ) as post:
        quote = fetch_fund_quote(" hu0000722590 ")

    assert quote["symbol"] == "HU0000722590"
    post.assert_called_once()
    args, kwargs = post.call_args
    assert args[0] == BAMOSZ_DOWNLOAD_URL
    assert kwargs["data"] == {"isin": "HU0000722590", "separator": "vesszo"}
    assert kwargs["headers"]["User-Agent"]
    assert kwargs["headers"]["Accept-Encoding"] == "identity"
    assert kwargs["timeout"] > 0


def test_fetch_fund_quote_timeout_is_provider_error():
    with patch(
        "services.fund_quote_provider.requests.post",
        side_effect=requests.exceptions.Timeout,
    ):
        with pytest.raises(FundProviderUnavailableError, match="időtúllépés"):
            fetch_fund_quote("HU0000722590")


def test_unknown_isin_is_not_found():
    payload = (
        '"Letöltés időpontja","2026.09.15. 12:00"\n'
        '"Dátum","Árfolyam","NEÉ"\n'
    ).encode("iso-8859-2")

    with pytest.raises(FundNotFoundError):
        parse_bamosz_csv(payload, "HU0000000000")


def test_known_fund_with_missing_price_is_unavailable():
    payload = _csv(latest_price="").replace(b'"2,900001"', b'""')
    with pytest.raises(FundQuoteUnavailableError, match="nincs használható árfolyam"):
        parse_bamosz_csv(payload, "HU0000722590", as_of_date="2026-09-10")


def test_malformed_response_without_price_table_is_rejected():
    payload = (
        '"Alap neve:","Teszt Alap"\n'
        '"Befektetési jegy devizaneme:","EUR"\n'
    ).encode("iso-8859-2")

    with pytest.raises(MalformedFundResponseError):
        parse_bamosz_csv(payload, "HU0000722590")


def test_unsupported_currency_is_rejected():
    with pytest.raises(UnsupportedFundCurrencyError, match="XYZ"):
        parse_bamosz_csv(_csv(currency="XYZ"), "HU0000722590")
