"""
device.py — Identificare automată a dispozitivului pentru auto-login.

Generează un fingerprint unic și persistent pe baza caracteristicilor
browserului (user agent, dimensiune ecran, timezone, limbă etc.).
Asociază dispozitivele cu conturile de utilizator în Turso DB.

Flow:
1. La prima accesare, browserul primește un device_id generat din fingerprint
2. Device_id-ul e stocat în localStorage (persistent)
3. La accesările următoare, device_id-ul e citit și căutat în DB
4. Dacă există o asociere → auto-login la contul respectiv
5. Dacă nu există → login manual → asociere automată
"""

import hashlib
import json
import time
import uuid

import db


# ---------------------------------------------------------------------------
# Fingerprint generation (client-side JS sends these values)
# ---------------------------------------------------------------------------

def generate_device_id(user_agent="", screen_w=0, screen_h=0,
                       timezone="", language="", platform=""):
    """Generează un device_id deterministic dintr-un fingerprint de browser.

    Aceasta e funcția server-side. Client-ul (JavaScript) colectează
    characteristicile și le trimite aici.
    """
    raw = "|".join([
        (user_agent or "").strip(),
        str(screen_w),
        str(screen_h),
        (timezone or "").strip(),
        (language or "").strip(),
        (platform or "").strip(),
    ])
    # SHA-256 truncated la 32 chars — suficient de unic, nu expune datele
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def parse_device_info(user_agent=""):
    """Extrage un nume prietenos din user agent (ex: 'Samsung S10 Lite')."""
    ua = (user_agent or "").lower()
    # Android devices
    android_models = [
        ("samsung", "SM-G770", "Samsung Galaxy S10 Lite"),
        ("samsung", "SM-G781", "Samsung Galaxy S20 FE"),
        ("samsung", "SM-A515", "Samsung Galaxy A51"),
        ("samsung", "SM-A525", "Samsung Galaxy A52"),
        ("samsung", "SM-A536", "Samsung Galaxy A53"),
        ("samsung", "SM-A546", "Samsung Galaxy A54"),
        ("samsung", "SM-A556", "Samsung Galaxy A55"),
        ("samsung", "SM-G991", "Samsung Galaxy S21"),
        ("samsung", "SM-G996", "Samsung Galaxy S21+"),
        ("samsung", "SM-G998", "Samsung Galaxy S21 Ultra"),
        ("samsung", "SM-S908", "Samsung Galaxy S22 Ultra"),
        ("samsung", "SM-S911", "Samsung Galaxy S24"),
        ("samsung", "SM-S916", "Samsung Galaxy S24+"),
        ("samsung", "SM-S918", "Samsung Galaxy S24 Ultra"),
        ("pixel", "Pixel 7", "Google Pixel 7"),
        ("pixel", "Pixel 8", "Google Pixel 8"),
        ("xiaomi", "M2101K6G", "Xiaomi Redmi Note 10 Pro"),
        ("xiaomi", "M2012K11AG", "Xiaomi Mi 11"),
        ("oneplus", "LE2125", "OnePlus 9 Pro"),
        ("huawei", "NOH-NX9", "Huawei P40 Pro"),
    ]
    for brand_hint, model_hint, name in android_models:
        if model_hint.lower() in ua:
            return name
    # iOS devices
    if "iphone" in ua:
        if "iPhone15" in ua:
            return "iPhone 15"
        if "iPhone14" in ua:
            if "Pro Max" in user_agent:
                return "iPhone 14 Pro Max"
            return "iPhone 14"
        if "iPhone13" in ua:
            return "iPhone 13"
        if "iPhone12" in ua:
            return "iPhone 12"
        if "iPhone11" in ua:
            return "iPhone 11"
        if "iPhone10" in ua:
            return "iPhone X"
        if "iPhone8" in ua:
            return "iPhone 8"
        return "iPhone"
    if "ipad" in ua:
        return "iPad"
    # Generic fallback
    if "android" in ua:
        return "Dispozitiv Android"
    if "windows" in ua:
        return "PC Windows"
    if "mac" in ua:
        return "Mac"
    if "linux" in ua:
        return "Linux"
    return "Dispozitiv necunoscut"


# ---------------------------------------------------------------------------
# DB operations — device_sessions collection
# ---------------------------------------------------------------------------def _exec(sql, params=None):
    """Execută SQL pe backend-ul curent (Turso/PG/mongomock)."""
    if hasattr(db, '_exec'):
        db._exec(sql, params)
    elif hasattr(db, '_turso_exec'):
        db._turso_exec(sql, params)


