"""
migrate_neon_to_turso.py — copiază datele de pe baza veche (Neon/PostgreSQL)
pe baza nouă (Turso). Rulează doar când Neon devine accesibil (de ex. după
upgradarea planului sau resetarea cotei de transfer).

Utilizare:
    DATABASE_URL=postgresql://... python3 migrate_neon_to_turso.py
    (TURSO_URL și TURSO_TOKEN trebuie să fie setate în mediu / .env)

Nu șterge nimic de pe Neon — doar citește și inserează pe Turso.
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

COLLECTION_ORDER = [
    "users", "sessions", "email_codes",
    "characters", "conversations", "messages",
    "chat_groups", "group_messages",
    "voice_library", "ambient_library",
]


def read_neon():
    import psycopg2
    import psycopg2.extras
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("Eroare: DATABASE_URL nu e setat.")
        sys.exit(1)
    conn = psycopg2.connect(dsn, cursor_factory=psycopg2.extras.RealDictCursor,
                            connect_timeout=15)
    data = {}
    for coll in COLLECTION_ORDER:
        try:
            with conn.cursor() as cur:
                cur.execute(f'SELECT * FROM "{coll}"')
                data[coll] = [dict(r) for r in cur.fetchall()]
            print(f"Neon {coll}: {len(data[coll])} rânduri")
        except Exception as e:
            print(f"Neon {coll}: NU CITIT ({type(e).__name__}: {str(e)[:80]})")
            data[coll] = []
    conn.close()
    return data


def write_turso(data):
    import db_turso
    if not db_turso.turso_ready():
        print("Eroare: Turso nu e accesibil.")
        sys.exit(1)
    # hartă tabel Neon -> funcții Turso
    target = {
        "users": ("users", ["id"]),
        "sessions": ("sessions", ["token"]),
        "email_codes": ("email_codes", ["email", "purpose"]),
        "characters": ("characters", ["id"]),
        "conversations": ("conversations", ["id"]),
        "messages": ("messages", ["id"]),
        "chat_groups": ("chat_groups", ["id"]),
        "group_messages": ("group_messages", ["id"]),
        "voice_library": ("voice_library", ["id"]),
        "ambient_library": ("ambient_library", ["id"]),
    }
    total = 0
    for coll, rows in data.items():
        table, key_cols = target[coll]
        for row in rows:
            doc = dict(row)
            # PostgreSQL ne oferă JSONB deja parsabil; asigură tipuri simple.
            for k, v in list(doc.items()):
                if hasattr(v, "isoformat"):  # datetime
                    doc[k] = v.isoformat()
                elif isinstance(v, (list, dict)):
                    doc[k] = v
            # elimină chei goale / NULL-uri inutile
            doc = {k: v for k, v in doc.items() if v is not None}
            db_turso._insert(table, doc)
            total += 1
    print(f"Total rânduri migrate pe Turso: {total}")


if __name__ == "__main__":
    data = read_neon()
    write_turso(data)
    print("Migrare finalizată.")
