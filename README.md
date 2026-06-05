# 📈 Portfólió Követő – Zoltán

Egyszerű, magyar nyelvű webalkalmazás részvények és befektetések követéséhez.
Admin felülettel, felhasználókezeléssel és Vercel/PostgreSQL deployment támogatással.

---

## 🚀 Gyors indítás (helyi fejlesztés)

```powershell
pip install -r requirements.txt
python app.py
```

Böngészőben: http://localhost:5000  
Belépés: **admin / admin** (fejlesztési alapértelmezett)

---

## 🔐 Belépési adatok és SECRET_KEY

### SECRET_KEY generálás
```powershell
python -c "import secrets; print(secrets.token_hex(32))"
```

### `.env` fájl (a projekt gyökerében):
```
SECRET_KEY=ide-irj-egy-hosszu-veletlen-kulcsot
PORTFOLIO_USERNAME=zoltan
PORTFOLIO_PASSWORD=erős-jelszó
FLASK_USE_RELOADER=false
```

Másold le a `.env.example` fájlt kiindulópontnak:
```powershell
copy .env.example .env
```

---

## 👤 Admin felület

Belépés után az admin felhasználók a `/admin` oldalon kezelhetik:

| Oldal | URL | Leírás |
|-------|-----|--------|
| Áttekintő | `/admin` | Statisztikák |
| Felhasználók | `/admin/users` | Listázás, létrehozás, szerkesztés |
| Beállítások | `/admin/settings` | Cache TTL, providerek, karbantartás |
| Rendszer | `/admin/system` | Verzió, DB állapot, env. változók |
| Napló | `/admin/logs` | Audit log (utolsó 100 esemény) |

Normál `user` szerepű fiók nem éri el az admin felületet (403).

---

## 🗄️ Adattárolás

### Helyi fejlesztés
- **SQLite**: `portfolio_tracker.db` (automatikusan keletkezik)

### Production / Vercel
- **PostgreSQL**: `DATABASE_URL` env változón keresztül

```
DATABASE_URL=postgresql://user:password@host:5432/dbname
```

### Táblastruktúra
| Tábla | Tartalom |
|-------|----------|
| `users` | Felhasználók, jelszavak (hash), role, aktív flag |
| `portfolio_items` | Portfólió tételek user_id izoláció |
| `symbols_cache` | Korábban keresett tickerek (globális) |
| `settings` | Alkalmazás beállítások admin felületről |
| `audit_logs` | Login, módosítás, export események |

### Migráció régi JSON fájlokból
Első indításkor `portfolio.json` → admin user portfóliójába importálódik.
Jelölőfájl (`portfolio.json.imported`) védi az újrafutástól.

---

## 🌐 Vercel deployment

### Miért nem jó SQLite Vercelen?
Vercel serverless funkcióknál a fájlrendszer nem tartós.
SQLite fájl minden deploy után elvész. **PostgreSQL szükséges.**

### Deployment lépések

**1. PostgreSQL adatbázis létrehozása**  
Ajánlott: Supabase, Neon, Railway, Render Postgres

```
DATABASE_URL=postgresql://user:pass@host:5432/db
```

**2. GitHub repository feltöltése**
```powershell
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/felhasznalo/portfolio-tracker.git
git push -u origin main
```

**3. Vercel import**
- Menj: https://vercel.com/new
- Importáld a GitHub repo-t
- Framework: **Other**

**4. Environment Variables beállítása Vercelen**

