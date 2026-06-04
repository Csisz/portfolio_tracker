"""
Zoltán Portfólió Követő
=======================
Indítás: python app.py
Majd böngészőben: http://localhost:5000

Belépési adatok (fejlesztésben): admin / admin
Éles használathoz állítsd be a .env fájlban:
  SECRET_KEY=...
  PORTFOLIO_USERNAME=...
  PORTFOLIO_PASSWORD=...
  DATABASE_URL=postgresql://...
  APP_ENV=production
"""

import functools
import io
import logging
import os
import secrets
import sys
from datetime import datetime

from flask import (Flask, Response, flash, jsonify, redirect, render_template,
                   request, send_file, session, url_for)

import services.db as db
from services import symbol_resolver
from services.db import (create_user, delete_portfolio_item_by_id,
                          get_all_users, get_audit_logs, get_portfolio,
                          get_stats, get_user_by_id, init_db, log_event,
                          save_full_portfolio, set_user_password,
                          upsert_portfolio_item, upsert_symbol_cache,
                          update_item_last_price, update_last_login,
                          update_portfolio_qty, update_user, verify_password)
from services.fx import get_fx_rates
from services.settings_store import (get_all_settings_with_defaults,
                                      get_bool, init_default_settings,
                                      save_setting)
from services.stocks import get_prices_for_tickers, get_ticker_info

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# .env betöltés
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)

_sk = os.environ.get("SECRET_KEY", "").strip()
_app_env = os.environ.get("APP_ENV", "").lower()

if not _sk:
    if _app_env == "production":
        logger.error("SECRET_KEY nincs beállítva production módban! Az alkalmazás nem indul.")
        sys.exit(1)
    _sk = secrets.token_hex(32)
    logger.warning(
        "SECRET_KEY nincs beállítva. Ideiglenes kulcs aktív – session-ök újraindításkor lejárnak. "
        "Generálj: python -c \"import secrets; print(secrets.token_hex(32))\""
    )

app.secret_key = _sk

# ---------------------------------------------------------------------------
# Context processor – globálisan elérhető változók minden template-ben
# ---------------------------------------------------------------------------

@app.context_processor
def inject_user_context():
    return {
        "current_username": session.get("username", ""),
        "current_role": session.get("role", "user"),
        "is_admin": session.get("role") == "admin",
    }


# Cookie biztonság
if _app_env == "production":
    app.config["SESSION_COOKIE_SECURE"] = True
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
else:
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ---------------------------------------------------------------------------
# Auth decoratorok
# ---------------------------------------------------------------------------

def login_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Bejelentkezés szükséges", "login_required": True}), 401
            return redirect(url_for("login_page"))

        # Karbantartási mód: csak admin léphet be
        if get_bool("maintenance_mode") and session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Karbantartás folyamatban. Próbáld később."}), 503
            return render_template("maintenance.html"), 503

        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            if request.path.startswith("/api/"):
                return jsonify({"error": "Bejelentkezés szükséges"}), 401
            return redirect(url_for("login_page"))
        if session.get("role") != "admin":
            if request.path.startswith("/api/"):
                return jsonify({"error": "Nincs jogosultságod ehhez a művelethez."}), 403
            return render_template("403.html"), 403
        return f(*args, **kwargs)
    return decorated


def current_user_id() -> int:
    return session["user_id"]


def current_username() -> str:
    return session.get("username", "")


def current_role() -> str:
    return session.get("role", "user")


def _client_ip() -> str:
    return request.headers.get("X-Forwarded-For", request.remote_addr or "")


# ---------------------------------------------------------------------------
# Login / Logout
# ---------------------------------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if "user_id" in session:
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = verify_password(username, password)
        if user:
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user.get("role", "user")
            update_last_login(user["id"])
            log_event(user["id"], "login", f"Sikeres belépés: {username}", _client_ip())
            return redirect(url_for("index"))
        else:
            log_event(None, "login_failed", f"Sikertelen belépés: {username}", _client_ip())
            error = "Hibás felhasználónév vagy jelszó, vagy az account inaktív."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    uid = session.get("user_id")
    uname = session.get("username", "")
    log_event(uid, "logout", f"Kilépés: {uname}", _client_ip())
    session.clear()
    return redirect(url_for("login_page"))


# ---------------------------------------------------------------------------
# Favicon
# ---------------------------------------------------------------------------

