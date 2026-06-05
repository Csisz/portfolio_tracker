"""
Frontend FX warning szovegek regresszios tesztjei.
"""
from pathlib import Path


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "index.html"


def _index_html() -> str:
    return TEMPLATE.read_text(encoding="utf-8")


def test_huf_only_fx_missing_message_is_not_warning():
    html = _index_html()
    assert "Devizaárfolyam nem elérhető, de a HUF összesítés pontos" not in html
    assert "A portfólió csak HUF elemeket tartalmaz, ezért devizaátváltásra nincs szükség." in html
    assert 'class="${needsFx ? \'fx-meta-warn\' : \'\'}"' in html


def test_mixed_currency_fx_missing_message_warns_about_incomplete_huf_total():
    html = _index_html()
    assert "function portfolioNeedsFx()" in html
    assert "Devizaárfolyam nem elérhető, ezért a HUF összesítés nem teljes." in html
def test_purchase_profit_loss_ui_present():
    html = _index_html()
    assert "purchase_price" in html
    assert "total-invested" in html
    assert "total-pl" in html
    assert "total-return" in html
    assert "/api/price-history" in html


def test_historical_price_errors_are_inline_not_alerts():
    html = _index_html()
    form_block = html[html.index("async function fetchHistoricalForForm"):html.index("const _selectStockBase")]
    row_block = html[html.index("async function fetchHistoricalForRow"):html.index("function setRowPurchaseSource")]
    assert "alert(" not in form_block
    assert "alert(" not in row_block
    assert "Ehhez a reszvenyhez vagy datumhoz nem sikerult historikus arfolyamot lekerni. Add meg kezzel a veteli arat." in form_block
    assert "Ehhez a reszvenyhez vagy datumhoz nem sikerult historikus arfolyamot lekerni. Add meg kezzel a veteli arat." in row_block
    assert "setRowPurchaseWarning" in row_block


def test_purchase_return_guard_present():
    html = _index_html()
    assert "function isValidPurchasePrice" in html
    assert "source !== 'manual' && n <= 1" in html
    assert "m.returnPct == null ? '-'" in html
