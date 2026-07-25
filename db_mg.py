"""
db_mg.py — backend în memorie (mongomock) pentru când DATABASE_URL nu e setat.
Date reset la repornire — folosit pe HF Spaces fără bază de date externă.
"""

import os
import uuid
from datetime import datetime, timezone, timedelta

import mongomock

_client = mongomock.MongoClient()
_db = _client["persona"]

characters   = _db.characters
messages     = _db.messages
conversations = _db.conversations
users        = _db.users
sessions     = _db.sessions
email_codes  = _db.email_codes

try:
    users.create_index("email", unique=True)
    sessions.create_index("token", unique=True)
except Exception:
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def get_config(name, default=None):
    return os.environ.get(name, default)


# --------------- users ---------------

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
    users.insert_one(doc)
    doc.pop("_id", None)
    return doc


def get_user_by_email(email):
    return users.find_one({"email": email}, {"_id": 0})


def get_user_by_id(uid):
    return users.find_one({"id": uid}, {"_id": 0})


def update_user(uid, data):
    users.update_one({"id": uid}, {"$set": data})
    return get_user_by_id(uid)


def set_user_verified(email):
    users.update_one({"email": email}, {"$set": {"verified": True}})


def set_user_password(email, password_hash):
    users.update_one({"email": email}, {"$set": {"password_hash": password_hash}})


def toggle_favorite(user_id, char_id):
    u = get_user_by_id(user_id)
    favs = list((u or {}).get("favorites") or [])
    if char_id in favs:
        favs.remove(char_id)
        state = False
    else:
        favs.append(char_id)
        state = True
    users.update_one({"id": user_id}, {"$set": {"favorites": favs}})
    return state


def get_favorites(user_id):
    u = get_user_by_id(user_id)
    return list((u or {}).get("favorites") or []) if u else []


def favorite_counts():
    counts = {}
    for u in users.find({}, {"favorites": 1, "_id": 0}):
        for cid in (u.get("favorites") or []):
            counts[cid] = counts.get(cid, 0) + 1
    return counts


def delete_user(user_id):
    u = get_user_by_id(user_id)
    if not u:
        return
    for ch in list_characters(owner_id=user_id):
        delete_character(ch["id"])
    sessions.delete_many({"user_id": user_id})
    email_codes.delete_many({"email": u.get("email")})
    users.delete_one({"id": user_id})


def increment_stat(char_id, field, n=1):
    characters.update_one({"id": char_id}, {"$inc": {field: n}})


def character_message_count(char_id):
    conv_ids = [c["id"] for c in list_conversations(char_id)]
    if not conv_ids:
        return 0
    return messages.count_documents({"conversation_id": {"$in": conv_ids}})


# --------------- email codes / sessions ---------------

def create_email_code(email, code, purpose, ttl_minutes=15):
    email_codes.delete_many({"email": email, "purpose": purpose})
    email_codes.insert_one({
        "email": email, "code": code, "purpose": purpose,
        "created_at": _now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)).isoformat(),
    })


def check_email_code(email, code, purpose):
    doc = email_codes.find_one({"email": email, "purpose": purpose, "code": (code or "").strip()})
    if not doc:
        return False
    try:
        if datetime.fromisoformat(doc["expires_at"]) < datetime.now(timezone.utc):
            email_codes.delete_one({"_id": doc["_id"]})
            return False
    except Exception:
        pass
    email_codes.delete_one({"_id": doc["_id"]})
    return True


def create_session(token, user_id, expires_days=30):
    sessions.insert_one({
        "token": token, "user_id": user_id,
        "created_at": _now(),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat(),
    })


def get_session(token):
    s = sessions.find_one({"token": token}, {"_id": 0})
    if not s:
        return None
    try:
        if datetime.fromisoformat(s["expires_at"]) < datetime.now(timezone.utc):
            sessions.delete_one({"token": token})
            return None
    except Exception:
        pass
    return s


def delete_session(token):
    sessions.delete_one({"token": token})


# --------------- characters ---------------

def create_character(data):
    doc = {"id": str(uuid.uuid4()), "created_at": _now(), **data}
    characters.insert_one(doc)
    doc.pop("_id", None)
    return doc


def list_characters(owner_id=None):
    q = {} if owner_id is None else {"owner_id": owner_id}
    return list(characters.find(q, {"_id": 0}).sort("created_at", -1))