@app.route("/favicon.ico")
def favicon():
    return Response(status=204)


# ---------------------------------------------------------------------------
# Főoldal
# ---------------------------------------------------------------------------

@app.route("/")
@login_required
def index():
    portfolio = get_portfolio(current_user_id())
    return render_template(
        "index.html",
        portfolio=portfolio,
        username=current_username(),
        role=current_role(),
    )


# ---------------------------------------------------------------------------
# /api/fx
# ---------------------------------------------------------------------------

@app.route("/api/fx")
@login_required
def get_fx():
    if not get_bool("enable_mnb"):
        return jsonify({"fx": {}, "errors": ["MNB lekérés kikapcsolva."], "source": "disabled", "timestamp": _ts()})
    result = get_fx_rates()
    result.pop("_raw", None)
    return jsonify(result)


# ---------------------------------------------------------------------------
# /api/prices
# ---------------------------------------------------------------------------

@app.route("/api/prices", methods=["POST"])
@login_required
def api_prices():
    data = request.get_json(silent=True) or {}
    tickers = data.get("tickers", [])
    if not isinstance(tickers, list) or not tickers:
        return jsonify({"error": "Nincs ticker megadva", "prices": {}, "errors": []}), 400

    result = get_prices_for_tickers(tickers)

    uid = current_user_id()
    now = _ts()
    for ticker, pdata in result.get("prices", {}).items():
        if not pdata.get("stale"):
            try:
                update_item_last_price(uid, ticker, pdata["price"], pdata["currency"], pdata["source"], pdata.get("timestamp", now))
            except Exception:
                pass

    return jsonify(result)


# ---------------------------------------------------------------------------
# /api/portfolio
# ---------------------------------------------------------------------------

@app.route("/api/portfolio", methods=["GET"])
@login_required
def api_portfolio_get():
    return jsonify(get_portfolio(current_user_id()))


@app.route("/api/portfolio", methods=["POST"])
@login_required
def api_portfolio_save():
    data = request.get_json(silent=True)
    if not isinstance(data, list):
        return jsonify({"ok": False, "error": "Lista szükséges"}), 400
    try:
        save_full_portfolio(current_user_id(), data)
        return jsonify({"ok": True})
    except Exception as e:
        logger.error("Portfolio mentési hiba: %s", e)
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/portfolio/<int:item_id>", methods=["DELETE"])
@login_required
def api_portfolio_delete(item_id: int):
    ok = delete_portfolio_item_by_id(current_user_id(), item_id)
    if ok:
        log_event(current_user_id(), "portfolio_delete", f"id={item_id}", _client_ip())
    return jsonify({"ok": ok})


@app.route("/api/portfolio/<int:item_id>", methods=["PUT", "PATCH"])
@login_required
def api_portfolio_update(item_id: int):
    data = request.get_json(silent=True) or {}
    qty = data.get("qty")
    if qty is None:
        return jsonify({"ok": False, "error": "qty mező szükséges"}), 400
    try:
        qty = float(qty)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Érvénytelen darabszám"}), 400

    portfolio = get_portfolio(current_user_id())
    item = next((i for i in portfolio if i["id"] == item_id), None)
    if not item:
        return jsonify({"ok": False, "error": "Elem nem található"}), 404

    ok = update_portfolio_qty(current_user_id(), item["ticker"], qty)
    if ok:
        log_event(current_user_id(), "portfolio_qty_update",
                  f"ticker={item['ticker']} qty={qty}", _client_ip())
    return jsonify({"ok": ok})


# ---------------------------------------------------------------------------
# /api/search
# ---------------------------------------------------------------------------

@app.route("/api/search/<path:query>")
@login_required
def api_search(query):
    result = symbol_resolver.search(query.strip())
    return jsonify(result)


# ---------------------------------------------------------------------------
# /api/add_manual
# ---------------------------------------------------------------------------

