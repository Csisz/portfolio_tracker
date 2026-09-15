"""Hungarian investment-fund quotes from BAMOSZ's public CSV export."""

from __future__ import annotations

import csv
import io
import math
import re
from datetime import date, datetime

import requests


BAMOSZ_DOWNLOAD_URL = (
    "https://www.bamosz.hu/"
    "bamosz-public-alapoldal-portlet/kuka.download"
)
BAMOSZ_FUND_PAGE_URL = "https://www.bamosz.hu/alapoldal?isin={isin}"
REQUEST_TIMEOUT_SECONDS = 12
USER_AGENT = "portfolio-tracker/1.0"

# These currencies can be converted by the application's existing FX service.
SUPPORTED_CURRENCIES = {
    "HUF", "USD", "EUR", "GBP", "CHF", "JPY", "CAD", "AUD",
    "PLN", "CZK", "SEK", "NOK", "DKK",
}


class FundQuoteError(RuntimeError):
    """Base class for expected fund-provider failures."""


class InvalidIsinError(FundQuoteError):
    pass


class FundNotFoundError(FundQuoteError):
    pass


class FundProviderUnavailableError(FundQuoteError):
    pass


class FundQuoteUnavailableError(FundQuoteError):
    pass


class MalformedFundResponseError(FundQuoteError):
    pass


class UnsupportedFundCurrencyError(FundQuoteError):
    pass


def normalize_isin(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def is_hungarian_isin(symbol: str) -> bool:
    """Return True for the Hungarian ISIN shape required by the application."""
    return re.fullmatch(r"HU\d{10}", normalize_isin(symbol)) is not None


def looks_like_hungarian_isin(symbol: str) -> bool:
    """Identify likely mistyped Hungarian ISIN input without catching HU tickers."""
    normalized = normalize_isin(symbol)
    return bool(re.fullmatch(r"HU\d+", normalized) or (
        normalized.startswith("HU") and len(normalized) == 12
    ))


def _decode_csv(payload: bytes | str) -> str:
    if isinstance(payload, str):
        return payload
    try:
        return payload.decode("iso-8859-2")
    except UnicodeDecodeError as exc:
        raise MalformedFundResponseError("A BAMOSZ válaszának kódolása hibás.") from exc


def _label(value: str) -> str:
    return " ".join(str(value or "").replace("\xa0", " ").split()).rstrip(":").casefold()


def _parse_date(value: str) -> date | None:
    clean = str(value or "").strip().rstrip(".")
    for fmt in ("%Y/%m/%d", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _parse_price(value: str) -> float | None:
    clean = str(value or "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    try:
        price = float(clean)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def parse_bamosz_csv(
    payload: bytes | str,
    isin: str,
    *,
    as_of_date: str | date | None = None,
) -> dict:
    """Parse BAMOSZ's labeled metadata and dated NAV rows."""
    normalized = normalize_isin(isin)
    if not is_hungarian_isin(normalized):
        raise InvalidIsinError("Érvénytelen magyar ISIN. A várt forma: HU és 10 számjegy.")

    try:
        rows = list(csv.reader(io.StringIO(_decode_csv(payload))))
    except (csv.Error, UnicodeError) as exc:
        raise MalformedFundResponseError("A BAMOSZ CSV válasza nem olvasható.") from exc

    metadata: dict[str, str] = {}
    header_index = None
    for index, row in enumerate(rows):
        if not row:
            continue
        first = _label(row[0])
        if first == _label("Dátum") and len(row) > 1 and _label(row[1]) == _label("Árfolyam"):
            header_index = index
            break
        if len(row) > 1:
            metadata[first] = str(row[1] or "").strip()

    fund_name = metadata.get(_label("Alap neve"), "").strip()
    if not fund_name:
        raise FundNotFoundError(f"A BAMOSZ nem talált alapot ehhez az ISIN-hez: {normalized}")
    if header_index is None:
        raise MalformedFundResponseError("A BAMOSZ válaszából hiányzik az árfolyam-adatsor.")

    currency = metadata.get(_label("Befektetési jegy devizaneme"), "").strip().upper()
    if not currency:
        raise FundQuoteUnavailableError("A BAMOSZ válaszából hiányzik az alap devizaneme.")
    if currency not in SUPPORTED_CURRENCIES:
        raise UnsupportedFundCurrencyError(
            f"Az alap devizaneme nem támogatott: {currency}"
        )

    target = None
    if as_of_date is not None:
        if isinstance(as_of_date, date):
            target = as_of_date
        else:
            target = _parse_date(str(as_of_date))
        if target is None:
            raise ValueError("Invalid date")

    selected_date = None
    selected_price = None
    for row in rows[header_index + 1:]:
        if len(row) < 2:
            continue
        quote_date = _parse_date(row[0])
        price = _parse_price(row[1])
        if quote_date is None or price is None:
            continue
        if target is None or quote_date <= target:
            selected_date = quote_date
            selected_price = price
            break

    if selected_price is None or selected_date is None:
        raise FundQuoteUnavailableError("A BAMOSZ válaszában nincs használható árfolyam.")

    series = metadata.get(_label("Befektetési sorozat megjelölése"), "").strip()
    display_name = fund_name
    if series and series.casefold() not in fund_name.casefold():
        display_name = f"{fund_name} – {series}"

    return {
        "symbol": normalized,
        "name": display_name,
        "price": selected_price,
        "currency": currency,
        "quote_date": selected_date.isoformat(),
        "source": "BAMOSZ",
        "source_url": BAMOSZ_FUND_PAGE_URL.format(isin=normalized),
    }


def fetch_fund_quote(isin: str, *, as_of_date: str | date | None = None) -> dict:
    """Fetch a current (or date-bounded) fund NAV from BAMOSZ."""
    normalized = normalize_isin(isin)
    if not is_hungarian_isin(normalized):
        raise InvalidIsinError("Érvénytelen magyar ISIN. A várt forma: HU és 10 számjegy.")

    try:
        response = requests.post(
            BAMOSZ_DOWNLOAD_URL,
            data={"isin": normalized, "separator": "vesszo"},
            headers={
                "User-Agent": USER_AGENT,
                # BAMOSZ uses Content-Encoding for the character set, so avoid
                # requests treating "ISO-8859-2" as a compression algorithm.
                "Accept-Encoding": "identity",
                "Accept": "text/csv,application/vnd.ms-excel;q=0.9,*/*;q=0.5",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.exceptions.Timeout as exc:
        raise FundProviderUnavailableError("A BAMOSZ lekérése időtúllépés miatt sikertelen.") from exc
    except requests.exceptions.RequestException as exc:
        raise FundProviderUnavailableError("A BAMOSZ jelenleg nem érhető el.") from exc

    return parse_bamosz_csv(response.content, normalized, as_of_date=as_of_date)