def _fetch(sql, params=None):
    """Fetch rows din backend-ul curent. Returnează listă de dict-uri."""
    if hasattr(db, '_fetch'):
        return db._fetch(sql, params)
    elif hasattr(db, '_turso_exec'):
        return db._turso_exec(sql, params)
    return []


def _ensure_collection():
    """Asigură colecția device_sessions în Turso."""
    try:
        _fetch("SELECT 1 FROM device_sessions LIMIT 1")
    except Exception:
        # Tabelul nu există — îl cream
        try:
            _exec(
                "CREATE TABLE IF NOT EXISTS device_sessions ("
                "  id TEXT PRIMARY KEY,"
                "  device_id TEXT NOT NULL,"
                "  username TEXT NOT NULL,"
                "  device_name TEXT DEFAULT '',"
                "  user_agent TEXT DEFAULT '',"
                "  created_at TEXT DEFAULT (datetime('now')),"  
                "  last_seen TEXT DEFAULT (datetime('now'))"
                ")"
            )
            _exec(
                "CREATE INDEX IF NOT EXISTS idx_devdev "
                "ON device_sessions(device_id)"
            )
            _exec(
                "CREATE INDEX IF NOT EXISTS idx_devusr "
                "ON device_sessions(username)"
            )
        except Exception:
            pass


def associate_device(device_id, username, device_name="", user_agent=""):
    """Asociază un dispozitiv cu un cont de utilizator."""
    _ensure_collection()
    username = (username or "").strip().lower()
    device_id = (device_id or "").strip()
    if not device_id or not username:
        return None

    # Verifică dacă există deja o asociere
    existing = lookup_device(device_id)
    if existing:
        # Actualizează last_seen
        try:
            _exec(
                "UPDATE device_sessions SET last_seen = datetime('now'), "
                "device_name = ? WHERE device_id = ?",
                [device_name or "", device_id],
            )
        except Exception:
            pass
        return existing

    # Creează asociere nouă
    sid = str(uuid.uuid4())
    try:
        _exec(
            "INSERT INTO device_sessions "
            "(id, device_id, username, device_name, user_agent) "
            "VALUES (?, ?, ?, ?, ?)",
            [sid, device_id, username, device_name or "", user_agent or ""],
        )
        return {"username": username, "device_name": device_name}
    except Exception:
        return None


def lookup_device(device_id):
    """Caută contul asociat unui dispozitiv. Returnează username sau None."""
    _ensure_collection()
    device_id = (device_id or "").strip()
    if not device_id:
        return None
    try:
        rows = _fetch(
            "SELECT username, device_name FROM device_sessions "
            "WHERE device_id = ? LIMIT 1",
            [device_id],
        )
        if rows:
            # Update last_seen
            try:
                _exec(
                    "UPDATE device_sessions SET last_seen = datetime('now') "
                    "WHERE device_id = ?",
                    [device_id],
                )
            except Exception:
                pass
            # rows is list of dicts from _fetch
            r = rows[0] if isinstance(rows[0], dict) else {"username": rows[0][0], "device_name": rows[0][1]}
            return {"username": r.get("username", ""), "device_name": r.get("device_name", "")}
    except Exception:
        pass
    return None


def list_devices(username):
    """Listează toate dispozitivele asociate unui cont."""
    _ensure_collection()
    username = (username or "").strip().lower()
    if not username:
        return []
    try:
        rows = _fetch(
            "SELECT device_id, device_name, created_at, last_seen "
            "FROM device_sessions WHERE username = ? ORDER BY last_seen DESC",
            [username],
        )
        return [
            {"device_id": r.get("device_id", ""),
             "device_name": r.get("device_name", "") or "Dispozitiv",
             "created_at": r.get("created_at", ""),
             "last_seen": r.get("last_seen", "")}
            for r in rows
        ]
    except Exception:
        return []


def remove_device(device_id):
    """Elimină asocierea unui dispozitiv."""
    _ensure_collection()
    device_id = (device_id or "").strip()
    if not device_id:
        return False
    try:
        _exec(
            "DELETE FROM device_sessions WHERE device_id = ?",
            [device_id],
        )
        return True
    except Exception:
        return False


def remove_all_devices(username):
    """Elimină toate dispozitivele asociate unui cont (logout everywhere)."""
    _ensure_collection()
    username = (username or "").strip().lower()
    if not username:
        return False
    try:
        _exec(
            "DELETE FROM device_sessions WHERE username = ?",
            [username],
        )
        return True
    except Exception:
        return False
