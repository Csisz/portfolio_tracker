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
def test_main_portfolio_table_is_simple_and_keeps_purchase_data_inputs_elsewhere():
    html = _index_html()
    assert html.count("function renderTable()") == 1
    assert "renderTable = function()" not in html
    assert html.count("function calcTotals()") == 1
    assert "calcTotals = function()" not in html

    assert "purchase_price" in html
    assert "total-invested" in html
    assert "total-pl" in html
    assert "total-return" in html
    assert 'colspan="8"' in html
    assert "<th class=\"num\">Sorrend</th>" in html
    assert "<th>Tétel</th>" in html
    assert "<th class=\"num\">Darab / összeg" in html
    assert "<th class=\"num\">Aktuális ár / forrás</th>" in html
    assert "<th class=\"num\">Aktuális érték</th>" in html
    assert "<th class=\"num\">Eredmény</th>" in html
    assert "<th class=\"num\">Költség</th>" in html
    assert "<th>Műveletek</th>" in html
    assert "Vétel dátuma" in html
    assert "Vételi ár" in html
    assert "Vételi költség" in html
    table_start = html.index("<!-- Portfólió táblázat -->")
    table_end = html.index("</table>", table_start)
    table_html = html[table_start:table_end]
    assert "Vétel dátuma" not in table_html
    assert "Vételi ár" not in table_html
    assert "Hozam %" not in table_html
    assert "purchase_date" in html
    assert "purchase_cost" in html
    assert "purchase-date" in html
    assert "purchase-cost" in html
    assert "Atlagos veteli ar" not in html
    assert "/api/price-history" not in html
    assert "purchase_price_source" in html
    assert "purchase-source" not in html
    assert "fetchHistorical" not in html
    assert "historikus" not in html.lower()
    assert "btn-history" not in html


def test_manual_order_controls_present():
    html = _index_html()
    assert "Sorrend" in html
    assert "movePortfolioRow" in html
    assert "/api/portfolio/reorder" in html
    assert 'title="Fel"' in html
    assert 'title="Le"' in html
    assert "ordered_ids" in html


def test_table_controls_use_id_delete_cost_and_force_refresh():
    html = _index_html()
    assert "removeStock(${i},${itemId})" in html
    assert "delete(`/api/portfolio/${itemId}`" not in html
    assert "savePurchaseCost" in html
    assert "purchase_cost" in html
    assert 'onclick="refreshAll(true)"' in html
    assert "refreshAll(false)" in html
    assert "force_refresh: Boolean(forceRefresh)" in html
    assert "Adatok lekérve:" in html
    assert "Árfolyam időpontja:" in html
    assert "Késleltetett árfolyam" in html
    assert "Utolsó ismert árfolyam" in html
    assert "Piac zárva – utolsó elérhető záróár" in html
    assert "Vételi ár:" in html
    assert "profitLoss" in html
    table_css_start = html.index(".table-wrap")
    table_css_end = html.index("table {", table_css_start)
    assert "overflow: hidden" not in html[table_css_start:table_css_end]
    assert "overflow-x: auto" in html[table_css_start:table_css_end]


def test_cash_and_full_item_editor_are_visible():
    html = _index_html()
    assert "Készpénz hozzáadása" in html
    assert 'id="cash-currency"' in html
    assert "function addCash()" in html
    assert 'title="Tétel szerkesztése"' in html
    assert "function openItemEditor(i)" in html
    assert "function saveItemEditor()" in html


def test_add_stock_card_shows_current_price_and_requires_purchase_price():
    html = _index_html()
    assert 'id="sel-current-price"' in html
    assert "Aktuális árfolyam" in html
    assert "Forrás: cache / utolsó ismert árfolyam" in html
    assert "prefillCurrentPurchasePrice('sel', ticker)" in html
    assert "function normalizeDisplayTicker(ticker)" in html
    assert "OTP: 'OTP.BD'" in html
    assert "MOL: 'MOL.BD'" in html
    assert "findPriceEntry(d.prices, ticker)" in html
    assert "body: JSON.stringify({tickers: [ticker]})" in html
    assert "validateAddInputs('sel', qty)" in html
    assert "validateAddInputs('m', qty)" in html
    assert "Adj meg érvényes vételi árat." in html
    assert "Az aktuális árfolyam most nem elérhető. Add meg kézzel a vételi árat." in html
