"""
db.py — strat de persistență PostgreSQL (Replit built-in).

Fiecare tabel are coloane indexate pentru interogări + o coloană `doc` JSONB
care stochează documentul complet, păstrând API-ul identic cu versiunea MongoDB.
"""

import os
import uuid
import json
from pathlib import Path
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from provider import clean_key as _clean_key


def _clean_tok(raw):
    return _clean_key(raw, "gh") if raw else raw


# ---------------------------------------------------------------------------
# Conexiune
# ---------------------------------------------------------------------------

@contextmanager
def _conn():
    """Deschide o conexiune Postgres, commit la succes / rollback la eroare,
    și O ÎNCHIDE ÎNTOTDEAUNA (fix scurgere de conexiuni — psycopg2 `with conn`
    NU închide conexiunea, doar tranzacția, ceea ce epuiza limita Neon)."""
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # noqa
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:  # noqa
            pass


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT UNIQUE NOT NULL,
    doc         JSONB NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS characters (
    id          TEXT PRIMARY KEY,
    owner_id    TEXT,
    visibility  TEXT DEFAULT 'private',
    created_at  TEXT,
    doc         JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_chars_owner ON characters(owner_id);
CREATE INDEX IF NOT EXISTS idx_chars_vis   ON characters(visibility);

CREATE TABLE IF NOT EXISTS conversations (
    id           TEXT PRIMARY KEY,
    character_id TEXT,
    created_at   TEXT,
    doc          JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_convs_char ON conversations(character_id);

CREATE TABLE IF NOT EXISTS messages (
    id              TEXT PRIMARY KEY,
    conversation_id TEXT,
    created_at      TEXT,
    role            TEXT,
    media_kind      TEXT,
    doc             JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_msgs_conv ON messages(conversation_id);

CREATE TABLE IF NOT EXISTS sessions (
    token    TEXT PRIMARY KEY,
    user_id  TEXT,
    doc      JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);

CREATE TABLE IF NOT EXISTS email_codes (
    id      SERIAL PRIMARY KEY,
    email   TEXT,
    purpose TEXT,
    doc     JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ecodes ON email_codes(email, purpose);

CREATE TABLE IF NOT EXISTS voice_library (
    id              TEXT PRIMARY KEY,
    owner_id        TEXT,
    visibility      TEXT DEFAULT 'public',
    created_at      TEXT,
    name            TEXT NOT NULL,
    description     TEXT,
    sample_b64      TEXT,
    sample_name     TEXT,
    speaker_embedding JSONB,
    voice_params    JSONB DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_voice_owner ON voice_library(owner_id);
CREATE INDEX IF NOT EXISTS idx_voice_vis   ON voice_library(visibility);

-- Biblioteca de sunete ambientale
CREATE TABLE IF NOT EXISTS ambient_library (
    id              TEXT PRIMARY KEY,
    owner_id        TEXT,
    visibility      TEXT DEFAULT 'public',
    created_at      TEXT,
    name            TEXT NOT NULL,
    category        TEXT,
    description     TEXT,
    audio_b64       TEXT,
    audio_name      TEXT,
    duration        REAL DEFAULT 0.0,
    tags            TEXT[],
    is_synthetic    BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_ambient_owner ON ambient_library(owner_id);
CREATE INDEX IF NOT EXISTS idx_ambient_vis   ON ambient_library(visibility);
CREATE INDEX IF NOT EXISTS idx_ambient_cat   ON ambient_library(category);

"""


def _init_schema():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(_DDL)
        c.commit()


try:
    _init_schema()
except Exception as _e:
    import sys
    print(f"[db] Schema init error: {_e}", file=sys.stderr)


# Seed biblioteca de sunete ambientale (doar dacă e goală)
try:
    _with_conn = _conn()
    with _with_conn as c:
        with c.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM ambient_library")
            count = cur.fetchone()["count"]
        if count == 0:
            print("[db] Seeding ambient library...")
            try:
                seed_ambient_library()
                print("[db] Ambient library seeded successfully!")
            except Exception as seed_err:
                print(f"[db] Seed warning: {seed_err}")
except Exception as _e2:
    import sys
    print(f"[db] Ambient seed error: {_e2}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Helpers JSONB
# ---------------------------------------------------------------------------

def _row(row):
    """Returnează doc-ul Python dintr-un rând de tabel."""
    if row is None:
        return None
    d = dict(row["doc"])
    return d


def _rows(rows):
    return [_row(r) for r in rows]


def _jdump(d):
    return psycopg2.extras.Json(d)


# ---------------------------------------------------------------------------
# get_config (citit din os.environ; GitHub fallback eliminat)
# ---------------------------------------------------------------------------

def get_config(name, default=None):
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# users / auth
# ---------------------------------------------------------------------------

def create_user(email, password_hash, name, verified=False,
                security_question=None, security_answer_hash=None):
    doc = {
        "id": str(uuid.uuid4()),
        "email": email,
        "password_hash": password_hash,
        "name": name,
        "verified": verified,
        "avatar_image": None,
        "security_question": security_question,
        "security_answer_hash": security_answer_hash,
        "favorites": [],
        "prefs": {},
        "created_at": _now(),
    }
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, email, doc) VALUES (%s, %s, %s)"
                " ON CONFLICT (email) DO NOTHING",
                (doc["id"], email, _jdump(doc)),
            )
        c.commit()
    return doc


def get_user_by_email(email):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT doc FROM users WHERE email = %s", (email,))
            return _row(cur.fetchone())


def get_user_by_id(uid):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT doc FROM users WHERE id = %s", (uid,))
            return _row(cur.fetchone())


def update_user(uid, data):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE users SET doc = doc || %s::jsonb WHERE id = %s",
                (_jdump(data), uid),
            )
            # dacă email-ul s-a schimbat, actualizăm și coloana indexată
            if "email" in data:
                cur.execute(
                    "UPDATE users SET email = %s WHERE id = %s",
                    (data["email"], uid),
                )
        c.commit()
    return get_user_by_id(uid)


def set_user_verified(email):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE users SET doc = doc || '{\"verified\": true}'::jsonb"
                " WHERE email = %s",
                (email,),
            )
        c.commit()


def set_user_password(email, password_hash):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE users SET doc = doc || %s::jsonb WHERE email = %s",
                (_jdump({"password_hash": password_hash}), email),
            )
        c.commit()


def toggle_favorite(user_id, char_id):
    u = get_user_by_id(user_id)
    favs = list((u or {}).get("favorites") or [])
    if char_id in favs:
        favs.remove(char_id)
        state = False
    else:
        favs.append(char_id)
        state = True
    update_user(user_id, {"favorites": favs})
    return state


def get_favorites(user_id):
    u = get_user_by_id(user_id)
    return list((u or {}).get("favorites") or []) if u else []


def favorite_counts():
    counts = {}
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT doc->>'favorites' AS favs FROM users")
            for row in cur.fetchall():
                raw = row["favs"]
                if not raw:
                    continue
                try:
                    for cid in json.loads(raw):
                        counts[cid] = counts.get(cid, 0) + 1
                except Exception:
                    pass
    return counts


def delete_user(user_id):
    u = get_user_by_id(user_id)
    if not u:
        return
    for ch in list_characters(owner_id=user_id):
        delete_character(ch["id"])
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            cur.execute(
                "DELETE FROM email_codes WHERE email = %s", (u.get("email"),)
            )
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        c.commit()


# ---------------------------------------------------------------------------
# email codes / sessions
# ---------------------------------------------------------------------------

def create_email_code(email, code, purpose, ttl_minutes=15):
    doc = {
        "email": email,
        "code": code,
        "purpose": purpose,
        "created_at": _now(),
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
        ).isoformat(),
    }
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM email_codes WHERE email = %s AND purpose = %s",
                (email, purpose),
            )
            cur.execute(
                "INSERT INTO email_codes (email, purpose, doc) VALUES (%s, %s, %s)",
                (email, purpose, _jdump(doc)),
            )
        c.commit()


def check_email_code(email, code, purpose):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT id, doc FROM email_codes"
                " WHERE email = %s AND purpose = %s AND doc->>'code' = %s",
                (email, purpose, (code or "").strip()),
            )
            row = cur.fetchone()
            if not row:
                return False
            doc = dict(row["doc"])
            try:
                if (
                    datetime.fromisoformat(doc["expires_at"])
                    < datetime.now(timezone.utc)
                ):
                    cur.execute(
                        "DELETE FROM email_codes WHERE id = %s", (row["id"],)
                    )
                    c.commit()
                    return False
            except Exception:
                pass
            cur.execute("DELETE FROM email_codes WHERE id = %s", (row["id"],))
            c.commit()
    return True


def create_session(token, user_id, expires_days=30):
    doc = {
        "token": token,
        "user_id": user_id,
        "created_at": _now(),
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(days=expires_days)
        ).isoformat(),
    }
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (token, user_id, doc) VALUES (%s, %s, %s)"
                " ON CONFLICT (token) DO NOTHING",
                (token, user_id, _jdump(doc)),
            )
        c.commit()


def get_session(token):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT doc FROM sessions WHERE token = %s", (token,))
            row = cur.fetchone()
            if not row:
                return None
            s = dict(row["doc"])
            try:
                if (
                    datetime.fromisoformat(s["expires_at"])
                    < datetime.now(timezone.utc)
                ):
                    cur.execute(
                        "DELETE FROM sessions WHERE token = %s", (token,)
                    )
                    c.commit()
                    return None
            except Exception:
                pass
            return s


def delete_session(token):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
        c.commit()


# ---------------------------------------------------------------------------
# characters
# ---------------------------------------------------------------------------

def create_character(data):
    doc = {"id": str(uuid.uuid4()), "created_at": _now(), **data}
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO characters (id, owner_id, visibility, created_at, doc)"
                " VALUES (%s, %s, %s, %s, %s)",
                (
                    doc["id"],
                    doc.get("owner_id"),
                    doc.get("visibility", "private"),
                    doc["created_at"],
                    _jdump(doc),
                ),
            )
        c.commit()
    return doc


def list_characters(owner_id=None):
    with _conn() as c:
        with c.cursor() as cur:
            if owner_id is None:
                cur.execute(
                    "SELECT doc FROM characters ORDER BY created_at DESC"
                )
            else:
                cur.execute(
                    "SELECT doc FROM characters WHERE owner_id = %s"
                    " ORDER BY created_at DESC",
                    (owner_id,),
                )
            return _rows(cur.fetchall())


def reassign_owner(old_owner_id, new_owner_id):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE characters SET owner_id = %s,"
                " doc = doc || %s::jsonb WHERE owner_id = %s",
                (new_owner_id, _jdump({"owner_id": new_owner_id}), old_owner_id),
            )
        c.commit()


def list_public_characters():
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT doc FROM characters WHERE visibility = 'public'"
                " ORDER BY created_at DESC"
            )
            return _rows(cur.fetchall())


def get_character(cid):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("SELECT doc FROM characters WHERE id = %s", (cid,))
            return _row(cur.fetchone())


def update_character(cid, data):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE characters SET doc = doc || %s::jsonb WHERE id = %s",
                (_jdump(data), cid),
            )
            # actualizează coloanele indexate dacă e cazul
            if "owner_id" in data or "visibility" in data:
                sets, vals = [], []
                if "owner_id" in data:
                    sets.append("owner_id = %s")
                    vals.append(data["owner_id"])
                if "visibility" in data:
                    sets.append("visibility = %s")
                    vals.append(data["visibility"])
                vals.append(cid)
                cur.execute(
                    f"UPDATE characters SET {', '.join(sets)} WHERE id = %s",
                    vals,
                )
        c.commit()
    return get_character(cid)


def delete_user_voices(user_id):
    """Șterge doar datele vocale din personajele utilizatorului."""
    chars = list_characters(owner_id=user_id)
    fields = [
        "voice_id", "voice_name", "voice_sample_b64", "voice_sample_name",
        "voice_ref_text", "voice_stability", "voice_similarity",
        "voice_style", "voice_tone",
    ]
    count = 0
    for ch in chars:
        update_character(
            ch["id"],
            {f: None for f in fields},
        )
        count += 1
    return count


def increment_stat(char_id, field, n=1):
    ch = get_character(char_id)
    if ch is None:
        return
    current = ch.get(field, 0) or 0
    update_character(char_id, {field: current + n})


def character_message_count(char_id):
    conv_ids = [c["id"] for c in list_conversations(char_id)]
    if not conv_ids:
        return 0
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM messages WHERE conversation_id = ANY(%s)",
                (conv_ids,),
            )
            row = cur.fetchone()
            return row["cnt"] if row else 0


def delete_character(cid):
    conv_ids = [c["id"] for c in list_conversations(cid)]
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM characters WHERE id = %s", (cid,))
            cur.execute(
                "DELETE FROM conversations WHERE character_id = %s", (cid,)
            )
            if conv_ids:
                cur.execute(
                    "DELETE FROM messages WHERE conversation_id = ANY(%s)",
                    (conv_ids,),
                )
        c.commit()


# ---------------------------------------------------------------------------
# conversations
# ---------------------------------------------------------------------------

def create_conversation(character_id, title="Conversație nouă"):
    doc = {
        "id": str(uuid.uuid4()),
        "character_id": character_id,
        "title": title,
        "created_at": _now(),
        "updated_at": _now(),
    }
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (id, character_id, created_at, doc)"
                " VALUES (%s, %s, %s, %s)",
                (doc["id"], character_id, doc["created_at"], _jdump(doc)),
            )
        c.commit()
    return doc


def list_conversations(character_id):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT doc FROM conversations WHERE character_id = %s"
                " ORDER BY created_at ASC",
                (character_id,),
            )
            return _rows(cur.fetchall())


def get_conversation(conv_id):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT doc FROM conversations WHERE id = %s", (conv_id,)
            )
            return _row(cur.fetchone())


def rename_conversation(conv_id, title):
    _update_conv(conv_id, {"title": title, "updated_at": _now()})


def touch_conversation(conv_id):
    _update_conv(conv_id, {"updated_at": _now()})


def _update_conv(conv_id, data):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET doc = doc || %s::jsonb WHERE id = %s",
                (_jdump(data), conv_id),
            )
        c.commit()


def delete_conversation(conv_id):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM conversations WHERE id = %s", (conv_id,)
            )
            cur.execute(
                "DELETE FROM messages WHERE conversation_id = %s", (conv_id,)
            )
        c.commit()


# ---------------------------------------------------------------------------
# messages
# ---------------------------------------------------------------------------

def add_message(conversation_id, role, content, audio_b64=None, extra=None):
    doc = {
        "id": str(uuid.uuid4()),
        "conversation_id": conversation_id,
        "role": role,
        "content": content,
        "created_at": _now(),
    }
    if audio_b64:
        doc["audio_b64"] = audio_b64
    if extra:
        doc.update(extra)
    media_kind = doc.get("media_kind")
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "INSERT INTO messages"
                " (id, conversation_id, created_at, role, media_kind, doc)"
                " VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    doc["id"],
                    conversation_id,
                    doc["created_at"],
                    role,
                    media_kind,
                    _jdump(doc),
                ),
            )
        c.commit()
    touch_conversation(conversation_id)
    return doc


def get_messages(conversation_id):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT doc FROM messages WHERE conversation_id = %s"
                " ORDER BY created_at ASC",
                (conversation_id,),
            )
            return _rows(cur.fetchall())


def list_media(owner_id):
    out = []
    for ch in list_characters(owner_id=owner_id):
        conv_ids = [c["id"] for c in list_conversations(ch["id"])]
        if not conv_ids:
            continue
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT doc FROM messages WHERE conversation_id = ANY(%s)"
                    " AND media_kind IN ('photo','song','video')",
                    (conv_ids,),
                )
                for row in cur.fetchall():
                    m = dict(row["doc"])
                    out.append({
                        "char_id": ch["id"],
                        "char_name": ch.get("name", "Personaj"),
                        "char_avatar": ch.get("avatar", "🎭"),
                        "media_kind": m.get("media_kind"),
                        "song_name": m.get("song_name"),
                        "image_b64": m.get("image_b64"),
                        "song_b64": m.get("song_b64"),
                        "video_b64": m.get("video_b64"),
                        "created_at": m.get("created_at"),
                    })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def list_letters(owner_id):
    out = []
    for ch in list_characters(owner_id=owner_id):
        conv_ids = [c["id"] for c in list_conversations(ch["id"])]
        if not conv_ids:
            continue
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT doc FROM messages WHERE conversation_id = ANY(%s)"
                    " AND role = 'assistant'",
                    (conv_ids,),
                )
                for row in cur.fetchall():
                    m = dict(row["doc"])
                    content = m.get("content") or ""
                    if content.startswith("💌 O scrisoare pentru tine:"):
                        out.append({
                            "id": m.get("id"),
                            "char_id": ch["id"],
                            "char_name": ch.get("name", "Personaj"),
                            "char_avatar": ch.get("avatar", "🎭"),
                            "voice_id": ch.get("voice_id"),
                            "content": content,
                            "created_at": m.get("created_at"),
                        })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def list_song_names(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return []
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT doc FROM messages WHERE conversation_id = ANY(%s)"
                " AND media_kind = 'song' ORDER BY created_at ASC",
                (conv_ids,),
            )
            seen, out = set(), []
            for row in cur.fetchall():
                n = dict(row["doc"]).get("song_name")
                if n and n not in seen:
                    seen.add(n)
                    out.append(n)
    return out


def list_songs(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return []
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT doc FROM messages WHERE conversation_id = ANY(%s)"
                " AND media_kind = 'song' AND role = 'user'"
                " ORDER BY created_at ASC",
                (conv_ids,),
            )
            rows = []
            for row in cur.fetchall():
                d = dict(row["doc"])
                rows.append({
                    "id": d.get("id"),
                    "song_name": d.get("song_name"),
                    "song_b64": d.get("song_b64"),
                    "created_at": d.get("created_at"),
                })
    return rows


def delete_song(message_id):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("DELETE FROM messages WHERE id = %s", (message_id,))
        c.commit()


def rename_song(message_id, new_name):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE messages SET doc = doc || %s::jsonb WHERE id = %s",
                (_jdump({"song_name": new_name}), message_id),
            )
        c.commit()


def random_song(character_id):
    songs = list_songs(character_id)
    if not songs:
        return None
    import random
    playable = [s for s in songs if s.get("song_b64")]
    return random.choice(playable or songs)


def has_media(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return False
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM messages WHERE conversation_id = ANY(%s)"
                " AND media_kind IN ('photo','song','video') LIMIT 1",
                (conv_ids,),
            )
            return cur.fetchone() is not None


def random_media(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return None
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT doc FROM messages WHERE conversation_id = ANY(%s)"
                " AND media_kind IN ('photo','song')",
                (conv_ids,),
            )
            items = _rows(cur.fetchall())
    if not items:
        return None
    import random
    return random.choice(items)


def clear_messages(conversation_id):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM messages WHERE conversation_id = %s",
                (conversation_id,),
            )
        c.commit()


def set_reaction(message_id, emoji):
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "UPDATE messages SET doc = doc || %s::jsonb WHERE id = %s",
                (_jdump({"reaction": emoji}), message_id),
            )
        c.commit()


def update_message(message_id, content=None, audio_b64=None):
    """Actualizează conținutul sau audio-ul unui mesaj."""
    updates = []
    values = []
    
    if content is not None:
        updates.append("doc = jsonb_set(doc, '{content}', %s)")
        values.append(content)
    
    if audio_b64 is not None:
        updates.append("doc = doc || %s::jsonb")
        values.append(_jdump({"audio_b64": audio_b64}))
    
    if not updates:
        return
    
    values.append(message_id)
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE messages SET {', '.join(updates)} WHERE id = %s",
                values
            )
        c.commit()


# ==============================================================================
# Ambient Sound Library - Sunete ambientale
# ==============================================================================

def create_ambient(owner_id, name, category=None, description=None,
                    audio_b64=None, audio_name=None, duration=0.0,
                    tags=None, visibility="public", is_synthetic=False):
    """Creează un sunet ambiental în biblioteca de sunete."""
    import uuid
    from datetime import datetime

    amb_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()

    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                INSERT INTO ambient_library
                (id, owner_id, visibility, created_at, name, category, description,
                 audio_b64, audio_name, duration, tags, is_synthetic)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING id, name, visibility, category, created_at
            """, (amb_id, owner_id, visibility, now, name, category, description,
                  audio_b64, audio_name, duration, tags, is_synthetic))
            row = cur.fetchone()
        c.commit()

    return dict(zip(["id", "name", "visibility", "category", "created_at"], row))


def get_ambient(ambient_id):
    """Preia un sunet ambiental după ID."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, owner_id, visibility, created_at, name, category,
                       description, audio_b64, audio_name, duration, tags, is_synthetic
                FROM ambient_library WHERE id = %s
            """, (ambient_id,))
            row = cur.fetchone()
            cols = ["id", "owner_id", "visibility", "created_at", "name", "category",
                    "description", "audio_b64", "audio_name", "duration", "tags", "is_synthetic"]
    return dict(zip(cols, row)) if row else None


def get_public_ambients(category=None):
    """Preia toate sunetele ambientale publice."""
    with _conn() as c:
        with c.cursor() as cur:
            if category:
                cur.execute("""
                    SELECT id, owner_id, visibility, created_at, name, category,
                           description, audio_name, duration, tags, is_synthetic
                    FROM ambient_library
                    WHERE visibility = 'public' AND category = %s
                    ORDER BY created_at DESC
                """, (category,))
            else:
                cur.execute("""
                    SELECT id, owner_id, visibility, created_at, name, category,
                           description, audio_name, duration, tags, is_synthetic
                    FROM ambient_library
                    WHERE visibility = 'public'
                    ORDER BY created_at DESC
                """)
            rows = cur.fetchall()
            cols = ["id", "owner_id", "visibility", "created_at", "name", "category",
                    "description", "audio_name", "duration", "tags", "is_synthetic"]
    return [dict(zip(cols, r)) for r in rows]


def get_user_ambients(user_id):
    """Preia sunetele ambientale ale unui utilizator (publice + private)."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, owner_id, visibility, created_at, name, category,
                       description, audio_name, duration, tags, is_synthetic
                FROM ambient_library
                WHERE visibility = 'public' OR owner_id = %s
                ORDER BY created_at DESC
            """, (user_id,))
            rows = cur.fetchall()
            cols = ["id", "owner_id", "visibility", "created_at", "name", "category",
                    "description", "audio_name", "duration", "tags", "is_synthetic"]
    return [dict(zip(cols, r)) for r in rows]


def get_ambients_by_category():
    """Returnează toate categoriile disponibile cu sunete."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT category, COUNT(*) as count
                FROM ambient_library
                WHERE visibility = 'public' AND category IS NOT NULL
                GROUP BY category
                ORDER BY count DESC
            """)
            return [{"category": r[0], "count": r[1]} for r in cur.fetchall()]


def update_ambient(ambient_id, **kwargs):
    """Actualizează un sunet ambiental."""
    allowed = ["name", "category", "description", "visibility", "audio_b64",
               "audio_name", "duration", "tags"]
    updates = []
    values = []
    for k, v in kwargs.items():
        if k in allowed and v is not None:
            updates.append(f"{k} = %s")
            values.append(v)
    if not updates:
        return
    values.append(ambient_id)

    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE ambient_library SET {', '.join(updates)} WHERE id = %s",
                values
            )
        c.commit()


def delete_ambient(ambient_id, owner_id):
    """Șterge un sunet ambiental (doar proprietarul îl poate șterge)."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM ambient_library WHERE id = %s AND owner_id = %s",
                (ambient_id, owner_id)
            )
        c.commit()


def search_ambients(query, category=None):
    """Caută sunete ambientale după nume, descriere sau tags."""
    with _conn() as c:
        with c.cursor() as cur:
            base = """
                SELECT id, owner_id, visibility, created_at, name, category,
                       description, audio_name, duration, tags, is_synthetic
                FROM ambient_library
                WHERE visibility = 'public'
                  AND (name ILIKE %s OR description ILIKE %s OR %s = ANY(tags))
            """
            args = [f"%{query}%", f"%{query}%", query]
            if category:
                base += " AND category = %s"
                args.append(category)
            base += " ORDER BY created_at DESC"
            cur.execute(base, args)
            rows = cur.fetchall()
            cols = ["id", "owner_id", "visibility", "created_at", "name", "category",
                    "description", "audio_name", "duration", "tags", "is_synthetic"]
    return [dict(zip(cols, r)) for r in rows]


# ==============================================================================
# Seed - Populare bibliotecă sunete ambientale
# ==============================================================================

def seed_ambient_library():
    """Populează biblioteca de sunete ambientale cu presetări sintetice."""
    ambients = [
        # Transport și călătorii
        {"name": "Tren în mers", "category": "transport", "description": "Sunet de tren care circulă pe șine", "tags": ["tren", "transport", "călătorie"]},
        {"name": "Metrou", "category": "transport", "description": "Sunet de metrou care circulă", "tags": ["metrou", "transport", "urban"]},
        {"name": "Autobuz", "category": "transport", "description": "Sunet de autobuz care circulă", "tags": ["autobuz", "transport", "urban"]},
        {"name": "Stradă cu trafic", "category": "transport", "description": "Sunet de stradă cu mașini și trafic", "tags": ["stradă", "trafic", "mașini", "urban"]},
        
        # Bucătărie și cafea
        {"name": "Cafea la espressor", "category": "cafea", "description": "Sunet de preparare a cafelei la espressor", "tags": ["cafea", "espressor", "bucătărie"]},
        {"name": "Apă care fierbe", "category": "cafea", "description": "Sunet de apă care fierbe", "tags": ["apă", "fierbere", "bucătărie"]},
        {"name": "Farfurii și tacâmuri", "category": "cafea", "description": "Sunet de vase și tacâmuri", "tags": ["farfurii", "tacâmuri", "bucătărie"]},
        {"name": "Frigider", "category": "cafea", "description": "Sunet de frigider care funcționează", "tags": ["frigider", "bucătărie", "electrocasnice"]},
        
        # Camere și case
        {"name": "Ușă care se deschide", "category": "cameră", "description": "Sunet de ușă care se deschide", "tags": ["ușă", "cameră", "casă"]},
        {"name": "Parchet - pași", "category": "cameră", "description": "Sunet de pași pe parchet", "tags": ["pași", "parchet", "cameră"]},
        {"name": "Lift", "category": "cameră", "description": "Sunet de lift care urcă și coboară", "tags": ["lift", "clădire", "cameră"]},
        
        # Natură și vreme
        {"name": "Ploaie ușoară", "category": "natură", "description": "Sunet de ploaie ușoară", "tags": ["ploaie", "natură", "vreme"]},
        {"name": "Ploaie puternică", "category": "natură", "description": "Sunet de ploaie puternică", "tags": ["ploaie", "furtună", "vreme"]},
        {"name": "Furtună cu tunete", "category": "natură", "description": "Sunet de furtună cu tunete", "tags": ["furtună", "tunete", "vreme"]},
        {"name": "Vânt puternic", "category": "natură", "description": "Sunet de vânt puternic", "tags": ["vânt", "natură", "vreme"]},
        {"name": "Pădure", "category": "natură", "description": "Sunet de pădure cu păsări", "tags": ["pădure", "natură", "păsări"]},
        {"name": "Râu care curge", "category": "natură", "description": "Sunet de apă care curge", "tags": ["râu", "apă", "natură"]},
        {"name": "Valuri de mare", "category": "natură", "description": "Sunet de valuri la mare", "tags": ["mare", "valuri", "plajă"]},
        {"name": "Șemineu", "category": "natură", "description": "Sunet de foc în șemineu", "tags": ["foc", "șemineu", "casă"]},
        {"name": "Zăpadă", "category": "natură", "description": "Sunet de ninsoare", "tags": ["zăpadă", "iarnă", "natură"]},
        
        # Animale
        {"name": "Câine care latră", "category": "animale", "description": "Sunet de câine care latră", "tags": ["câine", "lătrat", "animal"]},
        {"name": "Pisică care toarce", "category": "animale", "description": "Sunet de pisică care toarce", "tags": ["pisică", "tors", "animal"]},
        {"name": "Păsări în natură", "category": "animale", "description": "Sunet de păsări în natură", "tags": ["păsări", "natură", "cânt"]},
        {"name": "Greieri noaptea", "category": "animale", "description": "Sunet de greieri noaptea", "tags": ["greieri", "noapte", "natură"]},
        
        # Oameni și activități
        {"name": "Conversație în cafenea", "category": "oameni", "description": "Sunet de conversații într-o cafenea", "tags": ["cafenea", "conversație", "oameni"]},
        {"name": "Copii care se joacă", "category": "oameni", "description": "Sunet de copii care se joacă", "tags": ["copii", "joacă", "oameni"]},
        {"name": "Restaurant aglomerat", "category": "oameni", "description": "Sunet de restaurant cu mulți oameni", "tags": ["restaurant", "oameni", "aglomerat"]},
        
        # Tehnologie
        {"name": "Televizor în fundal", "category": "tehnologie", "description": "Sunet de televizor care merge în fundal", "tags": ["televizor", "tehnologie", "fundal"]},
        {"name": "Calculator", "category": "tehnologie", "description": "Sunet de calculator care funcționează", "tags": ["calculator", "tehnologie", "birou"]},
        {"name": "Notificări telefon", "category": "tehnologie", "description": "Sunet de notificări de telefon", "tags": ["telefon", "notificări", "tehnologie"]},
        
        # Spații publice
        {"name": "Supermarket", "category": "public", "description": "Sunet de supermarket cu oameni", "tags": ["supermarket", "public", "magazin"]},
        {"name": "Gara", "category": "public", "description": "Sunet de gară cu anunțuri", "tags": ["gară", "public", "transport"]},
        {"name": "Bibliotecă", "category": "public", "description": "Sunet de bibliotecă liniștită", "tags": ["bibliotecă", "public", "liniște"]},
    ]
    
    for amb in ambients:
        try:
            create_ambient(
                owner_id="system",
                name=amb["name"],
                category=amb["category"],
                description=amb["description"],
                tags=amb["tags"],
                visibility="public",
                is_synthetic=True
            )
            print(f"✅ Adăugat: {amb['name']}")
        except Exception as e:
            print(f"⚠️ {amb['name']}: {e}")


# ==============================================================================
# Voice Library - Clonare vocală
# ==============================================================================

def create_voice(owner_id, name, sample_b64=None, sample_name=None, 
                 description=None, visibility="public", speaker_embedding=None,
                 voice_params=None):
    """Creează o voce în biblioteca de voci."""
    import uuid
    from datetime import datetime
    
    voice_id = str(uuid.uuid4())
    created_at = datetime.utcnow().isoformat()
    
    doc = {
        "name": name,
        "description": description or "",
        "voice_params": voice_params or {},
    }
    
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                INSERT INTO voice_library 
                (id, owner_id, visibility, created_at, name, description, 
                 sample_b64, sample_name, speaker_embedding, voice_params)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (voice_id, owner_id, visibility, created_at, name, 
                  description or "", sample_b64, sample_name,
                  _jdump(speaker_embedding) if speaker_embedding else None,
                  _jdump(doc)))
        c.commit()
    
    return {"id": voice_id, "name": name, "visibility": visibility, 
            "description": description, "created_at": created_at}


def get_voice(voice_id):
    """Preia o voce după ID."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, owner_id, visibility, created_at, name, description,
                       sample_b64, sample_name, speaker_embedding, voice_params
                FROM voice_library WHERE id = %s
            """, (voice_id,))
            row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "owner_id": row[1],
        "visibility": row[2],
        "created_at": row[3],
        "name": row[4],
        "description": row[5],
        "sample_b64": row[6],
        "sample_name": row[7],
        "speaker_embedding": row[8],
        "voice_params": row[9] if len(row) > 9 else {},
    }


def get_public_voices():
    """Preia toate vocile publice."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, owner_id, visibility, created_at, name, description,
                       sample_name
                FROM voice_library 
                WHERE visibility = 'public'
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()
    return [
        {"id": r[0], "owner_id": r[1], "visibility": r[2], 
         "created_at": r[3], "name": r[4], "description": r[5],
         "sample_name": r[6]}
        for r in rows
    ]


def get_user_voices(user_id):
    """Preia vocile unui utilizator (publice + private)."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, owner_id, visibility, created_at, name, description,
                       sample_name
                FROM voice_library 
                WHERE owner_id = %s OR visibility = 'public'
                ORDER BY created_at DESC
            """, (user_id,))
            rows = cur.fetchall()
    return [
        {"id": r[0], "owner_id": r[1], "visibility": r[2], 
         "created_at": r[3], "name": r[4], "description": r[5],
         "sample_name": r[6]}
        for r in rows
    ]


