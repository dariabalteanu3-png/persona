"""
db_turso.py — backend de persistență pe Turso (libSQL) via HTTP API.

Folosește variabilele de mediu:
  TURSO_URL   = "libsql://<org>-<db>.aws-<reg>.turso.io"   (sau https://...)
  TURSO_TOKEN = tokenul de autentificare primit de la Turso

Stocare: o tabelă per colecție, cu coloana `id` (primary key) și coloana `doc`
(JSON complet). Funcțiile expuse păstrează API-ul identic cu db_pg.py / db_mg.py,
astfel încât `db.py` poate ruta transparent spre Turso.

Conexiunea se face prin endpoint-ul HTTP `POST {host}/v2/pipeline`, ceea ce evită
dependența de WebSocket-ul Hrana (indisponibil pe unele rețele).
"""

import os
import json
import uuid
import random
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TURSO_URL = os.environ.get("TURSO_URL")
TURSO_TOKEN = os.environ.get("TURSO_TOKEN")

_HTTP_URL = ""
if TURSO_URL:
    _HTTP_URL = TURSO_URL.replace("libsql://", "https://").replace("wss://", "https://")
    if not _HTTP_URL.endswith(".turso.io"):
        _HTTP_URL = TURSO_URL

_COLLECTIONS = [
    "users", "sessions", "email_codes",
    "characters", "conversations", "messages",
    "chat_groups", "group_messages",
    "voice_library", "ambient_library",
]

_session = requests.Session()


# ---------------------------------------------------------------------------
# Conexiune & SQL de bază
# ---------------------------------------------------------------------------

def _cell(value):
    t = value.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(value["value"])
    if t == "float":
        return float(value["value"])
    return value.get("value")


def _post(requests_list, timeout=20):
    """Trimite o listă de cereri SQL către /v2/pipeline și întoarce rezultatele."""
    if not _HTTP_URL or not TURSO_TOKEN:
        raise RuntimeError("TURSO_URL sau TURSO_TOKEN nu sunt setate")
    resp = _session.post(
        f"{_HTTP_URL}/v2/pipeline",
        headers={"Authorization": f"Bearer {TURSO_TOKEN}"},
        json={"requests": requests_list},
        timeout=timeout,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Turso HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    out = []
    for item in data.get("results", []):
        if item.get("type") == "error":
            raise RuntimeError(item.get("error", {}).get("message", "Turso error"))
        resp_item = item.get("response", {})
        if resp_item.get("type") == "execute":
            out.append(resp_item.get("result") or {})
    return out


def _exec(sql, params=None):
    params = params or []
    args = [_to_arg(p) for p in params]
    _post([{"type": "execute", "stmt": {"sql": sql, "args": args}}])


def _fetch(sql, params=None):
    params = params or []
    args = [_to_arg(p) for p in params]
    results = _post([{"type": "execute", "stmt": {"sql": sql, "args": args}}])
    result = results[0]
    cols = [c["name"] for c in result.get("cols", [])]
    rows = []
    for r in result.get("rows", []):
        rows.append({cols[i]: _cell(r[i]) for i in range(len(cols))})
    return rows


def _to_arg(value):
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": str(value)}
    return {"type": "text", "value": str(value)}


# ---------------------------------------------------------------------------
# Schema & inițializare
# ---------------------------------------------------------------------------

def _init_schema():
    stmts = []
    for coll in _COLLECTIONS:
        stmts.append(
            f"CREATE TABLE IF NOT EXISTS t_{coll} "
            f"(id TEXT PRIMARY KEY, doc TEXT NOT NULL)"
        )
    stmts.append(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
        "ON t_users (json_extract(doc, '$.email'))"
    )
    stmts.append(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token "
        "ON t_sessions (json_extract(doc, '$.token'))"
    )
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_characters_owner "
        "ON t_characters (json_extract(doc, '$.owner_id'))"
    )
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_conversations_char "
        "ON t_conversations (json_extract(doc, '$.character_id'))"
    )
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_messages_conv "
        "ON t_messages (json_extract(doc, '$.conversation_id'))"
    )
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_groups_owner "
        "ON t_chat_groups (json_extract(doc, '$.owner_id'))"
    )
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_groupmsg_group "
        "ON t_group_messages (json_extract(doc, '$.group_id'))"
    )
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_voices_owner "
        "ON t_voice_library (json_extract(doc, '$.owner_id'))"
    )
    stmts.append(
        "CREATE INDEX IF NOT EXISTS idx_ambients_owner "
        "ON t_ambient_library (json_extract(doc, '$.owner_id'))"
    )
    requests_list = [
        {"type": "execute", "stmt": {"sql": s, "args": []}} for s in stmts
    ]
    _post(requests_list)