@app.route("/api/add_manual", methods=["POST"])
@login_required
def api_add_manual():
    data = request.get_json(silent=True) or {}
    ticker = str(data.get("ticker", "")).strip().upper()
    name = str(data.get("name", "")).strip() or ticker
    qty_raw = data.get("qty", 1)

    if not ticker:
        return jsonify({"ok": False, "error": "A ticker mező kötelező."}), 400
    try:
        qty = float(qty_raw)
        if qty <= 0:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Érvénytelen darabszám."}), 400

    currency = str(data.get("currency", "")).strip().upper() or None
    exchange = str(data.get("exchange", "")).strip()

    validated = False
    validation_warning = None
    info = None
    try:
        info = get_ticker_info(ticker)
        if info:
            validated = True
            if not currency:
                currency = info.get("currency")
            if not exchange:
                exchange = info.get("exchange", "")
            if name == ticker and info.get("name"):
                name = info["name"]
    except Exception as e:
        logger.warning("Kézi ticker validálás hiba %s: %s", ticker, e)

    if not validated:
        validation_warning = "Ticker most nem ellenőrizhető, de elmentve. Árfolyam később próbálható."

    saved = upsert_portfolio_item(current_user_id(), {
        "ticker": ticker, "name": name, "qty": qty,
        "currency": currency, "exchange": exchange,
        "source": "manual", "manually_added": True,
    })

    sym = {
        "ticker": ticker, "name": name, "currency": currency,
        "exchange": exchange, "source": "manual",
        "query_aliases": [ticker.lower(), name.lower()],
        "last_price": info.get("last_price") if info else None,
        "last_price_time": _ts() if info else None,
    }
    upsert_symbol_cache(sym)
    symbol_resolver.upsert_symbol(sym)

    log_event(current_user_id(), "portfolio_add",
              f"ticker={ticker} qty={qty} manual=true", _client_ip())

    resp = {
        "ok": True, "ticker": ticker, "name": name,
        "currency": currency, "exchange": exchange,
        "validated": validated, "id": saved.get("id"),
    }
    if validation_warning:
        resp["warning"] = validation_warning
    return jsonify(resp)


# ---------------------------------------------------------------------------
# /api/symbols/recent
# ---------------------------------------------------------------------------

@app.route("/api/symbols/recent")
@login_required
def api_symbols_recent():
    db_symbols = db.get_all_symbols(limit=20)
    if db_symbols:
        return jsonify({"symbols": db_symbols})
    symbols = symbol_resolver.get_cached_symbols()
    symbols_sorted = sorted(symbols, key=lambda s: s.get("last_seen", ""), reverse=True)
    return jsonify({"symbols": symbols_sorted[:20]})


# ---------------------------------------------------------------------------
# /api/export/xlsx
# ---------------------------------------------------------------------------

@app.route("/api/export/xlsx")
@login_required
def api_export_xlsx():
    if not get_bool("excel_export_enabled"):
        return jsonify({"error": "Excel export ki van kapcsolva."}), 403

    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        return jsonify({"error": "openpyxl nincs telepítve."}), 500

    uid = current_user_id()
    portfolio = get_portfolio(uid)
    if not portfolio:
        return jsonify({"error": "Üres portfólió"}), 400

    tickers = [i["ticker"] for i in portfolio]
    price_result = get_prices_for_tickers(tickers)
    prices = price_result.get("prices", {})

    fx_result = get_fx_rates()
    fx = fx_result.get("fx", {})

    def to_huf(val, currency):
        if val is None:
            return None
        c = (currency or "").upper()
        if c == "HUF":
            return round(val, 2)
        if c in ("GBP", "GBX") and fx.get("GBP/HUF"):
            return round(val * fx["GBP/HUF"] / (100 if c == "GBX" else 1), 2)
        rate = fx.get(f"{c}/HUF")
        return round(val * rate, 2) if rate else None

    wb = Workbook()
    ws = wb.active
    ws.title = "Portfólió"

    headers = [
        "Részvény neve", "Ticker", "Tőzsde", "Darab",
        "Aktuális árfolyam", "Deviza", "Forrás",
        "Érték (saját deviza)", "Érték (HUF)", "Érték (EUR)", "Érték (USD)",
        "Árfolyam időpontja", "Elavult ár?", "Hozzáadás módja", "Export időpontja",
    ]
    hdr_font = Font(bold=True, color="FFFFFF")
    hdr_fill = PatternFill("solid", fgColor="1A1A2E")
    ws.append(headers)
    for col_idx, _ in enumerate(headers, 1):
        cell = ws.cell(1, col_idx)
        cell.font = hdr_font
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 36
    ws.freeze_panes = "A2"

    export_ts = datetime.now().strftime("%Y.%m.%d %H:%M")
    total_huf = 0.0

    for item in portfolio:
        ticker = item["ticker"]
        p = prices.get(ticker)
        price = p["price"] if p else (item.get("last_price"))
        currency = (p["currency"] if p else None) or item.get("currency") or ""
        val_own = round(price * item["qty"], 4) if price is not None else None
        val_huf = to_huf(val_own, currency)
        val_eur = round(val_huf / fx["EUR/HUF"], 2) if val_huf and fx.get("EUR/HUF") else None
        val_usd = round(val_huf / fx["USD/HUF"], 2) if val_huf and fx.get("USD/HUF") else None
        if val_huf:
            total_huf += val_huf
        ws.append([
            item.get("name", ticker), ticker, item.get("exchange", ""), item["qty"],
            price, currency, (p or {}).get("source", "cache" if item.get("last_price") else ""),
            val_own, val_huf, val_eur, val_usd,
            (p or {}).get("timestamp", item.get("last_price_time", "")),
            "Igen" if (p or {}).get("stale") else "Nem",
            "kézi" if item.get("manually_added") else "keresés",
            export_ts,
        ])

    ws.append([])
    ws.append(["ÖSSZESÍTÉS"])
    ws.append(["Összesen HUF", total_huf])
    if fx.get("EUR/HUF") and total_huf:
        ws.append(["Összesen EUR", round(total_huf / fx["EUR/HUF"], 2)])
    if fx.get("USD/HUF") and total_huf:
        ws.append(["Összesen USD", round(total_huf / fx["USD/HUF"], 2)])
    ws.append(["Árfolyam nélküli tételek", sum(1 for i in portfolio if i["ticker"] not in prices)])
    ws.append(["Forrás (deviza)", fx_result.get("source", "")])

    col_widths = [22, 12, 12, 8, 16, 8, 18, 18, 18, 14, 14, 20, 10, 14, 18]
    for i, w in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        for cell in row:
            if isinstance(cell.value, float):
                cell.number_format = "#,##0.00"
            cell.alignment = Alignment(vertical="center")
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)

    log_event(current_user_id(), "excel_export", f"sorok={len(portfolio)}", _client_ip())

    filename = datetime.now().strftime("portfolio_%Y%m%d_%H%M.xlsx")
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=filename,
    )


