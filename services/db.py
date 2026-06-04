"""
Adatbázis réteg – SQLite (local) és PostgreSQL (production) támogatással.

Helyi fejlesztéshez: SQLite (portfolio_tracker.db)
Production / Vercel: DATABASE_URL env változóból PostgreSQL

SQLAlchemy Core-t használ, ORM nélkül.
"""
import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime
from typing import Optional

from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
from werkzeug.security import check_password_hash, generate_password_hash

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portfolio_tracker.db")
_PORTFOLIO_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "portfolio.json")
_SYMBOLS_JSON = os.path.join(os.path.dirname(os.path.dirname(__file__)), "symbols_cache.json")

_engine = None


def reset_engine():
    """Törli a cached engine-t – teszteknél szükséges, ha DB_PATH változik."""
    global _engine
    _engine = None


def _is_postgres() -> bool:
    url = os.environ.get("DATABASE_URL", "")
    return url.startswith(("postgres://", "postgresql://"))


def get_engine():
    global _engine
    if _engine is None:
        _engine = _build_engine()
    return _engine


def _build_engine():
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if db_url:
        # Heroku/Vercel régi postgres:// → postgresql://
        if db_url.startswith("postgres://"):
            db_url = db_url.replace("postgres://", "postgresql://", 1)
        logger.info("Adatbázis: PostgreSQL (DATABASE_URL)")
        return create_engine(db_url)

    # Helyi SQLite
    logger.info("Adatbázis: SQLite (%s)", DB_PATH)
    return create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@contextmanager
def _conn():
    with get_engine().connect() as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def _row(row) -> dict:
    """SQLAlchemy Row → dict."""
    if row is None:
        return None
    return dict(row._mapping)


# ---------------------------------------------------------------------------
# Schema + inicializálás
# ---------------------------------------------------------------------------

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT,
    updated_at    TEXT,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    ticker              TEXT NOT NULL,
    name                TEXT NOT NULL DEFAULT '',
    qty                 REAL NOT NULL DEFAULT 0,
    currency            TEXT,
    exchange            TEXT DEFAULT '',
    source              TEXT DEFAULT 'unknown',
    manually_added      INTEGER DEFAULT 0,
    created_at          TEXT,
    updated_at          TEXT,
    last_price          REAL,
    last_price_currency TEXT,
    last_price_source   TEXT,
    last_price_time     TEXT,
    UNIQUE(user_id, ticker)
);

CREATE TABLE IF NOT EXISTS symbols_cache (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT UNIQUE NOT NULL,
    name                TEXT,
    currency            TEXT,
    exchange            TEXT,
    query_aliases       TEXT DEFAULT '[]',
    source              TEXT,
    last_price          REAL,
    last_price_currency TEXT,
    last_price_time     TEXT,
    last_seen           TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    value_type  TEXT DEFAULT 'string',
    description TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER,
    action      TEXT NOT NULL,
    details     TEXT,
    ip_address  TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS fx_rate_cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    mode       TEXT NOT NULL,
    pair       TEXT NOT NULL,
    rate       REAL NOT NULL,
    source     TEXT,
    fetched_at TEXT,
    rate_date  TEXT,
    UNIQUE(mode, pair)
);
"""

_SCHEMA_POSTGRES = """
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    username      TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'user',
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT,
    updated_at    TEXT,
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS portfolio_items (
    id                  SERIAL PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    ticker              TEXT NOT NULL,
    name                TEXT NOT NULL DEFAULT '',
    qty                 REAL NOT NULL DEFAULT 0,
    currency            TEXT,
    exchange            TEXT DEFAULT '',
    source              TEXT DEFAULT 'unknown',
    manually_added      INTEGER DEFAULT 0,
    created_at          TEXT,
    updated_at          TEXT,
    last_price          REAL,
    last_price_currency TEXT,
    last_price_source   TEXT,
    last_price_time     TEXT,
    UNIQUE(user_id, ticker)
);