def turso_ready():
    """Verifică dacă Turso este configurat și accesibil; inițializează schema."""
    if not TURSO_URL or not TURSO_TOKEN:
        return False
    try:
        _fetch("SELECT 1 AS ok")
        _init_schema()
        if _count("ambient_library") == 0:
            try:
                seed_ambient_library()
            except Exception as _seed_err:
                print(f"[db] Ambient seed warning: {_seed_err}")
        return True
    except Exception as _e:
        import sys
        print(f"[db] Turso indisponibil la pornire ({type(_e).__name__}). "
              f"Folosesc backend-ul de rezervă (datele NU persistă la restart).",
              file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Helpers CRUD generice (colecție = tabelă, doc = JSON)
# ---------------------------------------------------------------------------

def _field_expr(field):
    return f"json_extract(doc, '$.{field}')"


def _where(q):
    if not q:
        return "", []
    if "$or" in q:
        sub_parts, sub_params = [], []
        for sub in q["$or"]:
            w, p = _where(sub)
            if w:
                sub_parts.append(f"({w})")
                sub_params.extend(p)
        if sub_parts:
            return "(" + " OR ".join(sub_parts) + ")", sub_params
    parts, params = [], []
    for k, cond in q.items():
        if k == "$or":
            continue
        field = _field_expr(k)
        if isinstance(cond, dict):
            if "$in" in cond:
                vals = cond["$in"]
                if not vals:
                    parts.append("0")
                else:
                    ph = ", ".join(["?"] * len(vals))
                    parts.append(f"{field} IN ({ph})")
                    params.extend(vals)
            elif "$ne" in cond:
                parts.append(f"{field} != ?")
                params.append(cond["$ne"])
            elif "$exists" in cond:
                parts.append(f"{field} IS NOT NULL" if cond["$exists"] else f"{field} IS NULL")
            elif "$regex" in cond:
                parts.append(f"lower({field}) LIKE lower(?)")
                params.append(f"%{cond['$regex']}%")
            elif "$gt" in cond:
                parts.append(f"{field} > ?")
                params.append(cond["$gt"])
            elif "$lt" in cond:
                parts.append(f"{field} < ?")
                params.append(cond["$lt"])
            else:
                parts.append(f"{field} = ?")
                params.append(cond.get("$eq"))
        elif cond is None:
            parts.append(f"{field} IS NULL")
        else:
            parts.append(f"{field} = ?")
            params.append(cond)
    return " AND ".join(parts), params


def _insert(coll, doc):
    doc = dict(doc)
    if "id" not in doc or not doc.get("id"):
        doc["id"] = str(uuid.uuid4())
    _exec(
        f"INSERT OR REPLACE INTO t_{coll} (id, doc) VALUES (?, ?)",
        [doc["id"], json.dumps(doc, ensure_ascii=False)],
    )
    return doc


def _find(coll, q=None, sort=None, limit=None):
    where, params = _where(q or {})
    sql = f"SELECT doc FROM t_{coll}"
    if where:
        sql += f" WHERE {where}"
    if sort:
        sql += f" ORDER BY {sort}"
    if limit is not None:
        sql += f" LIMIT {limit}"
    rows = _fetch(sql, params)
    return [json.loads(r["doc"]) for r in rows]


def _find_one(coll, q=None):
    docs = _find(coll, q, limit=1)
    return docs[0] if docs else None


def _count(coll, q=None):
    where, params = _where(q or {})
    sql = f"SELECT count(*) AS c FROM t_{coll}"
    if where:
        sql += f" WHERE {where}"
    return int(_fetch(sql, params)[0]["c"])


def _update(coll, q, update):
    docs = _find(coll, q)
    for doc in docs:
        for k, v in update.items():
            if k == "$set" and isinstance(v, dict):
                for f, val in v.items():
                    doc[f] = val
            elif k == "$inc" and isinstance(v, dict):
                for f, val in v.items():
                    doc[f] = (doc.get(f) or 0) + val
            elif k == "$unset" and isinstance(v, dict):
                for f in v:
                    doc.pop(f, None)
            elif k == "$push" and isinstance(v, dict):
                for f, val in v.items():
                    doc.setdefault(f, []).append(val)
            else:
                doc[k] = v
        _insert(coll, doc)
    return len(docs)


def _delete(coll, q):
    ids = [d["id"] for d in _find(coll, q)]
    for _id in ids:
        _exec("DELETE FROM t_{coll} WHERE id = ?".format(coll=coll), [_id])
    return len(ids)


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_config(name, default=None):
    return os.environ.get(name, default)


# ---------------------------------------------------------------------------
# users
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
    return _insert("users", doc)


def get_user_by_email(email):
    return _find_one("users", {"email": email})


def get_user_by_id(uid):
    return _find_one("users", {"id": uid})


def update_user(uid, data):
    _update("users", {"id": uid}, data)
    return get_user_by_id(uid)


def set_user_verified(email):
    _update("users", {"email": email}, {"$set": {"verified": True}})


def set_user_password(email, password_hash):
    _update("users", {"email": email}, {"$set": {"password_hash": password_hash}})


def toggle_favorite(user_id, char_id):
    u = get_user_by_id(user_id)
    favs = list((u or {}).get("favorites") or [])
    if char_id in favs:
        favs.remove(char_id)
        state = False
    else:
        favs.append(char_id)
        state = True
    _update("users", {"id": user_id}, {"$set": {"favorites": favs}})
    return state


def get_favorites(user_id):
    u = get_user_by_id(user_id)
    return list((u or {}).get("favorites") or []) if u else []


def favorite_counts():
    counts = {}
    for u in _find("users"):
        for cid in (u.get("favorites") or []):
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def delete_user(user_id):
    u = get_user_by_id(user_id)
    if not u:
        return
    for ch in list_characters(owner_id=user_id):
        delete_character(ch["id"])
    _delete("sessions", {"user_id": user_id})
    _delete("email_codes", {"email": u.get("email")})
    _delete("users", {"id": user_id})


def increment_stat(char_id, field, n=1):
    _update("characters", {"id": char_id}, {"$inc": {field: n}})


def character_message_count(char_id):
    conv_ids = [c["id"] for c in list_conversations(char_id)]
    if not conv_ids:
        return 0
    return _count("messages", {"conversation_id": {"$in": conv_ids}})


# ---------------------------------------------------------------------------
# email codes / sessions
# ---------------------------------------------------------------------------

def create_email_code(email, code, purpose, ttl_minutes=15):
    _delete("email_codes", {"email": email, "purpose": purpose})
    _insert("email_codes", {
        "email": email, "code": code, "purpose": purpose,
        "created_at": _now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat(),
    })


def check_email_code(email, code, purpose):
    doc = _find_one("email_codes",
                    {"email": email, "purpose": purpose, "code": (code or "").strip()})
    if not doc:
        return False
    try:
        if datetime.fromisoformat(doc["expires_at"]) < datetime.now(timezone.utc):
            _delete("email_codes", {"id": doc["id"]})
            return False
    except Exception:
        pass
    _delete("email_codes", {"id": doc["id"]})
    return True


def create_session(token, user_id, expires_days=30):
    _insert("sessions", {
        "token": token, "user_id": user_id,
        "created_at": _now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat(),
    })


def get_session(token):
    s = _find_one("sessions", {"token": token})
    if not s:
        return None
    try:
        if datetime.fromisoformat(s["expires_at"]) < datetime.now(timezone.utc):
            _delete("sessions", {"token": token})
            return None
    except Exception:
        pass
    return s


def delete_session(token):
    _delete("sessions", {"token": token})


# ---------------------------------------------------------------------------
# characters
# ---------------------------------------------------------------------------

def create_character(data):
    doc = {"id": str(uuid.uuid4()), "created_at": _now(), **data}
    return _insert("characters", doc)


def list_characters(owner_id=None):
    q = {} if owner_id is None else {"owner_id": owner_id}
    return _find("characters", q, sort="json_extract(doc, '$.created_at') DESC")


def reassign_owner(old_owner_id, new_owner_id):
    _update("characters", {"owner_id": old_owner_id},
            {"$set": {"owner_id": new_owner_id}})


def list_public_characters():
    return _find("characters", {"visibility": "public"},
                 sort="json_extract(doc, '$.created_at') DESC")


def get_character(cid):
    return _find_one("characters", {"id": cid})


def update_character(cid, data):
    _update("characters", {"id": cid}, data)
    return get_character(cid)


def delete_user_voices(user_id):
    fields = ["voice_id", "voice_name", "voice_sample_b64", "voice_sample_name",
              "voice_ref_text", "voice_stability", "voice_similarity",
              "voice_style", "voice_tone"]
    chars = _find("characters", {"owner_id": user_id})
    for ch in chars:
        for f in fields:
            ch.pop(f, None)
        _insert("characters", ch)
    return len(chars)


def delete_character(cid):
    conv_ids = [c["id"] for c in list_conversations(cid)]
    _delete("characters", {"id": cid})
    _delete("conversations", {"character_id": cid})
    if conv_ids:
        _delete("messages", {"conversation_id": {"$in": conv_ids}})


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
    return _insert("conversations", doc)


def list_conversations(character_id):
    return _find("conversations", {"character_id": character_id},
                 sort="json_extract(doc, '$.created_at') ASC")


def get_conversation(conv_id):
    return _find_one("conversations", {"id": conv_id})


def rename_conversation(conv_id, title):
    _update("conversations", {"id": conv_id},
            {"$set": {"title": title, "updated_at": _now()}})


def touch_conversation(conv_id):
    _update("conversations", {"id": conv_id}, {"$set": {"updated_at": _now()}})


def delete_conversation(conv_id):
    _delete("conversations", {"id": conv_id})
    _delete("messages", {"conversation_id": conv_id})


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
    _insert("messages", doc)
    touch_conversation(conversation_id)
    return doc


def get_messages(conversation_id):
    return _find("messages", {"conversation_id": conversation_id},
                 sort="json_extract(doc, '$.created_at') ASC")


def clear_messages(conversation_id):
    _delete("messages", {"conversation_id": conversation_id})


def set_reaction(message_id, emoji):
    _update("messages", {"id": message_id}, {"$set": {"reaction": emoji}})


def update_message(message_id, content=None, audio_b64=None):
    update = {}
    if content is not None:
        update["content"] = content
    if audio_b64 is not None:
        update["audio_b64"] = audio_b64
    if update:
        _update("messages", {"id": message_id}, {"$set": update})


# ---------------------------------------------------------------------------
# chat de grup
# ---------------------------------------------------------------------------

def create_group(owner_id, name, character_ids):
    doc = {
        "id": str(uuid.uuid4()),
        "owner_id": owner_id,
        "name": name,
        "character_ids": list(character_ids or []),
        "created_at": _now(),
    }
    return _insert("chat_groups", doc)


def list_groups(owner_id):
    return _find("chat_groups", {"owner_id": owner_id},
                 sort="json_extract(doc, '$.created_at') DESC")


def get_group(group_id):
    return _find_one("chat_groups", {"id": group_id})


def delete_group(group_id):
    _delete("chat_groups", {"id": group_id})
    _delete("group_messages", {"group_id": group_id})


def add_group_message(group_id, speaker_id, speaker_name, content, audio_b64=None):
    doc = {
        "id": str(uuid.uuid4()),
        "group_id": group_id,
        "speaker_id": speaker_id,
        "speaker_name": speaker_name,
        "content": content,
        "created_at": _now(),
    }
    if audio_b64:
        doc["audio_b64"] = audio_b64
    _insert("group_messages", doc)
    return doc


def get_group_messages(group_id):
    return _find("group_messages", {"group_id": group_id},
                 sort="json_extract(doc, '$.created_at') ASC")


def clear_group_messages(group_id):
    _delete("group_messages", {"group_id": group_id})


# ---------------------------------------------------------------------------
# media / scrisori / melodii
# ---------------------------------------------------------------------------

def list_media(owner_id):
    out = []
    for ch in list_characters(owner_id=owner_id):
        conv_ids = [c["id"] for c in list_conversations(ch["id"])]
        if not conv_ids:
            continue
        for m in _find("messages", {
            "conversation_id": {"$in": conv_ids},
            "media_kind": {"$in": ["photo", "song", "video"]},
        }):
            out.append({
                "char_id": ch["id"], "char_name": ch.get("name", "Personaj"),
                "char_avatar": ch.get("avatar", "🎭"),
                "media_kind": m.get("media_kind"), "song_name": m.get("song_name"),
                "image_b64": m.get("image_b64"), "song_b64": m.get("song_b64"),
                "video_b64": m.get("video_b64"), "created_at": m.get("created_at"),
            })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def list_letters(owner_id):
    out = []
    for ch in list_characters(owner_id=owner_id):
        conv_ids = [c["id"] for c in list_conversations(ch["id"])]
        if not conv_ids:
            continue
        for m in _find("messages",
                       {"conversation_id": {"$in": conv_ids}, "role": "assistant"}):
            content = m.get("content") or ""
            if content.startswith("💌 O scrisoare pentru tine:"):
                out.append({
                    "id": m.get("id"), "char_id": ch["id"],
                    "char_name": ch.get("name", "Personaj"),
                    "char_avatar": ch.get("avatar", "🎭"),
                    "voice_id": ch.get("voice_id"),
                    "content": content, "created_at": m.get("created_at"),
                })
    out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return out


def _conv_ids_or_empty(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return None
    return conv_ids


def list_song_names(character_id):
    conv_ids = _conv_ids_or_empty(character_id)
    if not conv_ids:
        return []
    cur = _find("messages", {
        "conversation_id": {"$in": conv_ids}, "media_kind": "song",
    }, sort="json_extract(doc, '$.created_at') ASC")
    seen, out = set(), []
    for m in cur:
        n = m.get("song_name")
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def list_songs(character_id):
    conv_ids = _conv_ids_or_empty(character_id)
    if not conv_ids:
        return []
    return _find("messages", {
        "conversation_id": {"$in": conv_ids},
        "media_kind": "song", "role": "user",
    }, sort="json_extract(doc, '$.created_at') ASC")


def delete_song(message_id):
    _delete("messages", {"id": message_id})


def rename_song(message_id, new_name):
    _update("messages", {"id": message_id}, {"$set": {"song_name": new_name}})


def random_song(character_id):
    songs = list_songs(character_id)
    if not songs:
        return None
    playable = [s for s in songs if s.get("song_b64")]
    return random.choice(playable or songs)


def has_media(character_id):
    conv_ids = _conv_ids_or_empty(character_id)
    if not conv_ids:
        return False
    return _count("messages", {
        "conversation_id": {"$in": conv_ids},
        "media_kind": {"$in": ["photo", "song", "video"]},
    }) > 0


def random_media(character_id):
    conv_ids = _conv_ids_or_empty(character_id)
    if not conv_ids:
        return None
    items = _find("messages", {
        "conversation_id": {"$in": conv_ids},
        "media_kind": {"$in": ["photo", "song"]},
    })
    if not items:
        return None
    return random.choice(items)


# ---------------------------------------------------------------------------
# Voice Library
# ---------------------------------------------------------------------------

def create_voice(owner_id, name, sample_b64=None, sample_name=None,
                 description=None, visibility="public", speaker_embedding=None,
                 voice_params=None):
    voice_id = str(uuid.uuid4())
    doc = {
        "id": voice_id,
        "owner_id": owner_id,
        "visibility": visibility,
        "created_at": datetime.utcnow().isoformat(),
        "name": name,
        "description": description or "",
        "sample_b64": sample_b64,
        "sample_name": sample_name,
        "speaker_embedding": speaker_embedding,
        "voice_params": voice_params or {},
    }
    _insert("voice_library", doc)
    return {"id": voice_id, "name": name, "visibility": visibility,
            "description": description, "created_at": doc["created_at"]}


def get_voice(voice_id):
    return _find_one("voice_library", {"id": voice_id})


def _strip_sample(docs):
    for d in docs:
        d.pop("sample_b64", None)
    return docs


def get_public_voices():
    return _strip_sample(_find("voice_library", {"visibility": "public"},
                               sort="json_extract(doc, '$.created_at') DESC"))


def get_user_voices(user_id):
    return _strip_sample(_find("voice_library", {
        "$or": [{"owner_id": user_id}, {"visibility": "public"}],
    }, sort="json_extract(doc, '$.created_at') DESC"))


def update_voice(voice_id, **kwargs):
    allowed = ["name", "description", "visibility", "sample_b64",
               "sample_name", "speaker_embedding", "voice_params"]
    update = {k: v for k, v in kwargs.items() if k in allowed}
    if update:
        _update("voice_library", {"id": voice_id}, {"$set": update})


def delete_voice(voice_id, owner_id):
    _delete("voice_library", {"id": voice_id, "owner_id": owner_id})


def search_voices(query):
    return _strip_sample(_find("voice_library", {
        "visibility": "public",
        "$or": [
            {"name": {"$regex": query}},
            {"description": {"$regex": query}},
        ],
    }, sort="json_extract(doc, '$.created_at') DESC"))


# ---------------------------------------------------------------------------
# Ambient Sound Library
# ---------------------------------------------------------------------------

def create_ambient(owner_id, name, category=None, description=None,
                   audio_b64=None, audio_name=None, duration=0.0,
                   tags=None, visibility="public", is_synthetic=False):
    amb_id = str(uuid.uuid4())
    doc = {
        "id": amb_id,
        "owner_id": owner_id,
        "visibility": visibility,
        "created_at": datetime.utcnow().isoformat(),
        "name": name,
        "category": category,
        "description": description or "",
        "audio_b64": audio_b64,
        "audio_name": audio_name,
        "duration": duration,
        "tags": tags or [],
        "is_synthetic": is_synthetic,
    }
    _insert("ambient_library", doc)
    return {"id": amb_id, "name": name, "visibility": visibility,
            "category": category, "created_at": doc["created_at"]}


def get_ambient(ambient_id):
    return _find_one("ambient_library", {"id": ambient_id})


def _strip_audio(docs):
    for d in docs:
        d.pop("audio_b64", None)
    return docs


def get_public_ambients(category=None):
    q = {"visibility": "public"}
    if category:
        q["category"] = category
    return _strip_audio(_find("ambient_library", q,
                              sort="json_extract(doc, '$.created_at') DESC"))


def get_user_ambients(user_id):
    return _strip_audio(_find("ambient_library", {
        "$or": [{"owner_id": user_id}, {"visibility": "public"}],
    }, sort="json_extract(doc, '$.created_at') DESC"))


def get_ambients_by_category():
    counts = {}
    for a in _find("ambient_library", {"visibility": "public"}):
        cat = a.get("category")
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    return [{"category": k, "count": v}
            for k, v in sorted(counts.items(), key=lambda x: -x[1])]


def update_ambient(ambient_id, **kwargs):
    allowed = ["name", "category", "description", "visibility", "audio_b64",
               "audio_name", "duration", "tags", "is_synthetic"]
    update = {k: v for k, v in kwargs.items() if k in allowed}
    if update:
        _update("ambient_library", {"id": ambient_id}, {"$set": update})


def delete_ambient(ambient_id, owner_id):
    _delete("ambient_library", {"id": ambient_id, "owner_id": owner_id})


def search_ambients(query, category=None):
    q = {
        "visibility": "public",
        "$or": [
            {"name": {"$regex": query}},
            {"description": {"$regex": query}},
            {"tags": {"$regex": query}},
        ],
    }
    if category:
        q["category"] = category
    return _strip_audio(_find("ambient_library", q,
                              sort="json_extract(doc, '$.created_at') DESC"))


def seed_ambient_library():
    ambients = [
        {"name": "Tren în mers", "category": "transport", "description": "Sunet de tren care circulă pe șine", "tags": ["tren", "transport", "călătorie"]},
        {"name": "Metrou", "category": "transport", "description": "Sunet de metrou care circulă", "tags": ["metrou", "transport", "urban"]},
        {"name": "Autobuz", "category": "transport", "description": "Sunet de autobuz care circulă", "tags": ["autobuz", "transport", "urban"]},
        {"name": "Stradă cu trafic", "category": "transport", "description": "Sunet de stradă cu mașini și trafic", "tags": ["stradă", "trafic", "mașini", "urban"]},
        {"name": "Ploaie ușoară", "category": "natură", "description": "Sunet de ploaie ușoară", "tags": ["ploaie", "natură", "vreme"]},
        {"name": "Ploaie puternică", "category": "natură", "description": "Sunet de ploaie puternică", "tags": ["ploaie", "furtună", "vreme"]},
        {"name": "Furtună cu tunete", "category": "natură", "description": "Sunet de furtună cu tunete", "tags": ["furtună", "tunete", "vreme"]},
        {"name": "Vânt puternic", "category": "natură", "description": "Sunet de vânt puternic", "tags": ["vânt", "natură", "vreme"]},
        {"name": "Pădure", "category": "natură", "description": "Sunet de pădure cu păsări", "tags": ["pădure", "natură", "păsări"]},
        {"name": "Râu care curge", "category": "natură", "description": "Sunet de apă care curge", "tags": ["râu", "apă", "natură"]},
        {"name": "Valuri de mare", "category": "natură", "description": "Sunet de valuri la mare", "tags": ["mare", "valuri", "plajă"]},
        {"name": "Șemineu", "category": "natură", "description": "Sunet de foc în șemineu", "tags": ["foc", "șemineu", "casă"]},
        {"name": "Zăpadă", "category": "natură", "description": "Sunet de ninsoare", "tags": ["zăpadă", "iarnă", "natură"]},
        {"name": "Câine care latră", "category": "animale", "description": "Sunet de câine care latră", "tags": ["câine", "lătrat", "animal"]},
        {"name": "Pisică care toarce", "category": "animale", "description": "Sunet de pisică care toarce", "tags": ["pisică", "tors", "animal"]},
        {"name": "Păsări în natură", "category": "animale", "description": "Sunet de păsări în natură", "tags": ["păsări", "natură", "cânt"]},
        {"name": "Greieri noaptea", "category": "animale", "description": "Sunet de greieri noaptea", "tags": ["greieri", "noapte", "natură"]},
        {"name": "Conversație în cafenea", "category": "oameni", "description": "Sunet de conversații într-o cafenea", "tags": ["cafenea", "conversație", "oameni"]},
        {"name": "Copii care se joacă", "category": "oameni", "description": "Sunet de copii care se joacă", "tags": ["copii", "joacă", "oameni"]},
        {"name": "Restaurant aglomerat", "category": "oameni", "description": "Sunet de restaurant cu mulți oameni", "tags": ["restaurant", "oameni", "aglomerat"]},
        {"name": "Televizor în fundal", "category": "tehnologie", "description": "Sunet de televizor care merge în fundal", "tags": ["televizor", "tehnologie", "fundal"]},
        {"name": "Calculator", "category": "tehnologie", "description": "Sunet de calculator care funcționează", "tags": ["calculator", "tehnologie", "birou"]},
        {"name": "Notificări telefon", "category": "tehnologie", "description": "Sunet de notificări de telefon", "tags": ["telefon", "notificări", "tehnologie"]},
        {"name": "Supermarket", "category": "public", "description": "Sunet de supermarket cu oameni", "tags": ["supermarket", "public", "magazin"]},
        {"name": "Gara", "category": "public", "description": "Sunet de gară cu anunțuri", "tags": ["gară", "public", "transport"]},
        {"name": "Bibliotecă", "category": "public", "description": "Sunet de bibliotecă liniștită", "tags": ["bibliotecă", "public", "liniște"]},
        {"name": "Cafea la espressor", "category": "cafea", "description": "Sunet de preparare a cafelei la espressor", "tags": ["cafea", "espressor", "bucătărie"]},
        {"name": "Apă care fierbe", "category": "cafea", "description": "Sunet de apă care fierbe", "tags": ["apă", "fierbere", "bucătărie"]},
        {"name": "Farfurii și tacâmuri", "category": "cafea", "description": "Sunet de vase și tacâmuri", "tags": ["farfurii", "tacâmuri", "bucătărie"]},
        {"name": "Frigider", "category": "cafea", "description": "Sunet de frigider care funcționează", "tags": ["frigider", "bucătărie", "electrocasnice"]},
        {"name": "Ușă care se deschide", "category": "cameră", "description": "Sunet de ușă care se deschide", "tags": ["ușă", "cameră", "casă"]},
        {"name": "Parchet - pași", "category": "cameră", "description": "Sunet de pași pe parchet", "tags": ["pași", "parchet", "cameră"]},
        {"name": "Lift", "category": "cameră", "description": "Sunet de lift care urcă și coboară", "tags": ["lift", "clădire", "cameră"]},
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
                is_synthetic=True,
            )
            print(f"Adăugat: {amb['name']}")
        except Exception as e:
            print(f"Avertisment {amb['name']}: {e}")
