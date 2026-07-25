"""
db.py — router automat între backends:
  - DATABASE_URL setat  →  PostgreSQL (Replit built-in sau Neon)
  - DATABASE_URL absent →  memorie temporară via mongomock (HF Spaces fără DB extern)
"""
import os

if os.environ.get("DATABASE_URL"):
    from db_pg import *          # noqa: F401, F403
    from db_pg import get_config, _now  # noqa: F401
else:
    from db_mg import *          # noqa: F401, F403
    from db_mg import get_config, _now  # noqa: F401