CREATE TABLE IF NOT EXISTS symbols_cache (
    id                  SERIAL PRIMARY KEY,
    ticker              TEXT UNIQUE NOT NULL,
    name                TEXT,
    currency            TEXT,
    exchange            TEXT,
    query_aliases       TEXT DEFAULT '[]',
    source              TEXT,
    last_price          REAL,
    last_price_currency TEXT,
    last_price_time     TEXT,
    last_seen           TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT,
    value_type  TEXT DEFAULT 'string',
    description TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER,
    action      TEXT NOT NULL,
    details     TEXT,
    ip_address  TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS fx_rate_cache (
    id         SERIAL PRIMARY KEY,
    mode       TEXT NOT NULL,
    pair       TEXT NOT NULL,
    rate       REAL NOT NULL,
    source     TEXT,
    fetched_at TEXT,
    rate_date  TEXT,
    UNIQUE(mode, pair)
);
"""


def _create_tables(conn):
    schema = _SCHEMA_POSTGRES if _is_postgres() else _SCHEMA_SQLITE
    for stmt in schema.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(text(stmt))


def _apply_migrations(conn):
    """Meglévő DB-hez hozzáadja a hiányzó oszlopokat. Idempotens."""
    migrations = [
        "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE users ADD COLUMN updated_at TEXT",
        """
        CREATE TABLE IF NOT EXISTS fx_rate_cache (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            mode       TEXT NOT NULL,
            pair       TEXT NOT NULL,
            rate       REAL NOT NULL,
            source     TEXT,
            fetched_at TEXT,
            rate_date  TEXT,
            UNIQUE(mode, pair)
        )
        """,
    ]
    for sql in migrations:
        try:
            conn.execute(text(sql))
            conn.commit()
        except Exception:
            # Oszlop már létezik – normál eset
            conn.rollback()


def init_db(admin_username: str = None, admin_password: str = None):
    """
    Létrehozza a táblákat, az admin usert, és importálja a JSON fájlokat.
    Idempotens: többször biztonságosan hívható.
    """
    # Production: DATABASE_URL kötelező, ha APP_ENV=production
    app_env = os.environ.get("APP_ENV", "").lower()
    db_url = os.environ.get("DATABASE_URL", "").strip()
    if app_env == "production" and not db_url:
        raise RuntimeError(
            "Production módban DATABASE_URL kötelező! "
            "Állítsd be a DATABASE_URL környezeti változót."
        )

    with _conn() as conn:
        _create_tables(conn)
        _apply_migrations(conn)

    _ensure_admin_user(admin_username, admin_password)
    _migrate_portfolio_json()
    _migrate_symbols_json()
    logger.info("DB inicializálva: %s", "PostgreSQL" if _is_postgres() else DB_PATH)


def _ensure_admin_user(username: str = None, password: str = None):
    env_user = os.environ.get("PORTFOLIO_USERNAME", "").strip()
    env_pass = os.environ.get("PORTFOLIO_PASSWORD", "").strip()

    # Production: ne hozzon létre default admint, ha nincs jelszó
    app_env = os.environ.get("APP_ENV", "").lower()
    enable_default = os.environ.get("ENABLE_DEFAULT_ADMIN", "true").lower() != "false"

    uname = username or env_user or ("admin" if enable_default else None)
    pword = password or env_pass or ("admin" if enable_default else None)

    if not uname or not pword:
        logger.info("Nincs admin user létrehozva (ENABLE_DEFAULT_ADMIN=false vagy hiányzó jelszó).")
        return

    if uname == "admin" and pword == "admin":
        logger.warning(
            "Alapértelmezett admin/admin jelszó aktív. "
            "Internetre így ne tedd ki! Állíts be PORTFOLIO_USERNAME és PORTFOLIO_PASSWORD változókat."
        )

    with _conn() as conn:
        existing = conn.execute(
            text("SELECT id FROM users WHERE username = :u"), {"u": uname}
        ).fetchone()
        if not existing:
            phash = generate_password_hash(pword)
            now = _now()
            conn.execute(
                text("""
                    INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
                    VALUES (:u, :p, 'admin', 1, :ts, :ts)
                """),
                {"u": uname, "p": phash, "ts": now},
            )
            logger.info("Admin user létrehozva: %s (role=admin)", uname)


def _migrate_portfolio_json():
    marker = _PORTFOLIO_JSON + ".imported"
    if os.path.exists(marker) or not os.path.exists(_PORTFOLIO_JSON):
        return
    try:
        with open(_PORTFOLIO_JSON, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list) or not items:
            _write_marker(marker)
            return
    except Exception as e:
        logger.warning("portfolio.json olvasási hiba (migráció kihagyva): %s", e)
        return

    with _conn() as conn:
        row = conn.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).fetchone()
        if not row:
            return
        user_id = row[0]
        count = conn.execute(
            text("SELECT COUNT(*) FROM portfolio_items WHERE user_id = :uid"), {"uid": user_id}
        ).fetchone()[0]
        if count > 0:
            _write_marker(marker)
            return

        imported = 0
        now = _now()
        for item in items:
            if not isinstance(item, dict) or not item.get("ticker"):
                continue
            ticker = str(item.get("ticker", "")).strip().upper()
            try:
                if _is_postgres():
                    conn.execute(text("""
                        INSERT INTO portfolio_items (user_id, ticker, name, qty, currency, exchange, source, manually_added, created_at, updated_at)
                        VALUES (:uid, :t, :n, :q, :c, :e, :s, :m, :ts, :ts)
                        ON CONFLICT (user_id, ticker) DO NOTHING
                    """), {
                        "uid": user_id, "t": ticker,
                        "n": str(item.get("name") or ticker),
                        "q": float(item.get("qty", 0)),
                        "c": str(item.get("currency") or "") or None,
                        "e": str(item.get("exchange") or ""),
                        "s": str(item.get("source") or "imported"),
                        "m": int(bool(item.get("manually_added", False))),
                        "ts": now,
                    })
                else:
                    conn.execute(text("""
                        INSERT OR IGNORE INTO portfolio_items (user_id, ticker, name, qty, currency, exchange, source, manually_added, created_at, updated_at)
                        VALUES (:uid, :t, :n, :q, :c, :e, :s, :m, :ts, :ts)
                    """), {
                        "uid": user_id, "t": ticker,
                        "n": str(item.get("name") or ticker),
                        "q": float(item.get("qty", 0)),
                        "c": str(item.get("currency") or "") or None,
                        "e": str(item.get("exchange") or ""),
                        "s": str(item.get("source") or "imported"),
                        "m": int(bool(item.get("manually_added", False))),
                        "ts": now,
                    })
                imported += 1
            except Exception as exc:
                logger.warning("Portfolio elem import hiba (%s): %s", ticker, exc)

    if imported:
        logger.info("portfolio.json importálva: %d sor", imported)
    _write_marker(marker)


def _migrate_symbols_json():
    marker = _SYMBOLS_JSON + ".imported"
    if os.path.exists(marker) or not os.path.exists(_SYMBOLS_JSON):
        return
    try:
        with open(_SYMBOLS_JSON, "r", encoding="utf-8") as f:
            symbols = json.load(f)
        if not isinstance(symbols, list):
            _write_marker(marker)
            return
    except Exception as e:
        logger.warning("symbols_cache.json olvasási hiba: %s", e)
        return

    imported = 0
    for sym in symbols:
        if isinstance(sym, dict) and sym.get("ticker"):
            try:
                upsert_symbol_cache(sym)
                imported += 1
            except Exception:
                pass
    if imported:
        logger.info("symbols_cache.json importálva: %d sor", imported)
    _write_marker(marker)


def _write_marker(path: str):
    try:
        with open(path, "w") as f:
            f.write(_now())
    except Exception:
        pass


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# User management
# ---------------------------------------------------------------------------

def get_user_by_username(username: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            text("SELECT * FROM users WHERE username = :u"), {"u": username}
        ).fetchone()
        return _row(row)


def get_user_by_id(user_id: int) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            text("SELECT * FROM users WHERE id = :id"), {"id": user_id}
        ).fetchone()
        return _row(row)


def get_all_users() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            text("SELECT * FROM users ORDER BY created_at")
        ).fetchall()
        return [_row(r) for r in rows]


def verify_password(username: str, password: str) -> Optional[dict]:
    """Jelszó ellenőrzés. Csak aktív user léphet be."""
    user = get_user_by_username(username)
    if user and user.get("is_active", 1) and check_password_hash(user["password_hash"], password):
        return user
    return None


def create_user(username: str, password: str, role: str = "user", is_active: bool = True) -> dict:
    phash = generate_password_hash(password)
    now = _now()
    with _conn() as conn:
        conn.execute(text("""
            INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
            VALUES (:u, :p, :r, :a, :ts, :ts)
        """), {"u": username, "p": phash, "r": role, "a": int(is_active), "ts": now})
    return get_user_by_username(username)


def update_user(user_id: int, **fields) -> bool:
    """Frissít tetszőleges mezőket (username, role, is_active, password_hash)."""
    allowed = {"username", "role", "is_active", "password_hash"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = :{k}" for k in updates)
    updates["uid"] = user_id
    with _conn() as conn:
        cur = conn.execute(
            text(f"UPDATE users SET {set_clause} WHERE id = :uid"), updates
        )
        return cur.rowcount > 0


def set_user_password(user_id: int, new_password: str) -> bool:
    phash = generate_password_hash(new_password)
    return update_user(user_id, password_hash=phash)


def update_last_login(user_id: int):
    with _conn() as conn:
        conn.execute(
            text("UPDATE users SET last_login_at = :ts WHERE id = :id"),
            {"ts": _now(), "id": user_id},
        )


# ---------------------------------------------------------------------------
# Portfolio CRUD
# ---------------------------------------------------------------------------

def get_portfolio(user_id: int) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            text("SELECT * FROM portfolio_items WHERE user_id = :uid ORDER BY created_at"),
            {"uid": user_id},
        ).fetchall()
        return [_row(r) for r in rows]


def upsert_portfolio_item(user_id: int, item: dict) -> dict:
    ticker = str(item.get("ticker", "")).strip().upper()
    now = _now()

    with _conn() as conn:
        existing = conn.execute(
            text("SELECT id FROM portfolio_items WHERE user_id = :uid AND ticker = :t"),
            {"uid": user_id, "t": ticker},
        ).fetchone()

        if existing:
            conn.execute(text("""
                UPDATE portfolio_items SET
                    name=:n, qty=:q, currency=:c, exchange=:e,
                    source=:s, manually_added=:m, updated_at=:ts
                WHERE user_id=:uid AND ticker=:t
            """), {
                "n": str(item.get("name") or ticker),
                "q": float(item.get("qty", 0)),
                "c": str(item.get("currency") or "") or None,
                "e": str(item.get("exchange") or ""),
                "s": str(item.get("source") or "unknown"),
                "m": int(bool(item.get("manually_added", False))),
                "ts": now, "uid": user_id, "t": ticker,
            })
        else:
            conn.execute(text("""
                INSERT INTO portfolio_items
                (user_id, ticker, name, qty, currency, exchange, source, manually_added, created_at, updated_at)
                VALUES (:uid, :t, :n, :q, :c, :e, :s, :m, :ts, :ts)
            """), {
                "uid": user_id, "t": ticker,
                "n": str(item.get("name") or ticker),
                "q": float(item.get("qty", 0)),
                "c": str(item.get("currency") or "") or None,
                "e": str(item.get("exchange") or ""),
                "s": str(item.get("source") or "unknown"),
                "m": int(bool(item.get("manually_added", False))),
                "ts": now,
            })

        row = conn.execute(
            text("SELECT * FROM portfolio_items WHERE user_id=:uid AND ticker=:t"),
            {"uid": user_id, "t": ticker},
        ).fetchone()
        return _row(row)


def update_portfolio_qty(user_id: int, ticker: str, qty: float) -> bool:
    ticker = ticker.strip().upper()
    with _conn() as conn:
        cur = conn.execute(
            text("UPDATE portfolio_items SET qty=:q, updated_at=:ts WHERE user_id=:uid AND ticker=:t"),
            {"q": qty, "ts": _now(), "uid": user_id, "t": ticker},
        )
        return cur.rowcount > 0


def delete_portfolio_item(user_id: int, ticker: str) -> bool:
    ticker = ticker.strip().upper()
    with _conn() as conn:
        cur = conn.execute(
            text("DELETE FROM portfolio_items WHERE user_id=:uid AND ticker=:t"),
            {"uid": user_id, "t": ticker},
        )
        return cur.rowcount > 0


def delete_portfolio_item_by_id(user_id: int, item_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute(
            text("DELETE FROM portfolio_items WHERE user_id=:uid AND id=:id"),
            {"uid": user_id, "id": item_id},
        )
        return cur.rowcount > 0


def update_item_last_price(user_id: int, ticker: str, price: float, currency: str, source: str, ts: str):
    ticker = ticker.strip().upper()
    with _conn() as conn:
        conn.execute(text("""
            UPDATE portfolio_items SET
                last_price=:p, last_price_currency=:c, last_price_source=:s, last_price_time=:ts
            WHERE user_id=:uid AND ticker=:t
        """), {"p": price, "c": currency, "s": source, "ts": ts, "uid": user_id, "t": ticker})


def save_full_portfolio(user_id: int, items: list[dict]):
    now = _now()
    with _conn() as conn:
        conn.execute(
            text("DELETE FROM portfolio_items WHERE user_id=:uid"), {"uid": user_id}
        )
        for item in items:
            ticker = str(item.get("ticker", "")).strip().upper()
            if not ticker:
                continue
            conn.execute(text("""
                INSERT INTO portfolio_items
                (user_id, ticker, name, qty, currency, exchange, source, manually_added, created_at, updated_at)
                VALUES (:uid, :t, :n, :q, :c, :e, :s, :m, :ts, :ts)
            """), {
                "uid": user_id, "t": ticker,
                "n": str(item.get("name") or ticker),
                "q": float(item.get("qty", 0)),
                "c": str(item.get("currency") or "") or None,
                "e": str(item.get("exchange") or ""),
                "s": str(item.get("source") or "unknown"),
                "m": int(bool(item.get("manually_added", False))),
                "ts": now,
            })


# ---------------------------------------------------------------------------
# Symbols cache
# ---------------------------------------------------------------------------

def upsert_symbol_cache(symbol: dict):
    ticker = str(symbol.get("ticker", "")).strip().upper()
    if not ticker:
        return
    aliases_json = json.dumps(symbol.get("query_aliases", []), ensure_ascii=False)
    now = _now()

    with _conn() as conn:
        if _is_postgres():
            conn.execute(text("""
                INSERT INTO symbols_cache
                (ticker, name, currency, exchange, query_aliases, source,
                 last_price, last_price_currency, last_price_time, last_seen)
                VALUES (:t, :n, :c, :e, :qa, :s, :lp, :lpc, :lpt, :ls)
                ON CONFLICT (ticker) DO UPDATE SET
                    name=EXCLUDED.name, currency=EXCLUDED.currency,
                    exchange=EXCLUDED.exchange, query_aliases=EXCLUDED.query_aliases,
                    source=EXCLUDED.source,
                    last_price=COALESCE(EXCLUDED.last_price, symbols_cache.last_price),
                    last_price_currency=COALESCE(EXCLUDED.last_price_currency, symbols_cache.last_price_currency),
                    last_price_time=COALESCE(EXCLUDED.last_price_time, symbols_cache.last_price_time),
                    last_seen=EXCLUDED.last_seen
            """), _sym_params(ticker, symbol, aliases_json, now))
        else:
            conn.execute(text("""
                INSERT INTO symbols_cache
                (ticker, name, currency, exchange, query_aliases, source,
                 last_price, last_price_currency, last_price_time, last_seen)
                VALUES (:t, :n, :c, :e, :qa, :s, :lp, :lpc, :lpt, :ls)
                ON CONFLICT(ticker) DO UPDATE SET
                    name=excluded.name, currency=excluded.currency,
                    exchange=excluded.exchange, query_aliases=excluded.query_aliases,
                    source=excluded.source,
                    last_price=COALESCE(excluded.last_price, last_price),
                    last_price_currency=COALESCE(excluded.last_price_currency, last_price_currency),
                    last_price_time=COALESCE(excluded.last_price_time, last_price_time),
                    last_seen=excluded.last_seen
            """), _sym_params(ticker, symbol, aliases_json, now))


def _sym_params(ticker, symbol, aliases_json, now):
    return {
        "t": ticker,
        "n": str(symbol.get("name") or ""),
        "c": str(symbol.get("currency") or ""),
        "e": str(symbol.get("exchange") or ""),
        "qa": aliases_json,
        "s": str(symbol.get("source") or ""),
        "lp": symbol.get("last_price"),
        "lpc": str(symbol.get("last_price_currency") or "") or None,
        "lpt": str(symbol.get("last_price_time") or "") or None,
        "ls": now,
    }


def get_all_symbols(limit: int = 50) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            text("SELECT * FROM symbols_cache ORDER BY last_seen DESC LIMIT :lim"),
            {"lim": limit},
        ).fetchall()
    return _parse_symbols(rows)


def search_symbols_db(query: str) -> list[dict]:
    q = f"%{query.lower()}%"
    with _conn() as conn:
        rows = conn.execute(text("""
            SELECT * FROM symbols_cache
            WHERE lower(ticker) LIKE :q OR lower(name) LIKE :q OR lower(query_aliases) LIKE :q
            ORDER BY last_seen DESC LIMIT 20
        """), {"q": q}).fetchall()
    return _parse_symbols(rows)


def _parse_symbols(rows) -> list[dict]:
    result = []
    for r in rows:
        d = _row(r)
        try:
            d["query_aliases"] = json.loads(d.get("query_aliases") or "[]")
        except Exception:
            d["query_aliases"] = []
        result.append(d)
    return result


# ---------------------------------------------------------------------------
# FX cache
# ---------------------------------------------------------------------------

def save_fx_rate_cache(mode: str, fx: dict, source: str = "", fetched_at: str = None, rate_date: str = None):
    """Elmenti a legutobbi FX arfolyamokat paronkent."""
    if not fx:
        return
    now = fetched_at or _now()
    mode = (mode or "market").strip().lower()
    rows = []
    for pair, rate in fx.items():
        if "/" not in pair or rate is None:
            continue
        try:
            rows.append({
                "m": mode,
                "p": str(pair),
                "r": float(rate),
                "s": source,
                "f": now,
                "d": rate_date,
            })
        except (TypeError, ValueError):
            continue
    if not rows:
        return

    with _conn() as conn:
        for params in rows:
            if _is_postgres():
                conn.execute(text("""
                    INSERT INTO fx_rate_cache (mode, pair, rate, source, fetched_at, rate_date)
                    VALUES (:m, :p, :r, :s, :f, :d)
                    ON CONFLICT (mode, pair) DO UPDATE SET
                        rate=EXCLUDED.rate,
                        source=EXCLUDED.source,
                        fetched_at=EXCLUDED.fetched_at,
                        rate_date=EXCLUDED.rate_date
                """), params)
            else:
                conn.execute(text("""
                    INSERT INTO fx_rate_cache (mode, pair, rate, source, fetched_at, rate_date)
                    VALUES (:m, :p, :r, :s, :f, :d)
                    ON CONFLICT(mode, pair) DO UPDATE SET
                        rate=excluded.rate,
                        source=excluded.source,
                        fetched_at=excluded.fetched_at,
                        rate_date=excluded.rate_date
                """), params)


def get_latest_fx_rate_cache(mode: str) -> Optional[dict]:
    mode = (mode or "market").strip().lower()
    with _conn() as conn:
        rows = conn.execute(text("""
            SELECT * FROM fx_rate_cache
            WHERE mode = :m
            ORDER BY fetched_at DESC
        """), {"m": mode}).fetchall()
    if not rows:
        return None

    fx = {}
    source = None
    fetched_at = None
    rate_date = None
    for row in rows:
        d = _row(row)
        fx[d["pair"]] = d["rate"]
        source = source or d.get("source")
        fetched_at = fetched_at or d.get("fetched_at")
        rate_date = rate_date or d.get("rate_date")
    return {
        "mode": mode,
        "fx": fx,
        "source": source,
        "fetched_at": fetched_at,
        "rate_date": rate_date,
    }


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def get_setting_raw(key: str) -> Optional[dict]:
    with _conn() as conn:
        row = conn.execute(
            text("SELECT * FROM settings WHERE key = :k"), {"k": key}
        ).fetchone()
        return _row(row)


def set_setting(key: str, value: str, value_type: str = "string", description: str = ""):
    now = _now()
    with _conn() as conn:
        if _is_postgres():
            conn.execute(text("""
                INSERT INTO settings (key, value, value_type, description, updated_at)
                VALUES (:k, :v, :vt, :d, :ts)
                ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at
            """), {"k": key, "v": str(value), "vt": value_type, "d": description, "ts": now})
        else:
            conn.execute(text("""
                INSERT INTO settings (key, value, value_type, description, updated_at)
                VALUES (:k, :v, :vt, :d, :ts)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """), {"k": key, "v": str(value), "vt": value_type, "d": description, "ts": now})


def get_all_settings_raw() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            text("SELECT * FROM settings ORDER BY key")
        ).fetchall()
        return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def log_event(user_id: Optional[int], action: str, details: str = "", ip_address: str = ""):
    try:
        with _conn() as conn:
            conn.execute(text("""
                INSERT INTO audit_logs (user_id, action, details, ip_address, created_at)
                VALUES (:uid, :a, :d, :ip, :ts)
            """), {"uid": user_id, "a": action, "d": details, "ip": ip_address, "ts": _now()})
    except Exception as e:
        logger.warning("Audit log hiba: %s", e)


def get_audit_logs(limit: int = 100) -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(text("""
            SELECT al.*, u.username FROM audit_logs al
            LEFT JOIN users u ON al.user_id = u.id
            ORDER BY al.created_at DESC LIMIT :lim
        """), {"lim": limit}).fetchall()
        return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Admin stats
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    with _conn() as conn:
        users = conn.execute(text("SELECT COUNT(*) FROM users")).fetchone()[0]
        items = conn.execute(text("SELECT COUNT(*) FROM portfolio_items")).fetchone()[0]
        symbols = conn.execute(text("SELECT COUNT(*) FROM symbols_cache")).fetchone()[0]
        try:
            logs = conn.execute(text("SELECT COUNT(*) FROM audit_logs")).fetchone()[0]
        except Exception:
            logs = 0
    return {"users": users, "portfolio_items": items, "symbols": symbols, "audit_logs": logs}
