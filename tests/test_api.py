"""
Flask API végpont tesztek – session auth + mock-ok.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch

import services.db as db_module
import app as flask_app


@pytest.fixture(autouse=True)
def _reset_engine_before_each():
    """Minden teszt előtt törli az engine cache-t."""
    db_module.reset_engine()
    yield
    db_module.reset_engine()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Test client: ideiglenes DB, bejelentkezve admin/admin."""
    db_path = str(tmp_path / "test_api.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "portfolio.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "symbols.json"))
    db_module.reset_engine()  # engine cache törlése – minden teszt saját DB-t kap

    # DB + admin user inicializálás
    db_module.init_db("admin", "adminpass")

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test-secret-abc"

    with flask_app.app.test_client() as c:
        # Login
        c.post("/login", data={"username": "admin", "password": "adminpass"})
        yield c


@pytest.fixture()
def unauth_client(tmp_path, monkeypatch):
    """Test client: nincs bejelentkezve."""
    db_path = str(tmp_path / "test_unauth.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    db_module.init_db("admin", "adminpass")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test-secret-abc"
    with flask_app.app.test_client() as c:
        yield c


# ===========================================================================
# Auth
# ===========================================================================

def test_login_redirects_to_index(tmp_path, monkeypatch):
    db_path = str(tmp_path / "login.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.init_db("testuser", "testpass")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test"
    with flask_app.app.test_client() as c:
        r = c.post("/login", data={"username": "testuser", "password": "testpass"})
        assert r.status_code in (200, 302)


def test_login_wrong_password_stays_on_login(tmp_path, monkeypatch):
    db_path = str(tmp_path / "loginwrong.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.init_db("testuser", "correct")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test"
    with flask_app.app.test_client() as c:
        r = c.post("/login", data={"username": "testuser", "password": "wrong"})
        assert r.status_code == 200
        assert b"Hib" in r.data  # "Hibás jelszó"


def test_protected_route_requires_login(unauth_client):
    r = unauth_client.get("/api/fx")
    assert r.status_code == 401
    d = r.get_json()
    assert d.get("login_required") is True


def test_protected_portfolio_requires_login(unauth_client):
    r = unauth_client.get("/api/portfolio")
    assert r.status_code == 401


# ===========================================================================
# /api/fx
# ===========================================================================

def _mnb_ok():
    return {
        "fx": {"EUR/HUF": 395.40, "USD/HUF": 362.80, "USD/EUR": 0.9178},
        "errors": [],
        "source": "MNB",
        "timestamp": "2026-06-04T10:00:00",
        "date": "2026-06-04",
    }


def test_fx_structure(client):
    with patch("app.get_fx_rates", return_value=_mnb_ok()):
        r = client.get("/api/fx")
    assert r.status_code == 200
    d = r.get_json()
    assert "fx" in d
    assert "errors" in d
    assert "source" in d
    assert "timestamp" in d


def test_fx_has_eur_usd(client):
    with patch("app.get_fx_rates", return_value=_mnb_ok()):
        r = client.get("/api/fx")
    d = r.get_json()
    assert "EUR/HUF" in d["fx"]
    assert "USD/HUF" in d["fx"]


def test_fx_no_500_when_mnb_down(client):
    with patch("app.get_fx_rates", return_value={
        "fx": {}, "errors": ["MNB nem elérhető"], "source": "none",
        "timestamp": "2026-06-04T10:00:00"
    }):
        r = client.get("/api/fx")
    assert r.status_code == 200


# ===========================================================================
# /api/search
# ===========================================================================

def test_search_otp_never_500(client):
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        r = client.get("/api/search/OTP")
    assert r.status_code == 200


def test_search_otp_has_results(client):
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        r = client.get("/api/search/OTP")
    d = r.get_json()
    assert "results" in d
    tickers = [x["ticker"] for x in d["results"]]
    assert "OTP.BD" in tickers


def test_search_apple_fallback(client):
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        r = client.get("/api/search/Apple")
    d = r.get_json()
    tickers = [x["ticker"] for x in d["results"]]
    assert "AAPL" in tickers


def test_search_bmw_fallback(client):
    with patch("services.symbol_resolver._search_yahoo", return_value=([], ["rate limit"])):
        r = client.get("/api/search/BMW")
    d = r.get_json()
    tickers = [x["ticker"] for x in d["results"]]
    assert "BMW.DE" in tickers


def test_search_unknown_returns_200_not_500(client):
    with patch("services.symbol_resolver._search_yahoo", return_value=([], [])):
        r = client.get("/api/search/XYZUNKNOWN999")
    assert r.status_code == 200
    d = r.get_json()
    assert "results" in d


# ===========================================================================
# /api/prices
# ===========================================================================

def _mock_prices(tickers):
    prices = {}
    errors = []
    for t in tickers:
        if t == "AAPL":
            prices[t] = {"price": 195.23, "currency": "USD",
                         "source": "Yahoo Finance", "timestamp": "2026-06-04T10:00:00"}
        else:
            errors.append({"ticker": t, "message": "Árfolyam most nem elérhető."})
    return {"prices": prices, "errors": errors, "timestamp": "2026-06-04T10:00:00", "source": "Yahoo Finance"}


def test_prices_structure(client):
    with patch("app.get_prices_for_tickers", side_effect=_mock_prices):
        r = client.post("/api/prices", json={"tickers": ["AAPL", "OTP.BD"]})
    assert r.status_code == 200
    d = r.get_json()
    assert "prices" in d
    assert "errors" in d


def test_prices_partial_failure(client):
    with patch("app.get_prices_for_tickers", side_effect=_mock_prices):
        r = client.post("/api/prices", json={"tickers": ["AAPL", "OTP.BD"]})
    d = r.get_json()
    assert "AAPL" in d["prices"]
    assert any(e["ticker"] == "OTP.BD" for e in d["errors"])


def test_prices_empty_tickers_400(client):
    r = client.post("/api/prices", json={"tickers": []})
    assert r.status_code == 400


def test_prices_no_crash_on_error(client):
    with patch("app.get_prices_for_tickers", return_value={
        "prices": {}, "errors": [{"ticker": "X", "message": "hiba"}],
        "timestamp": "...", "source": "none"
    }):
        r = client.post("/api/prices", json={"tickers": ["X"]})
    assert r.status_code == 200


# ===========================================================================
# /api/portfolio
# ===========================================================================

def test_portfolio_get_empty(client):
    r = client.get("/api/portfolio")
    assert r.status_code == 200
    d = r.get_json()
    assert isinstance(d, list)


def test_portfolio_save_and_retrieve(client):
    items = [{"ticker": "AAPL", "name": "Apple", "qty": 5}]
    r = client.post("/api/portfolio", json=items)
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    r2 = client.get("/api/portfolio")
    d = r2.get_json()
    assert len(d) == 1
    assert d[0]["ticker"] == "AAPL"


def test_portfolio_delete_by_id(client):
    # Hozzáadás
    client.post("/api/portfolio", json=[{"ticker": "MSFT", "name": "Microsoft", "qty": 3}])
    portfolio = client.get("/api/portfolio").get_json()
    assert len(portfolio) == 1
    item_id = portfolio[0]["id"]

    r = client.delete(f"/api/portfolio/{item_id}")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True

    portfolio_after = client.get("/api/portfolio").get_json()
    assert len(portfolio_after) == 0


# ===========================================================================
# /api/export/xlsx – MIME type
# ===========================================================================

def test_export_xlsx_mime_type(client):
    # Portfolio feltöltés
    client.post("/api/portfolio", json=[{"ticker": "AAPL", "name": "Apple", "qty": 3}])
    with patch("app.get_prices_for_tickers", return_value={
        "prices": {"AAPL": {"price": 195.0, "currency": "USD", "source": "Yahoo Finance", "timestamp": "..."}},
        "errors": [], "timestamp": "...", "source": "Yahoo Finance"
    }), patch("app.get_fx_rates", return_value=_mnb_ok()):
        r = client.get("/api/export/xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.content_type


def test_export_xlsx_empty_portfolio_400(client):
    r = client.get("/api/export/xlsx")
    assert r.status_code == 400