# ===========================================================================
# ADMIN FELÜLET
# ===========================================================================

@app.route("/admin")
@admin_required
def admin_index():
    stats = get_stats()
    return render_template("admin/index.html",
                           stats=stats, username=current_username(), role=current_role())


# ---------------------------------------------------------------------------
# /admin/users
# ---------------------------------------------------------------------------

@app.route("/admin/users")
@admin_required
def admin_users():
    users = get_all_users()
    return render_template("admin/users.html", users=users,
                           username=current_username(), role=current_role())


@app.route("/admin/users/new", methods=["GET", "POST"])
@admin_required
def admin_user_new():
    error = None
    if request.method == "POST":
        uname = request.form.get("username", "").strip()
        pw1 = request.form.get("password", "")
        pw2 = request.form.get("password2", "")
        role = request.form.get("role", "user")
        is_active = request.form.get("is_active", "1") == "1"

        if not uname or not pw1:
            error = "Felhasználónév és jelszó kötelező."
        elif pw1 != pw2:
            error = "A két jelszó nem egyezik."
        elif len(pw1) < 6:
            error = "A jelszó legalább 6 karakter legyen."
        else:
            try:
                create_user(uname, pw1, role=role, is_active=is_active)
                log_event(current_user_id(), "user_create",
                          f"username={uname} role={role}", _client_ip())
                flash(f"Felhasználó létrehozva: {uname}", "success")
                return redirect(url_for("admin_users"))
            except Exception as e:
                error = f"Hiba: {e}"

    return render_template("admin/user_form.html", error=error, edit_user=None,
                           username=current_username(), role=current_role())


