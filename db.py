"""
db.py — router automat între backends:
  - DATABASE_URL setat  →  PostgreSQL (Replit built-in sau Neon)
  - DATABASE_URL absent →  memorie temporară via mongomock (HF Spaces fără DB extern)

Fallback de siguranță: dacă PostgreSQL este configurat dar INACCESIBIL la pornire
(ex: limita de transfer Neon depășită, serverul oprit), aplicația NU se prăbușește:
cade automat pe backend-ul în memorie, cu un avertisment clar în loguri.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Încarcă .env din directorul proiectului (safe cu caractere speciale gen „&" din URL).
# Important: python-dotenv citește valoarea ca atare, spre deosebire de `source` în bash,
# care trunchiaza DATABASE_URL la primul „&" (ex: ...&channel_binding=require) și face
# aplicația să cadă înapoi pe mongomock în-memory, pierzând datele la fiecare restart.
load_dotenv(Path(__file__).parent / ".env")


def _pg_reachable():
    """Test rapid: putem deschide o conexiune la PostgreSQL? (fără a rula schema)"""
    if not os.environ.get("DATABASE_URL"):
        return False
    try:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(
            os.environ["DATABASE_URL"],
            cursor_factory=psycopg2.extras.RealDictCursor,
            connect_timeout=8,
        )
        conn.close()
        return True
    except Exception as _e:  # noqa
        import sys
        print(f"[db] PostgreSQL indisponibil la pornire ({type(_e).__name__}). "
              f"Folosesc backend-ul în memorie (datele NU persistă la restart).", file=sys.stderr)
        return False


if os.environ.get("DATABASE_URL") and _pg_reachable():
    from db_pg import *          # noqa: F401, F403
    from db_pg import get_config, _now  # noqa: F401
else:
    from db_mg import *          # noqa: F401, F403
    from db_mg import get_config, _now  # noqa: F401
