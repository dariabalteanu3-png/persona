"""Freesound API client — sursa principală pentru sunete contextuale.

Folosește Freesound.org API v2 pentru a căuta sunete reale pe baza
contextului conversației. Biblioteca locală DSP rămâne ca fallback
când API-ul eșuează sau limita e atinsă.

API Key: setat prin variabila de mediu FREESOUND_API_KEY
"""

import os
import random
import time
import hashlib
from pathlib import Path

import requests

_API_KEY = os.environ.get("FREESOUND_API_KEY", "")
_BASE_URL = "https://freesound.org/apiv2"
_CACHE_DIR = Path(__file__).parent / ".freesound_cache"

# Cache timestamps — evită cereri repetate într-un timp scurt
_search_cache: dict = {}  # query → (results, timestamp)
_CACHE_TTL = 300  # 5 minute

# Rate-limit tracking
_last_request_time = 0.0
_rate_limited_until = 0.0
_request_count = 0


def is_available() -> bool:
    """Returnează True dacă Freesound API este configurat și disponibil."""
    return bool(_API_KEY)


def _rate_limit():
    """Respectă limita de 60 requests/minute a Freesound."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < 1.0:
        time.sleep(1.0 - elapsed)
    _last_request_time = time.time()


def _make_cache_key(query: str, tag: str = "") -> str:
    """Generează un cache key stabil pentru o căutare."""
    raw = f"{query}|{tag}"
    return hashlib.md5(raw.encode()).hexdigest()


def search_sounds(query: str, tags: list[str] | None = None,
                  duration_max: int = 120, page_size: int = 5) -> list[dict]:
    """Caută sunete pe Freesound.

    Args:
        query: Text de căutare (ex: "rain", "cooking", "forest birds")
        tags: Tags opționale pentru filtrare
        duration_max: Durata maximă în secunde (default 120s)
        page_size: Câte rezultate să returneze (max 150)

    Returns:
        Listă de dict-uri cu info despre sunete:
        [{id, name, username, license, preview_url, page_url, duration, tags}]
    """
    if not _API_KEY:
        return []

    # Verifică cache-ul
    cache_key = _make_cache_key(query, str(tags))
    if cache_key in _search_cache:
        results, ts = _search_cache[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return results

    # Verifică rate-limit
    if time.time() < _rate_limited_until:
        return []

    _rate_limit()

    params = {
        "token": _API_KEY,
        "query": query,
        "page_size": min(page_size, 30),
        "fields": "id,name,username,license,duration,tags,url,previews",
        "filter": f"duration:[0 TO {duration_max}]",
    }

    if tags:
        params["query"] = f"{query} tag:{' '.join(tags[:3])}"

    try:
        resp = requests.get(
            f"{_BASE_URL}/search/text/",
            params=params,
            timeout=10,
        )
        if resp.status_code == 429:
            _rate_limited_until = time.time() + 60
            return []
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    results = []
    for s in data.get("results", []):
        previews = s.get("previews", {})
        preview_url = (
            previews.get("preview-hq-mp3")
            or previews.get("preview-hq-ogg")
            or previews.get("preview-lq-mp3")
            or ""
        )
        if not preview_url:
            continue
        results.append({
            "id": s["id"],
            "name": s.get("name", ""),
            "username": s.get("username", ""),
            "license": s.get("license", ""),
            "preview_url": preview_url,
            "page_url": s.get("url", f"https://freesound.org/people/{s.get('username', '')}/sounds/{s['id']}/"),
            "duration": s.get("duration", 0),
            "tags": s.get("tags", []),
        })

    # Salvează în cache
    _search_cache[cache_key] = (results, time.time())
    return results


def get_sound_info(sound_id: int) -> dict | None:
    """Obține detalii despre un sunet specific."""
    if not _API_KEY:
        return None

    _rate_limit()
    try:
        resp = requests.get(
            f"{_BASE_URL}/sounds/{sound_id}/",
            params={"token": _API_KEY, "fields": "id,name,username,license,duration,tags,url,previews,description"},
            timeout=10,
        )
        if resp.status_code == 429:
            return None
        resp.raise_for_status()
        s = resp.json()
    except Exception:
        return None

    previews = s.get("previews", {})
    preview_url = (
        previews.get("preview-hq-mp3")
        or previews.get("preview-hq-ogg")
        or previews.get("preview-lq-mp3")
        or ""
    )
    return {
        "id": s["id"],
        "name": s.get("name", ""),
        "username": s.get("username", ""),
        "license": s.get("license", ""),
        "preview_url": preview_url,
        "page_url": s.get("url", ""),
        "duration": s.get("duration", 0),
        "tags": s.get("tags", []),
        "description": s.get("description", ""),
    }


def download_preview(preview_url: str, sound_id: int) -> str | None:
    """Descarcă un preview audio și returnează calea locală.

    Cache-ul previne descărcarea repetată a aceluiași sunet.
    """
    if not preview_url:
        return None

    _rate_limit()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _CACHE_DIR / f"{sound_id}.mp3"

    if cache_path.exists() and cache_path.stat().st_size > 0:
        return str(cache_path)

    try:
        resp = requests.get(preview_url, timeout=15)
        resp.raise_for_status()
        cache_path.write_bytes(resp.content)
        return str(cache_path)
    except Exception:
        return None


# ── Mapare context → query de căutare ─────────────────────────────────────

# Mapare de cuvinte cheie din conversație la query-uri Freesound.
# Folosește română + engleză pentru acoperire maximă.
_CONTEXT_KEYWORDS: dict[str, list[str]] = {
    # Natură
    "ploaie": ["rain", "raindrops"],
    "rain": ["rain", "raindrops"],
    "furtuna": ["thunderstorm"],
    "storm": ["thunderstorm"],
    "zapada": ["snow", "winter wind"],
    "vapeur": ["wind", "forest wind"],
    "padure": ["forest ambience", "birds forest"],
    "forest": ["forest ambience", "birds forest"],
    "plaja": ["ocean waves", "beach"],
    "beach": ["ocean waves", "beach"],
    "mare": ["ocean waves", "sea"],
    "ocean": ["ocean waves"],
    "lac": ["lake water", "nature"],
    "lac": ["lake water", "nature"],
    "apa": ["water stream", "creek"],
    "apa": ["water stream", "creek"],
    "munte": ["mountain wind", "nature"],
    "mountain": ["mountain wind"],

    # Oraș / transport
    "oras": ["city ambience", "street"],
    "city": ["city ambience"],
    "trafic": ["traffic", "street"],
    "metro": ["subway train", "metro"],
    "metrou": ["subway train"],
    "tren": ["train", "railway"],
    "train": ["train ambience"],
    "avion": ["airplane cabin"],
    "airplane": ["airplane cabin"],
    "masina": ["car interior", "driving"],
    "driving": ["car driving", "road"],
    "autobuz": ["bus interior"],

    # Acasă / interior
    "bucatarie": ["kitchen", "cooking"],
    "kitchen": ["kitchen sounds", "cooking"],
    "cooking": ["cooking", "frying", "chopping"],
    "gatit": ["cooking", "frying"],
    "prajit": ["frying food"],
    "taiat": ["chopping vegetables"],
    "baie": ["bathroom", "shower", "water tap"],
    "bathroom": ["shower water", "bathroom"],
    "dush": ["shower water"],
    "duș": ["shower water"],
    "shower": ["shower water"],
    "pat": ["bedroom", "quiet room"],
    "canapea": ["living room", "quiet"],
    "birou": ["office ambience", "keyboard typing"],
    "keyboard": ["keyboard typing"],
    "tastatura": ["keyboard typing"],
    "uscator": ["hair dryer"],
    "uscat": ["hair dryer"],
    "masina spalat": ["washing machine"],
    "frigider": ["refrigerator hum"],
    "ventilator": ["fan ambience"],
    "telefon": ["phone vibration", "notification"],
    "caine": ["dog barking"],
    "pisica": ["cat purring"],
    "câine": ["dog barking"],
    "pisică": ["cat purrying"],
    "pasari": ["birdsong", "birds singing"],
    "păsări": ["birdsong"],
    "cimpanzeu": ["jungle ambience"],
    "gradina": ["garden ambience", "birds garden"],
    "curte": ["backyard ambience"],

    # Cafenea / social
    "cafea": ["coffee shop ambience"],
    "cafe": ["coffee shop ambience", "cafe"],
    "bar": ["bar ambience", "pub sounds"],
    "restaurant": ["restaurant ambience", "dining"],
    "magazin": ["store ambience", "shopping mall"],
    "biblioteca": ["library ambience", "quiet room"],
    "cimitir": ["cemetery", "quiet", "wind"],

    # Activități
    "scrise": ["writing pen paper"],
    "citit": ["pages turning", "book"],
    "citire": ["pages turning", "book"],
    "cantec": ["singing", "vocal music"],
    "cântec": ["singing"],
    "muzica": ["music playing"],
    "pian": ["piano playing"],
    "chitara": ["guitar strumming"],
    "perie": ["brushing teeth"],
    "machiaj": ["cosmetics", "makeup brush"],
    "cosmetice": ["cosmetics sounds"],
    "parfum": ["perfume spray"],
    "umed": ["wet surface", "sponge"],
    "curatenie": ["vacuum cleaner", "cleaning"],

    # Context ambient general
    "noapte": ["night ambience", "crickets night"],
    "night": ["night ambience"],
    "dimineata": ["morning birds", "birds dawn"],
    "dimineață": ["morning birds"],
    "seara": ["evening ambience", "sunset"],
    "seră": ["evening ambience"],
    "vara": ["summer ambience", "summer insects"],
    "iarna": ["winter ambience", "cold wind"],
    "primavara": ["spring birds", "spring"],
    "toamna": ["autumn wind", "leaves rustling"],
    "ploios": ["rain ambience", "rain sounds"],
    "înnorat": ["cloudy ambience", "wind"],
    "insorit": ["sunny day", "birds cheerful"],
    "ceata": ["fog ambience", "mist"],
    "ceață": ["fog ambience", "mist"],
}


def context_to_query(text: str) -> str | None:
    """Extrage un query de căutare din textul conversației.

    Analizează textul pentru cuvinte cheie și returnează un query
    potrivit pentru Freesound, sau None dacă nu se potrivește nimic.
    """
    text_lower = text.lower()
    matches = []

    for keyword, queries in _CONTEXT_KEYWORDS.items():
        if keyword in text_lower:
            matches.extend(queries)

    if not matches:
        return None

    # Alege un query aleatoriu din potriviri
    return random.choice(matches)


def search_for_context(text: str, max_duration: int = 60) -> dict | None:
    """Caută un sunet potrivit pentru contextul conversației.

    Args:
        text: Textul mesajului utilizatorului (sau a contextului)
        max_duration: Durata maximă a sunetului în secunde

    Returns:
        Dict cu info sunet + preview_url + info autor/licență,
        sau None dacă nu găsește nimic.
    """
    query = context_to_query(text)
    if not query:
        return None

    results = search_sounds(query, duration_max=max_duration, page_size=5)
    if not results:
        return None

    # Alege cel mai scurt sunet relevant (evită clipuri lungi)
    selected = min(results, key=lambda r: r.get("duration", 999))
    return selected


def get_audio_for_context(text: str) -> tuple[str | None, dict | None]:
    """Obține calea locală a unui fișier audio pentru context.

    Returns:
        (audio_file_path, sound_info) sau (None, None) dacă nimic.
    """
    if not is_available():
        return None, None

    sound = search_for_context(text)
    if not sound:
        return None, None

    path = download_preview(sound["preview_url"], sound["id"])
    return path, sound


def clear_cache():
    """Șterge cache-ul de descărcări."""
    import shutil
    if _CACHE_DIR.exists():
        shutil.rmtree(_CACHE_DIR, ignore_errors=True)
    _search_cache.clear()