| Változó | Érték |
|---------|-------|
| `SECRET_KEY` | `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | PostgreSQL connection string |
| `PORTFOLIO_USERNAME` | Admin felhasználónév |
| `PORTFOLIO_PASSWORD` | Erős jelszó |
| `APP_ENV` | `production` |
| `ENABLE_DEFAULT_ADMIN` | `false` |

**5. Deploy → első belépés**  
Az alkalmazás induláskor automatikusan létrehozza a táblákat és az admin usert.

**6. Ellenőrzés**
- `/admin/system` → DB típus: PostgreSQL ✓
- `SECRET_KEY`: beállítva ✓
- `PORTFOLIO_PASSWORD`: beállítva ✓

---

## 📊 Árfolyamforrások

| Forrás | Használat |
|--------|-----------|
| Yahoo Finance | Elsődleges részvényárfolyam (~15 perc késés) |
| Stooq fallback | Ha Yahoo nem elérhető (`.us`, `.hu`, `.de`, `.uk` stb.) |
| MNB SOAP | Hivatalos devizaárfolyamok (HUF/EUR/USD stb.) |
| In-memory cache | TTL alapú (price_cache_minutes beállítástól) |

Admin beállításoknál ki/bekapcsolható: `enable_yahoo`, `enable_stooq`, `enable_mnb`.

---

## 📥 Excel export

A portfólió táblázat fejlécénél **⬇ Excel** gombra kattintva:
- BytesIO-ba generálódik (nem ír fájlt) → Vercelen is működik
- Tartalmaz: ticker, darabszám, árfolyam, deviza, forrás, értékek (HUF/EUR/USD)
- Összesítő sorok az aljánál
- Admin beállításban kikapcsolható: `excel_export_enabled`

---

## 🧪 Tesztek

```powershell
python -m pytest tests/ -v
```

145 teszt, internet nélkül (mock-ok). Lefed: DB CRUD, SQLAlchemy migráció,
user izolácio, admin jogosultság, settings, Excel, Stooq mapping, MNB SOAP.

---

## 📁 Fájlstruktúra

```
portfolio_tracker/
├── app.py                    ← Flask app, routes, auth, admin, Excel
├── api/
│   └── index.py              ← Vercel serverless belépési pont
├── services/
│   ├── db.py                 ← SQLAlchemy (SQLite/PostgreSQL)
│   ├── settings_store.py     ← Beállításkezelés DB + cache
│   ├── fx.py                 ← MNB SOAP devizaárfolyam
│   ├── stocks.py             ← Yahoo Finance + Stooq fallback
│   ├── symbol_resolver.py    ← Ticker keresés
│   ├── cache.py              ← In-memory TTL cache
│   └── portfolio_store.py    ← JSON portfólió (legacy/migration)
├── templates/
│   ├── login.html
│   ├── index.html
│   ├── 403.html
│   ├── maintenance.html
│   └── admin/
│       ├── base.html
│       ├── index.html
│       ├── users.html
│       ├── user_form.html
│       ├── settings.html
│       ├── system.html
│       └── logs.html
├── tests/
│   ├── test_admin.py
│   ├── test_api.py
│   ├── test_db.py
│   ├── test_fx.py
│   ├── test_stocks.py
│   ├── test_portfolio_store.py
│   └── test_symbol_resolver.py
├── scripts/
│   └── test_mnb.py
├── requirements.txt
├── vercel.json
├── .env.example
└── README.md
```

---

## 🔧 Hibakeresés

**Yahoo rate limit** → Automatikus Stooq fallback. Néhány perc várakozás után visszaáll.

**MNB devizaárfolyam hiba**:
```powershell
python scripts/test_mnb.py
```

**Production – DATABASE_URL hiánya**:
```
RuntimeError: Production módban DATABASE_URL kötelező!
```
→ Állítsd be a DATABASE_URL és APP_ENV=production változókat.

**Secret key figyelmeztetés** → Generálj és add meg a SECRET_KEY-t a .env-ben.

**Admin/admin jelszó veszély** → `/admin/system` oldalon piros jelzés.
Állíts be PORTFOLIO_USERNAME és PORTFOLIO_PASSWORD változókat.


## Automatikus frissítés és email riasztások

A főoldalon beállítható, hogy az adatok milyen gyakran frissüljenek automatikusan. A választott időköz böngészőnként mentődik, az admin felületen pedig az `auto_refresh_seconds` értékkel adható meg az alapértelmezett frissítési idő.

A riasztások a főoldalon hozhatók létre. Támogatott feltételek:

- részvényárfolyam egy érték alá csökken vagy egy értéket elér,
- részvényárfolyam adott százalékot nő vagy csökken,
- teljes portfólió HUF értéke egy összeg alá csökken vagy egy összeget elér,
- teljes portfólió értéke adott százalékot nő vagy csökken.

Email küldéshez SMTP környezeti változók szükségesek:

```env
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=pelda@gmail.com
SMTP_PASSWORD=alkalmazas-jelszo
SMTP_FROM=pelda@gmail.com
SMTP_TLS=true
```

A riasztások minden kézi vagy automatikus frissítés után ellenőrződnek. Vercel/serverless környezetben ez akkor fut biztosan, amikor az oldal nyitva van vagy valaki meghívja az `/api/alerts/check` végpontot.
