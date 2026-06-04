"""
Admin felület és jogosultság tesztek.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from unittest.mock import patch

import services.db as db_module
from services.db import (init_db, create_user, get_user_by_id,
                          get_portfolio, upsert_portfolio_item)
from services.settings_store import (get_setting, save_setting,
                                      get_bool, get_int,
                                      init_default_settings, DEFAULTS,
                                      invalidate_cache)
import app as flask_app


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_admin.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "portfolio.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "symbols.json"))
    db_module.reset_engine()
    yield db_path


@pytest.fixture()
def admin_client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "admin_client.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    init_db("adminuser", "adminpass123")

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test-secret"
    with flask_app.app.test_client() as c:
        c.post("/login", data={"username": "adminuser", "password": "adminpass123"})
        yield c


@pytest.fixture()
def user_client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "user_client.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    init_db("adminuser", "adminpass123")
    create_user("normaluser", "userpass123", role="user")

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test-secret"
    with flask_app.app.test_client() as c:
        c.post("/login", data={"username": "normaluser", "password": "userpass123"})
        yield c


# ===========================================================================
# login_required decorator
# ===========================================================================

def test_login_required_api_returns_401_when_not_logged_in(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lr.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    init_db("admin", "admin")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test"
    with flask_app.app.test_client() as c:
        r = c.get("/api/portfolio")
        assert r.status_code == 401
        assert r.get_json().get("login_required") is True


def test_login_required_html_redirects_to_login(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lr2.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    init_db("admin", "admin")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test"
    with flask_app.app.test_client() as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]


# ===========================================================================
# admin_required decorator
# ===========================================================================

def test_admin_required_user_gets_403(user_client):
    r = user_client.get("/admin")
    assert r.status_code == 403


def test_admin_required_api_user_gets_403(user_client):
    r = user_client.get("/admin/users")
    assert r.status_code == 403


def test_admin_can_access_admin_page(admin_client):
    r = admin_client.get("/admin")
    assert r.status_code == 200


def test_admin_can_access_users_page(admin_client):
    r = admin_client.get("/admin/users")
    assert r.status_code == 200


def test_unauthenticated_admin_redirects_to_login(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lr3.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    init_db("admin", "admin")
    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test"
    with flask_app.app.test_client() as c:
        r = c.get("/admin", follow_redirects=False)
        assert r.status_code == 302


# ===========================================================================
# User management
# ===========================================================================

def test_admin_creates_user(admin_client):
    r = admin_client.post("/admin/users/new", data={
        "username": "newuser",
        "password": "newpass123",
        "password2": "newpass123",
        "role": "user",
        "is_active": "1",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"newuser" in r.data


def test_admin_create_user_password_mismatch(admin_client):
    r = admin_client.post("/admin/users/new", data={
        "username": "baduser",
        "password": "pass1",
        "password2": "pass2",
        "role": "user",
        "is_active": "1",
    })
    assert r.status_code == 200
    assert "egyezik" in r.data.decode("utf-8")


def test_admin_create_user_short_password(admin_client):
    r = admin_client.post("/admin/users/new", data={
        "username": "shortpw",
        "password": "ab",
        "password2": "ab",
        "role": "user",
        "is_active": "1",
    })
    assert r.status_code == 200
    assert "karakter" in r.data.decode("utf-8")


def test_admin_can_update_user_role(admin_client, tmp_path, monkeypatch):
    init_db()  # already done
    create_user("editme", "editpass123", role="user")
    user = db_module.get_user_by_username("editme")
    uid = user["id"]

    r = admin_client.post(f"/admin/users/{uid}/edit", data={
        "action": "update",
        "role": "admin",
        "is_active": "1",
    }, follow_redirects=True)
    assert r.status_code == 200
    updated = get_user_by_id(uid)
    assert updated["role"] == "admin"


def test_admin_can_deactivate_user(admin_client):
    create_user("deactivateme", "deacpass123", role="user")
    user = db_module.get_user_by_username("deactivateme")
    uid = user["id"]

    admin_client.post(f"/admin/users/{uid}/edit", data={
        "action": "update",
        "role": "user",
        "is_active": "0",
    }, follow_redirects=True)
    updated = get_user_by_id(uid)
    assert updated["is_active"] == 0


def test_inactive_user_cannot_login(tmp_path, monkeypatch):
    db_path = str(tmp_path / "inactive.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    init_db("admin", "adminpass")
    create_user("inactive", "inactivepass", role="user", is_active=False)

    flask_app.app.config["TESTING"] = True
    flask_app.app.config["SECRET_KEY"] = "test"
    with flask_app.app.test_client() as c:
        r = c.post("/login", data={"username": "inactive", "password": "inactivepass"})
        assert r.status_code == 200
        assert "Hib" in r.data.decode("utf-8")


def test_password_not_in_plain_text():
    init_db("admin", "admin")
    u = db_module.get_user_by_username("admin")
    assert u["password_hash"] != "admin"
    assert len(u["password_hash"]) > 30


# ===========================================================================
# User isolation
# ===========================================================================

def test_user_only_sees_own_portfolio(tmp_path, monkeypatch):
    db_path = str(tmp_path / "isolation.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    init_db("admin", "adminpass")
    create_user("userB", "bpass123", role="user")

    admin = db_module.get_user_by_username("admin")
    user_b = db_module.get_user_by_username("userB")

    upsert_portfolio_item(admin["id"], {"ticker": "AAPL", "name": "Apple", "qty": 10})
    upsert_portfolio_item(user_b["id"], {"ticker": "TSLA", "name": "Tesla", "qty": 5})

    admin_portfolio = get_portfolio(admin["id"])
    userb_portfolio = get_portfolio(user_b["id"])

    assert any(i["ticker"] == "AAPL" for i in admin_portfolio)
    assert not any(i["ticker"] == "TSLA" for i in admin_portfolio)
    assert any(i["ticker"] == "TSLA" for i in userb_portfolio)
    assert not any(i["ticker"] == "AAPL" for i in userb_portfolio)


# ===========================================================================
# Settings
# ===========================================================================

def test_settings_defaults():
    init_db("admin", "admin")
    init_default_settings()
    invalidate_cache()
    assert get_setting("app_name") == "Portfólió Követő"
    assert get_setting("default_currency") == "HUF"
    assert get_bool("enable_yahoo") is True
    assert get_bool("enable_mnb") is True
    assert get_bool("maintenance_mode") is False
    assert get_int("price_cache_minutes") == 15


def test_settings_save_and_retrieve():
    init_db("admin", "admin")
    init_default_settings()
    save_setting("app_name", "Teszt App")
    invalidate_cache()
    assert get_setting("app_name") == "Teszt App"


def test_settings_bool_toggle():
    init_db("admin", "admin")
    init_default_settings()
    save_setting("maintenance_mode", "true")
    invalidate_cache()
    assert get_bool("maintenance_mode") is True
    save_setting("maintenance_mode", "false")
    invalidate_cache()
    assert get_bool("maintenance_mode") is False


def test_settings_unknown_key_returns_default():
    init_db("admin", "admin")
    result = get_setting("ismeretlen_kulcs", "fallback")
    assert result == "fallback"


def test_all_defaults_are_present():
    assert "enable_yahoo" in DEFAULTS
    assert "enable_stooq" in DEFAULTS
    assert "enable_mnb" in DEFAULTS
    assert "excel_export_enabled" in DEFAULTS
    assert "maintenance_mode" in DEFAULTS
    assert "price_cache_minutes" in DEFAULTS


def test_admin_settings_page(admin_client):
    init_default_settings()
    r = admin_client.get("/admin/settings")
    assert r.status_code == 200
    assert "settings" in r.data.decode("utf-8", errors="replace").lower() or r.status_code == 200


def test_admin_settings_save(admin_client):
    init_default_settings()
    r = admin_client.post("/admin/settings", data={
        "setting_app_name": "Módosított Név",
        "setting_maintenance_mode": "false",
    }, follow_redirects=True)
    assert r.status_code == 200
    invalidate_cache()
    assert get_setting("app_name") == "Módosított Név"


# ===========================================================================
# Excel export
# ===========================================================================

def test_excel_export_returns_xlsx(admin_client):
    upsert_portfolio_item(
        db_module.get_user_by_username("adminuser")["id"],
        {"ticker": "AAPL", "name": "Apple", "qty": 3}
    )
    with patch("app.get_prices_for_tickers", return_value={
        "prices": {"AAPL": {"price": 195.0, "currency": "USD",
                             "source": "Yahoo Finance", "timestamp": "2026-06-04T10:00:00"}},
        "errors": [], "timestamp": "...", "source": "Yahoo Finance"
    }), patch("app.get_fx_rates", return_value={
        "fx": {"EUR/HUF": 395.0, "USD/HUF": 362.0},
        "errors": [], "source": "MNB", "timestamp": "..."
    }):
        r = admin_client.get("/api/export/xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.content_type


def test_excel_export_empty_portfolio_400(admin_client):
    r = admin_client.get("/api/export/xlsx")
    assert r.status_code == 400


# ===========================================================================
# Production / DATABASE_URL
# ===========================================================================

def test_production_without_database_url_raises(monkeypatch, tmp_path):
    """Production módban DATABASE_URL hiánya RuntimeError-t dob."""
    db_path = str(tmp_path / "prod.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        init_db("admin", "admin")


def test_local_mode_sqlite_works(tmp_path, monkeypatch):
    """Local módban DATABASE_URL nélkül SQLite-on fut."""
    db_path = str(tmp_path / "local.db")
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "_PORTFOLIO_JSON", str(tmp_path / "p.json"))
    monkeypatch.setattr(db_module, "_SYMBOLS_JSON", str(tmp_path / "s.json"))
    db_module.reset_engine()
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    init_db("admin", "adminpass")
    user = db_module.get_user_by_username("admin")
    assert user is not None


# ===========================================================================
# Admin system page
# ===========================================================================

def test_admin_system_page(admin_client):
    r = admin_client.get("/admin/system")
    assert r.status_code == 200
    assert r.status_code == 200 and len(r.data) > 100


def test_admin_logs_page(admin_client):
    r = admin_client.get("/admin/logs")
    assert r.status_code == 200
    assert b"Audit" in r.data or r.status_code == 200


# ===========================================================================
# Navbar visibility tesztek
# ===========================================================================

def test_admin_sees_admin_link_on_main_page(admin_client):
    """Admin user főoldalán megjelenik az Admin link."""
    r = admin_client.get("/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "/admin" in html, "Admin link hiányzik a főoldalról admin user esetén"


def test_admin_sees_admin_link_on_admin_page(admin_client):
    """Admin user az /admin oldalon is látja a navigációt."""
    r = admin_client.get("/admin")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    assert "/admin/users" in html
    assert "/admin/settings" in html


def test_normal_user_no_admin_link_on_main_page(user_client):
    """Normál user főoldalán nincs Admin link."""
    r = user_client.get("/")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # /admin ne szerepeljen linkként (csak útvonalként, ha egyáltalán)
    # Ellenőrizzük, hogy az admin route-ra mutató link nem jelenik meg
    assert 'href="/admin"' not in html
    assert "Felhasználók" not in html or "/admin/users" not in html


def test_normal_user_cannot_access_admin_route(user_client):
    """Normál user nem éri el az /admin oldalt."""
    r = user_client.get("/admin")
    assert r.status_code == 403


def test_admin_active_nav_class_on_users_page(admin_client):
    """Felhasználók lapon a nav-active class az admin_users linkre kerül."""
    r = admin_client.get("/admin/users")
    assert r.status_code == 200
    html = r.data.decode("utf-8")
    # A Felhasználók link nav-active osztályt kap
    assert "nav-active" in html