@app.route("/admin/users/<int:uid>/edit", methods=["GET", "POST"])
@admin_required
def admin_user_edit(uid: int):
    edit_user = get_user_by_id(uid)
    if not edit_user:
        flash("Felhasználó nem található.", "error")
        return redirect(url_for("admin_users"))

    error = None
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "update":
            new_role = request.form.get("role", edit_user["role"])
            new_active = 1 if request.form.get("is_active") == "1" else 0
            update_user(uid, role=new_role, is_active=new_active)
            log_event(current_user_id(), "user_update",
                      f"uid={uid} role={new_role} active={new_active}", _client_ip())
            flash("Felhasználó módosítva.", "success")
            return redirect(url_for("admin_users"))
        elif action == "password":
            pw1 = request.form.get("password", "")
            pw2 = request.form.get("password2", "")
            if pw1 != pw2:
                error = "A két jelszó nem egyezik."
            elif len(pw1) < 6:
                error = "A jelszó legalább 6 karakter legyen."
            else:
                set_user_password(uid, pw1)
                log_event(current_user_id(), "user_password_change",
                          f"uid={uid}", _client_ip())
                flash("Jelszó módosítva.", "success")
                return redirect(url_for("admin_users"))

    edit_user = get_user_by_id(uid)
    return render_template("admin/user_form.html", error=error, edit_user=edit_user,
                           username=current_username(), role=current_role())


# ---------------------------------------------------------------------------
# /admin/settings
# ---------------------------------------------------------------------------

@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        for key in request.form:
            if key.startswith("setting_"):
                setting_key = key[len("setting_"):]
                value = request.form[key].strip()
                save_setting(setting_key, value)
        log_event(current_user_id(), "settings_update", "", _client_ip())
        flash("Beállítások mentve.", "success")
        return redirect(url_for("admin_settings"))

    settings = get_all_settings_with_defaults()
    return render_template("admin/settings.html", settings=settings,
                           username=current_username(), role=current_role())


# ---------------------------------------------------------------------------
# /admin/system
# ---------------------------------------------------------------------------

@app.route("/admin/system")
@admin_required
def admin_system():
    import platform
    from services.db import _is_postgres
    from services.fx import FX_FILE_CACHE

    stats = get_stats()

    # DB kapcsolat check
    db_ok = True
    try:
        db.get_stats()
    except Exception:
        db_ok = False

    # FX utolsó frissítés
    fx_cached_ts = None
    try:
        if os.path.exists(FX_FILE_CACHE):
            import json as _json
            with open(FX_FILE_CACHE, "r", encoding="utf-8") as f:
                fxd = _json.load(f)
                fx_cached_ts = fxd.get("timestamp")
    except Exception:
        pass

    def is_set(key):
        return bool(os.environ.get(key, "").strip())

    status = {
        "app_version": "1.0.0",
        "python_version": platform.python_version(),
        "db_type": "PostgreSQL" if _is_postgres() else "SQLite",
        "db_ok": db_ok,
        "app_env": os.environ.get("APP_ENV", "local"),
        "has_secret_key": is_set("SECRET_KEY"),
        "has_database_url": is_set("DATABASE_URL"),
        "has_portfolio_password": is_set("PORTFOLIO_PASSWORD"),
        "default_admin_danger": (
            os.environ.get("PORTFOLIO_USERNAME", "admin") == "admin"
            and os.environ.get("PORTFOLIO_PASSWORD", "admin") == "admin"
        ),
        "fx_last_update": fx_cached_ts,
        "enable_yahoo": get_bool("enable_yahoo"),
        "enable_stooq": get_bool("enable_stooq"),
        "enable_mnb": get_bool("enable_mnb"),
        "maintenance_mode": get_bool("maintenance_mode"),
        **stats,
    }
    return render_template("admin/system.html", status=status,
                           username=current_username(), role=current_role())


# ---------------------------------------------------------------------------
# /admin/logs
# ---------------------------------------------------------------------------

@app.route("/admin/logs")
@admin_required
def admin_logs():
    logs = get_audit_logs(limit=100)
    return render_template("admin/logs.html", logs=logs,
                           username=current_username(), role=current_role())


# ---------------------------------------------------------------------------
# Hibaoldalak
# ---------------------------------------------------------------------------

@app.errorhandler(403)
def forbidden(_e):
    return render_template("403.html"), 403


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html") if os.path.exists("templates/404.html") else ("Oldal nem található.", 404)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Indítás
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    init_db()
    init_default_settings()

    use_reloader = os.environ.get("FLASK_USE_RELOADER", "false").lower() == "true"

    print("=" * 62)
    print("  Portfólió Követő indul...")
    print("  Nyisd meg: http://localhost:5000")
    print("  Belépés: admin / admin (fejlesztés) vagy .env")
    print(f"  Flask reloader: {'BE' if use_reloader else 'KI'}")
    print(f"  DB: {'PostgreSQL' if db._is_postgres() else 'SQLite'}")
    print("=" * 62)

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True,
        use_reloader=use_reloader,
    )