def update_voice(voice_id, **kwargs):
    """Actualizează o voce."""
    allowed = ["name", "description", "visibility", "sample_b64", 
               "sample_name", "speaker_embedding", "voice_params"]
    updates = []
    values = []
    for key, val in kwargs.items():
        if key in allowed:
            updates.append(f"{key} = %s")
            if key == "speaker_embedding" or key == "voice_params":
                values.append(_jdump(val))
            else:
                values.append(val)
    
    if not updates:
        return
    
    values.append(voice_id)
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                f"UPDATE voice_library SET {', '.join(updates)} WHERE id = %s",
                values
            )
        c.commit()


def delete_voice(voice_id, owner_id):
    """Șterge o voce (doar proprietarul o poate șterge)."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute(
                "DELETE FROM voice_library WHERE id = %s AND owner_id = %s",
                (voice_id, owner_id)
            )
        c.commit()


def search_voices(query):
    """Caută voci după nume sau descriere."""
    with _conn() as c:
        with c.cursor() as cur:
            cur.execute("""
                SELECT id, owner_id, visibility, created_at, name, description,
                       sample_name
                FROM voice_library 
                WHERE visibility = 'public' 
                  AND (name ILIKE %s OR description ILIKE %s)
                ORDER BY created_at DESC
            """, (f"%{query}%", f"%{query}%"))
            rows = cur.fetchall()
    return [
        {"id": r[0], "owner_id": r[1], "visibility": r[2], 
         "created_at": r[3], "name": r[4], "description": r[5],
         "sample_name": r[6]}
        for r in rows
    ]
