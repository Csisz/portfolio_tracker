import pytest

from services.portfolio_valuation import calculate_portfolio


USD_HUF = {"USD/HUF": 360.0}
AAPL_QUOTE = {"AAPL": {"price": 120, "currency": "USD", "source": "mock"}}


def _aapl(price=100, qty=10, cost=10, item_id=1):
    return {
        "id": item_id, "ticker": "AAPL", "name": "Apple", "qty": qty,
        "currency": "USD", "purchase_price": price, "purchase_cost": cost,
    }


def _cash(currency, amount, item_id):
    return {
        "id": item_id, "ticker": f"CASH-{currency}",
        "name": f"Készpénz ({currency})", "qty": amount, "currency": currency,
        "purchase_price": 999, "purchase_cost": 50,
    }


def test_usd_stock_and_cash_follow_accounting_invariant():
    result = calculate_portfolio(
        [_aapl(), _cash("USD", 500, 2)],
        {**AAPL_QUOTE, "CASH-USD": {"price": 999, "currency": "USD"}},
        USD_HUF,
    )
    summary = result["summary"]
    assert summary["current_portfolio_huf"] == 612_000
    assert summary["invested_huf"] == 363_600
    assert summary["profit_loss_huf"] == 68_400
    assert summary["return_pct"] == pytest.approx(18.811881, rel=1e-6)
    assert result["composition"] == [{
        "currency": "USD", "count": 1, "current_value_native": 1200.0,
        "current_value_huf": 432000.0, "huf_complete": True,
    }]
    cash = result["records"][1]
    assert cash["current_price"] == 1
    assert cash["invested_value_huf"] == 0
    assert cash["profit_loss_huf"] == 0
    assert cash["return_pct"] is None


def test_mixed_huf_usd_and_cash_are_normalized_before_return():
    otp = {
        "id": 3, "ticker": "OTP.BD", "name": "OTP", "qty": 1,
        "currency": "HUF", "purchase_price": 10_000, "purchase_cost": 100,
    }
    prices = {**AAPL_QUOTE, "OTP.BD": {"price": 12_000, "currency": "HUF"}}
    result = calculate_portfolio([otp, _aapl(), _cash("USD", 500, 2)], prices, USD_HUF)
    summary = result["summary"]
    assert summary["current_portfolio_huf"] == 624_000
    assert summary["invested_huf"] == 373_700
    assert summary["profit_loss_huf"] == 70_300
    assert summary["return_pct"] == pytest.approx(18.812, rel=1e-4)


def test_cash_only_has_value_but_no_investment_return_or_stock_composition():
    result = calculate_portfolio(
        [_cash("HUF", 100_000, 1), _cash("USD", 500, 2)], {}, USD_HUF,
    )
    assert result["summary"] == {
        "current_portfolio_huf": 280_000,
        "invested_huf": 0.0,
        "profit_loss_huf": 0.0,
        "return_pct": None,
        "missing_purchase_data": False,
        "incomplete_huf_value": False,
    }
    assert result["composition"] == []


def test_multiple_lots_remain_separate_and_both_contribute():
    result = calculate_portfolio(
        [_aapl(price=100, item_id=1), _aapl(price=110, qty=5, cost=0, item_id=2)],
        AAPL_QUOTE, USD_HUF,
    )
    assert [row["id"] for row in result["records"]] == [1, 2]
    assert result["summary"]["invested_huf"] == (1010 + 550) * 360
    assert result["summary"]["profit_loss_huf"] == (190 + 50) * 360
    assert result["summary"]["return_pct"] == pytest.approx(240 / 1560 * 100)


def test_missing_stock_purchase_price_is_excluded_and_warned_about():
    missing = {"id": 4, "ticker": "MSFT", "qty": 2, "currency": "USD"}
    result = calculate_portfolio(
        [_aapl(), missing],
        {**AAPL_QUOTE, "MSFT": {"price": 200, "currency": "USD"}}, USD_HUF,
    )
    assert result["summary"]["current_portfolio_huf"] == (1200 + 400) * 360
    assert result["summary"]["invested_huf"] == 1010 * 360
    assert result["summary"]["profit_loss_huf"] == 190 * 360
    assert result["summary"]["missing_purchase_data"] is True
    assert result["records"][1]["invested_value_huf"] is None
    assert result["records"][1]["profit_loss_huf"] is None
