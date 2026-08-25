"""Shared portfolio valuation rules used by the web UI and data exports."""

from __future__ import annotations

import math
from typing import Any

from services.stocks import cash_currency_from_ticker, normalize_ticker


def _positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _non_negative_number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) and number >= 0 else default


def _huf_rate(currency: str | None, fx: dict) -> float | None:
    currency = str(currency or "").strip().upper()
    if currency == "HUF":
        return 1.0
    if currency == "GBX":
        gbp_rate = _positive_number(fx.get("GBP/HUF"))
        return gbp_rate / 100 if gbp_rate is not None else None
    rate = _positive_number(fx.get(f"{currency}/HUF"))
    return rate


def _to_huf(value: float | None, currency: str | None, fx: dict) -> float | None:
    if value is None:
        return None
    rate = _huf_rate(currency, fx)
    return value * rate if rate is not None else None


def _quote_for(item: dict, prices: dict) -> dict:
    ticker = str(item.get("ticker") or "").strip().upper()
    normalized = normalize_ticker(ticker)
    quote = prices.get(ticker) or prices.get(normalized)
    if not quote:
        quote = next(
            (value for key, value in prices.items() if normalize_ticker(key) == normalized),
            None,
        )
    if quote:
        return dict(quote)
    if _positive_number(item.get("last_price")) is not None:
        return {
            "price": item.get("last_price"),
            "currency": item.get("last_price_currency") or item.get("currency"),
            "source": "Utolsó ismert árfolyam",
            "quote_time": item.get("last_price_time"),
            "stale": True,
            "delayed": True,
        }
    return {}


def calculate_portfolio(portfolio: list[dict], prices: dict, fx: dict) -> dict:
    """Calculate lot metrics and HUF-normalized totals without mixing currencies."""
    records = []
    composition: dict[str, dict] = {}
    current_huf = 0.0
    invested_huf = 0.0
    profit_huf = 0.0
    current_known = False
    missing_current_value = False
    missing_investment_fx = False
    missing_profit_value = False
    missing_purchase_data = False

    for item in portfolio:
        ticker = str(item.get("ticker") or "").strip().upper()
        cash_currency = cash_currency_from_ticker(ticker)
        is_cash = cash_currency is not None
        qty = _positive_number(item.get("qty"))
        quote = _quote_for(item, prices)

        if is_cash:
            price = 1.0
            currency = cash_currency
            quote = {
                "price": 1.0,
                "currency": currency,
                "source": "Készpénz",
                "quote_time": None,
                "stale": False,
                "delayed": False,
            }
        else:
            price = _positive_number(quote.get("price"))
            currency = str(quote.get("currency") or item.get("currency") or "").upper()

        current_native = price * qty if price is not None and qty is not None else None
        current_value_huf = _to_huf(current_native, currency, fx)
        if current_value_huf is not None:
            current_huf += current_value_huf
            current_known = True
        elif current_native is not None:
            missing_current_value = True

        if is_cash:
            invested_native = 0.0
            invested_value_huf = 0.0
            profit_native = 0.0
            profit_value_huf = 0.0
            return_pct = None
        else:
            purchase_price = _positive_number(item.get("purchase_price"))
            purchase_cost = _non_negative_number(item.get("purchase_cost"))
            if purchase_price is None or qty is None:
                invested_native = None
                invested_value_huf = None
                profit_native = None
                profit_value_huf = None
                return_pct = None
                missing_purchase_data = True
            else:
                invested_native = purchase_price * qty + purchase_cost
                invested_value_huf = _to_huf(invested_native, currency, fx)
                if invested_value_huf is None:
                    missing_investment_fx = True
                else:
                    invested_huf += invested_value_huf

                profit_native = current_native - invested_native if current_native is not None else None
                profit_value_huf = _to_huf(profit_native, currency, fx)
                if profit_value_huf is not None:
                    profit_huf += profit_value_huf
                else:
                    missing_profit_value = True
                return_pct = (
                    profit_native / invested_native * 100
                    if profit_native is not None and invested_native > 0
                    else None
                )

            if current_native is not None:
                group = composition.setdefault(currency or "UNKNOWN", {
                    "currency": currency or "UNKNOWN",
                    "count": 0,
                    "current_value_native": 0.0,
                    "current_value_huf": 0.0,
                    "huf_complete": True,
                })
                group["count"] += 1
                group["current_value_native"] += current_native
                if current_value_huf is None:
                    group["huf_complete"] = False
                else:
                    group["current_value_huf"] += current_value_huf

        records.append({
            "id": item.get("id"),
            "ticker": ticker,
            "name": item.get("name") or ticker,
            "quantity": qty,
            "currency": currency,
            "purchase_price": 1.0 if is_cash else _positive_number(item.get("purchase_price")),
            "purchase_date": item.get("purchase_date"),
            "purchase_cost": 0.0 if is_cash else _non_negative_number(item.get("purchase_cost")),
            "current_price": price,
            "current_value_native": current_native,
            "current_value_huf": current_value_huf,
            "invested_value_native": invested_native,
            "invested_value_huf": invested_value_huf,
            "profit_loss_native": profit_native,
            "profit_loss_huf": profit_value_huf,
            "return_pct": return_pct,
            "quote_source": quote.get("source") or "",
            "quote_timestamp": quote.get("quote_time") or quote.get("timestamp"),
            "stale_or_delayed": bool(quote.get("stale") or quote.get("delayed")),
            "is_cash": is_cash,
        })

    total_return = None
    if invested_huf > 0 and not missing_investment_fx and not missing_profit_value:
        total_return = profit_huf / invested_huf * 100

    return {
        "records": records,
        "summary": {
            "current_portfolio_huf": current_huf if current_known else None,
            "invested_huf": invested_huf,
            "profit_loss_huf": profit_huf,
            "return_pct": total_return,
            "missing_purchase_data": missing_purchase_data,
            "incomplete_huf_value": missing_current_value or missing_investment_fx,
        },
        "composition": list(composition.values()),
    }
