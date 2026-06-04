"""
Vercel serverless belépési pont.
A Flask app-ot exportálja, amelyet a Vercel Python runtime kiszolgál.
"""
import sys
import os

# A projekt gyökerét hozzáadjuk az import pathhoz
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app, init_db, init_default_settings  # noqa: E402

# Vercel-en az init induláskor fut
try:
    init_db()
    init_default_settings()
except Exception as e:
    import logging
    logging.getLogger(__name__).error("DB init hiba: %s", e)

# Vercel a Flask WSGI app-ot várja „app" változóként
__all__ = ["app"]
