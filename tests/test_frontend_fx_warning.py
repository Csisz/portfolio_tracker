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
    assert "Vétel dátuma" in html
    assert "Vételi ár" in html
    assert "Vételi költség" in html
    assert "purchase_date" in html
    assert "purchase_cost" in html
    assert "purchase-date" in html
    assert "purchase-cost" in html
    assert "Atlagos veteli ar" not in html
    assert "/api/price-history" not in html
    assert "purchase_price_source" not in html
    assert "purchase-source" not in html
    assert "fetchHistorical" not in html
    assert "historikus" not in html.lower()
    assert "btn-history" not in html


def test_add_stock_card_shows_current_price_and_requires_purchase_price():
    html = _index_html()
    assert 'id="sel-current-price"' in html
    assert "Aktualis arfolyam" in html
    assert "Forras: cache / utolso ismert arfolyam" in html
    assert "prefillCurrentPurchasePrice('sel', ticker)" in html
    assert "function normalizeDisplayTicker(ticker)" in html
    assert "OTP: 'OTP.BD'" in html
    assert "MOL: 'MOL.BD'" in html
    assert "findPriceEntry(d.prices, ticker)" in html
    assert "body: JSON.stringify({tickers: [ticker]})" in html
    assert "validateAddInputs('sel', qty)" in html
    assert "validateAddInputs('m', qty)" in html
    assert "Adj meg ervenyes veteli arat." in html
    assert "Az aktualis arfolyam most nem elerheto. Add meg kezzel a veteli arat." in html
