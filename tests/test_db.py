"""
SQLite adatbázis réteg tesztek.
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import services.db as db_module
from services.db import (
    init_db, get_user_by_username, verify_password,
    get_portfolio, upsert_portfolio_item, update_portfolio_qty,
    delete_portfolio_item, delete_portfolio_item_by_id,
    save_full_portfolio, upsert_symbol_cache, get_all_symbols,
)


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    """Minden teszt saját ideiglenes DB-t kap; engine cache törölve."""
    db_path = str(tmp_path / "test.db")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    db_module.reset_engine()
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    # Ne próbáljon JSON fájlokat migrálni
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "portfolio.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "symbols_cache.json"))
    yield db_path


# ===========================================================================
# init_db
# ===========================================================================

def test_init_creates_tables():
    init_db("testuser", "testpass")
    user = get_user_by_username("testuser")
    assert user is not None
    assert user["username"] == "testuser"


def test_init_creates_admin_user():
    init_db("admin", "secret123")
    user = get_user_by_username("admin")
    assert user is not None


def test_init_idempotent():
    init_db("admin", "pass1")
    init_db("admin", "pass1")  # Második hívás ne dobjon hibát
    user = get_user_by_username("admin")
    assert user is not None


# ===========================================================================
# User management
# ===========================================================================

def test_verify_password_correct():
    init_db("zoltan", "titok123")
    user = verify_password("zoltan", "titok123")
    assert user is not None
    assert user["username"] == "zoltan"


def test_verify_password_wrong():
    init_db("zoltan", "titok123")
    user = verify_password("zoltan", "rossz")
    assert user is None


def test_verify_password_unknown_user():
    init_db("zoltan", "titok123")
    user = verify_password("nemletezik", "bármi")
    assert user is None


def test_password_not_stored_in_plain():
    init_db("zoltan", "titkos")
    user = get_user_by_username("zoltan")
    assert user["password_hash"] != "titkos"
    assert len(user["password_hash"]) > 20  # hash-olt


# ===========================================================================
# Portfolio CRUD
# ===========================================================================

def test_get_portfolio_empty():
    init_db("user1", "pass")
    user = get_user_by_username("user1")
    portfolio = get_portfolio(user["id"])
    assert portfolio == []


def test_upsert_adds_item():
    init_db("user1", "pass")
    uid = get_user_by_username("user1")["id"]
    saved = upsert_portfolio_item(uid, {"ticker": "AAPL", "name": "Apple", "qty": 10})
    assert saved["ticker"] == "AAPL"
    assert saved["qty"] == 10.0
    assert saved["id"] is not None
    portfolio = get_portfolio(uid)
    assert len(portfolio) == 1


def test_upsert_updates_existing():
    init_db("user1", "pass")
    uid = get_user_by_username("user1")["id"]
    upsert_portfolio_item(uid, {"ticker": "AAPL", "name": "Apple", "qty": 10})
    upsert_portfolio_item(uid, {"ticker": "AAPL", "name": "Apple Inc.", "qty": 15})
    portfolio = get_portfolio(uid)
    assert len(portfolio) == 1
    assert portfolio[0]["qty"] == 15.0
    assert portfolio[0]["name"] == "Apple Inc."


def test_update_qty():
    init_db("user1", "pass")
    uid = get_user_by_username("user1")["id"]
    upsert_portfolio_item(uid, {"ticker": "MSFT", "name": "Microsoft", "qty": 5})
    ok = update_portfolio_qty(uid, "MSFT", 12)
    assert ok
    portfolio = get_portfolio(uid)
    assert portfolio[0]["qty"] == 12.0


def test_delete_by_ticker():
    init_db("user1", "pass")
    uid = get_user_by_username("user1")["id"]
    upsert_portfolio_item(uid, {"ticker": "TSLA", "name": "Tesla", "qty": 3})
    ok = delete_portfolio_item(uid, "TSLA")
    assert ok
    assert get_portfolio(uid) == []


def test_delete_by_id():
    init_db("user1", "pass")
    uid = get_user_by_username("user1")["id"]
    saved = upsert_portfolio_item(uid, {"ticker": "NVDA", "name": "Nvidia", "qty": 2})
    item_id = saved["id"]
    ok = delete_portfolio_item_by_id(uid, item_id)
    assert ok
    assert get_portfolio(uid) == []


def test_save_full_portfolio_replaces():
    init_db("user1", "pass")
    uid = get_user_by_username("user1")["id"]
    upsert_portfolio_item(uid, {"ticker": "OLD", "name": "Old Item", "qty": 1})
    save_full_portfolio(uid, [
        {"ticker": "AAPL", "name": "Apple", "qty": 5},
        {"ticker": "MSFT", "name": "Microsoft", "qty": 3},
    ])
    portfolio = get_portfolio(uid)
    tickers = {i["ticker"] for i in portfolio}
    assert tickers == {"AAPL", "MSFT"}
    assert "OLD" not in tickers


# ===========================================================================
# User isolation
# ===========================================================================

def test_user_a_cannot_see_user_b_portfolio(tmp_path, monkeypatch):
    import services.db as db_mod
    db_path = str(tmp_path / "isolation.db")
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_mod, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_mod.reset_engine()

    # Két user létrehozása SQLAlchemy API-val
    init_db("userA", "passA")
    db_mod.create_user("userB", "passB", role="user")

    uid_a = db_mod.get_user_by_username("userA")["id"]
    uid_b = db_mod.get_user_by_username("userB")["id"]

    upsert_portfolio_item(uid_a, {"ticker": "AAPL", "name": "Apple", "qty": 10})
    upsert_portfolio_item(uid_b, {"ticker": "TSLA", "name": "Tesla", "qty": 5})

    portfolio_a = get_portfolio(uid_a)
    portfolio_b = get_portfolio(uid_b)

    assert len(portfolio_a) == 1
    assert portfolio_a[0]["ticker"] == "AAPL"
    assert len(portfolio_b) == 1
    assert portfolio_b[0]["ticker"] == "TSLA"


# ===========================================================================
# JSON migráció
# ===========================================================================

def test_portfolio_json_migration(tmp_path, monkeypatch):
    import services.db as db_mod
    db_path = str(tmp_path / "migr.db")
    pjson = tmp_path / "portfolio.json"
    pjson.write_text(
        json.dumps([
            {"ticker": "OTP.BD", "name": "OTP Bank", "qty": 10},
            {"ticker": "AAPL", "name": "Apple", "qty": 3},
        ]),
        encoding="utf-8"
    )
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "_PORTFOLIO_JSON", str(pjson))
    db_mod.reset_engine()
    monkeypatch.setattr(db_mod, "_SYMBOLS_JSON", str(tmp_path / "s.json"))

    init_db("admin", "admin")
    uid = db_mod.get_user_by_username("admin")["id"]
    portfolio = get_portfolio(uid)
    tickers = {i["ticker"] for i in portfolio}
    assert "OTP.BD" in tickers
    assert "AAPL" in tickers


def test_migration_not_run_twice(tmp_path, monkeypatch):
    import services.db as db_mod
    db_path = str(tmp_path / "once.db")
    pjson = tmp_path / "portfolio.json"
    pjson.write_text(json.dumps([{"ticker": "MSFT", "name": "Microsoft", "qty": 2}]), encoding="utf-8")
    monkeypatch.setattr(db_mod, "DB_PATH", db_path)
    monkeypatch.setattr(db_mod, "_PORTFOLIO_JSON", str(pjson))
    monkeypatch.setattr(db_mod, "_SYMBOLS_JSON", str(tmp_path / "s.json"))

    init_db("admin", "admin")
    uid = db_mod.get_user_by_username("admin")["id"]
    upsert_portfolio_item(uid, {"ticker": "EXTRA", "name": "Extra", "qty": 99})
    # Második init ne törölje az EXTRA-t
    init_db("admin", "admin")
    portfolio = get_portfolio(uid)
    tickers = {i["ticker"] for i in portfolio}
    assert "EXTRA" in tickers


# ===========================================================================
# Symbols cache
# ===========================================================================

def test_upsert_symbol_and_retrieve():
    init_db("admin", "admin")
    upsert_symbol_cache({"ticker": "BMW.DE", "name": "BMW AG", "currency": "EUR",
                          "exchange": "XETRA", "query_aliases": ["bmw"]})
    symbols = get_all_symbols()
    tickers = [s["ticker"] for s in symbols]
    assert "BMW.DE" in tickers


def test_symbol_upsert_updates():
    init_db("admin", "admin")
    upsert_symbol_cache({"ticker": "AAPL", "name": "Apple Inc.", "currency": "USD"})
    upsert_symbol_cache({"ticker": "AAPL", "name": "Apple (updated)", "currency": "USD"})
    symbols = get_all_symbols()
    aapl = next(s for s in symbols if s["ticker"] == "AAPL")
    assert aapl["name"] == "Apple (updated)"