def reassign_owner(old_owner_id, new_owner_id):
    characters.update_many({"owner_id": old_owner_id}, {"$set": {"owner_id": new_owner_id}})


def list_public_characters():
    return list(characters.find({"visibility": "public"}, {"_id": 0}).sort("created_at", -1))


def get_character(cid):
    return characters.find_one({"id": cid}, {"_id": 0})


def update_character(cid, data):
    characters.update_one({"id": cid}, {"$set": data})
    return get_character(cid)


def delete_user_voices(user_id):
    fields = ["voice_id", "voice_name", "voice_sample_b64", "voice_sample_name",
              "voice_ref_text", "voice_stability", "voice_similarity", "voice_style", "voice_tone"]
    result = characters.update_many(
        {"owner_id": user_id},
        {"$unset": {f: "" for f in fields}},
    )
    return result.modified_count


def delete_character(cid):
    conv_ids = [c["id"] for c in list_conversations(cid)]
    characters.delete_one({"id": cid})
    conversations.delete_many({"character_id": cid})
    messages.delete_many({"conversation_id": {"$in": conv_ids}})


# --------------- conversations ---------------

def create_conversation(character_id, title="Conversație nouă"):
    doc = {
        "id": str(uuid.uuid4()),
        "character_id": character_id,
        "title": title,
        "created_at": _now(),
        "updated_at": _now(),
    }
    conversations.insert_one(doc)
    doc.pop("_id", None)
    return doc


def list_conversations(character_id):
    return list(conversations.find({"character_id": character_id}, {"_id": 0}).sort("created_at", 1))


def get_conversation(conv_id):
    return conversations.find_one({"id": conv_id}, {"_id": 0})


def rename_conversation(conv_id, title):
    conversations.update_one({"id": conv_id}, {"$set": {"title": title, "updated_at": _now()}})


def touch_conversation(conv_id):
    conversations.update_one({"id": conv_id}, {"$set": {"updated_at": _now()}})


def delete_conversation(conv_id):
    conversations.delete_one({"id": conv_id})
    messages.delete_many({"conversation_id": conv_id})


# --------------- messages ---------------

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
    messages.insert_one(doc)
    touch_conversation(conversation_id)
    doc.pop("_id", None)
    return doc


def get_messages(conversation_id):
    return list(messages.find({"conversation_id": conversation_id}, {"_id": 0}).sort("created_at", 1))


def list_media(owner_id):
    out = []
    for ch in list_characters(owner_id=owner_id):
        conv_ids = [c["id"] for c in list_conversations(ch["id"])]
        if not conv_ids:
            continue
        cur = messages.find(
            {"conversation_id": {"$in": conv_ids}, "media_kind": {"$in": ["photo", "song", "video"]}},
            {"_id": 0},
        )
        for m in cur:
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
        for m in messages.find({"conversation_id": {"$in": conv_ids}, "role": "assistant"}, {"_id": 0}):
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


def list_song_names(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return []
    cur = messages.find(
        {"conversation_id": {"$in": conv_ids}, "media_kind": "song"},
        {"_id": 0, "song_name": 1, "created_at": 1},
    ).sort("created_at", 1)
    seen, out = set(), []
    for m in cur:
        n = m.get("song_name")
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return out


def list_songs(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return []
    return list(messages.find(
        {"conversation_id": {"$in": conv_ids}, "media_kind": "song", "role": "user"},
        {"_id": 0, "id": 1, "song_name": 1, "song_b64": 1, "created_at": 1},
    ).sort("created_at", 1))


def delete_song(message_id):
    messages.delete_one({"id": message_id})


def rename_song(message_id, new_name):
    messages.update_one({"id": message_id}, {"$set": {"song_name": new_name}})


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
    return messages.count_documents(
        {"conversation_id": {"$in": conv_ids}, "media_kind": {"$in": ["photo", "song", "video"]}}
    ) > 0


def random_media(character_id):
    conv_ids = [c["id"] for c in list_conversations(character_id)]
    if not conv_ids:
        return None
    items = list(messages.find(
        {"conversation_id": {"$in": conv_ids}, "media_kind": {"$in": ["photo", "song"]}},
        {"_id": 0},
    ))
    if not items:
        return None
    import random
    return random.choice(items)


def clear_messages(conversation_id):
    messages.delete_many({"conversation_id": conversation_id})


def set_reaction(message_id, emoji):
    messages.update_one({"id": message_id}, {"$set": {"reaction": emoji}})
