"""
db.py — router automat între backends:
  - TURSO_URL setat     →  Turso (libSQL) — baza de date nouă, fără limită Neon
  - DATABASE_URL setat  →  PostgreSQL (Replit built-in sau Neon)
  - niciunul            →  memorie temporară via mongomock (HF Spaces fără DB extern)

Fallback de siguranță: dacă backend-ul configurat este INACCESIBIL la pornire
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


import sys as _sys

# Prioritate: Turso (baza nouă) → PostgreSQL (Neon) → memorie (mongomock).
if os.environ.get("TURSO_URL") and os.environ.get("TURSO_TOKEN"):
    import db_turso
    if db_turso.turso_ready():
        from db_turso import *            # noqa: F401, F403
        from db_turso import get_config, _now, turso_stats  # noqa: F401
        print("[db] Backend activ: Turso (persistent)", file=_sys.stderr)
    elif os.environ.get("DATABASE_URL") and _pg_reachable():
        from db_pg import *               # noqa: F401, F403
        from db_pg import get_config, _now  # noqa: F401
        print("[db] Backend activ: PostgreSQL (fallback)", file=_sys.stderr)
    else:
        from db_mg import *               # noqa: F401, F403
        from db_mg import get_config, _now  # noqa: F401
        print("[db] Backend activ: mongomock IN-MEMORY (datele NU persistă!)", file=_sys.stderr)
elif os.environ.get("DATABASE_URL") and _pg_reachable():
    from db_pg import *          # noqa: F401, F403
    from db_pg import get_config, _now  # noqa: F401
    print("[db] Backend activ: PostgreSQL", file=_sys.stderr)
else:
    from db_mg import *          # noqa: F401, F403
    from db_mg import get_config, _now  # noqa: F401
    print("[db] Backend activ: mongomock IN-MEMORY (datele NU persistă!)", file=_sys.stderr)


def turso_stats():
    """Returnează statistici DB (doar pentru Turso, altfel goale)."""
    try:
        return db_turso.turso_stats()
    except Exception:
        return {"reads": 0, "writes": 0, "errors": 0}


_active_backend = "mongomock"
try:
    _active_backend = "turso" if "create_user" in dir() and db_turso.turso_connected() else "mongomock"
except Exception:
    _active_backend = "mongomock"


def get_active_backend():
    """Returnează numele backend-ului activ (turso/mongomock)."""
    return _active_backend


def try_reconnect_turso():
    """Încearcă reconectarea la Turso în timpul rulării.
    
    Dacă Turso răspunde, importă funcțiile Turso peste mongomock.
    Returnează (ok: bool, message: str).
    """
    global _active_backend
    import sys
    
    if not os.environ.get("TURSO_URL") or not os.environ.get("TURSO_TOKEN"):
        return False, "TURSO_URL sau TURSO_TOKEN nu sunt setate in environment."
    
    try:
        import db_turso
        ok = db_turso.turso_ready()
        if not ok:
            return False, "Turso nu raspunde (toate incercarile au esuat). Verifica TURSO_URL si TURSO_TOKEN."
        
        # Turso e conectat — importăm funcțiile peste mongomock
        import db_turso as _t
        # Copiem toate funcțiile publice din db_turso în namespace-ul curent
        for _name in dir(_t):
            if not _name.startswith('_'):
                globals()[_name] = getattr(_t, _name)
        _active_backend = "turso"
        print("[db] Reconectare Turso reusita! Backend: Turso (persistent)", file=sys.stderr)
        return True, "Turso reconectat cu succes!"
    except Exception as e:
        print(f"[db] Reconectare Turso esuata: {type(e).__name__}: {e}", file=sys.stderr)
        return False, f"Eroare: {type(e).__name__}: {str(e)[:100]}"
