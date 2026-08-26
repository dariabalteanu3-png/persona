"""Freesound API client — sursa principală pentru sunete contextuale.

Folosește Freesound.org API v2 pentru a căuta sunete reale pe baza
contextului conversației. Toate căutările se fac în limba engleză
pentru rezultate optime. Biblioteca locală DSP rămâne ca fallback
când API-ul eșuează sau limita e atinsă.

API Key: setat prin variabila de mediu FREESOUND_API_KEY
"""

import os
import random
import time
import re
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


# ── Calitatea sunetului — filtrare rezultate slabe ───────────────────────────

# Taguri care indică sunete de calitate slabă / nerelevante
_LOW_QUALITY_TAGS = {
    "synthesized", "synthetic", "generated", "computer generated",
    "test", "recording test", "sample", "tone", "sine", "beep",
    "white noise", "pink noise", "brown noise", "static",
    "distortion", "glitch", "broken", "error",
    "silence", "dead air", "muted",
}

# Cuvinte în numele sunetului care indică calitate scăzută
_LOW_QUALITY_NAME_PATTERNS = [
    r"test\s", r"sample\s", r"tone\b", r"beep\b",
    r"noise\b", r"static\b", r"hum\b", r"buzz\b",
    r"synthetic", r"generated", r"white\s", r"pink\s",
]


def _is_low_quality(sound: dict) -> bool:
    """Verifică dacă un sunet Freesound este de calitate scăzută.

    Returns True dacă sunetul pare a fi generat sintetic, test,
    zgomot de fond nerelevant, etc.
    """
    name = (sound.get("name") or "").lower()
    tags = [t.lower() for t in (sound.get("tags") or [])]
    duration = sound.get("duration", 0)

    # Prea scurt (< 1.5s) — probabil un click/bip
    if duration < 1.5:
        return True

    # Verifică taguri de calitate slabă
    if any(t in _LOW_QUALITY_TAGS for t in tags):
        return True

    # Verifică pattern-uri în nume
    for pattern in _LOW_QUALITY_NAME_PATTERNS:
        if re.search(pattern, name):
            return True

    return False


# ── Mapare context românesc → query englezesc (Freesound) ────────────────────

# Frază-level: propoziții / expresii întregi în română care mapează
# direct la un query Freesound englez.
_CONTEXT_PHRASES: list[tuple[str, list[str]]] = [
    # Balcon
    ("sunt pe balcon", ["balcony ambience outdoor", "birds on balcony", "city balcony ambience"]),
    ("stau pe balcon", ["balcony ambience outdoor", "wind on balcony"]),
    ("ies pe balcon", ["balcony ambience outdoor", "outdoor birds"]),
    ("ma duc pe balcon", ["balcony ambience outdoor"]),
    ("privesc de pe balcon", ["balcony ambience outdoor", "city sounds distant"]),

    # Ponton / lac
    ("sunt pe ponton", ["lake water lapping", "dock water sounds", "lake birds"]),
    ("ma uit la lac", ["lake water ambience", "water lapping shore"]),
    ("langa lac", ["lake water ambience", "birds near water"]),
    ("pe malul apei", ["river shore water", "water lapping"]),
    ("sunt la malul", ["river shore water", "waves shore"]),

    # Duș
    ("fac dus", ["shower water running", "bathroom shower"]),
    ("fac duș", ["shower water running", "bathroom shower"]),
    ("sunt sub dus", ["shower water running"]),
    ("am intrat la dus", ["shower water running", "bathroom echo"]),
    ("ma spal", ["water running tap", "washing hands"]),

    # Uscător de păr
    ("folosesc uscatorul", ["hair dryer blowing", "hair dryer"]),
    ("folosesc uscătorul", ["hair dryer blowing", "hair dryer"]),
    ("ma usc pe par", ["hair dryer blowing"]),
    ("usc parul", ["hair dryer blowing"]),

    # Bucătărie
    ("taie ceapa", ["chopping vegetables", "knife cutting board"]),
    ("tai ceapa", ["chopping vegetables", "knife cutting board"]),
    ("gatesc", ["cooking sizzling pan", "kitchen cooking"]),
    ("fac mancare", ["cooking sizzling pan", "kitchen sounds"]),
    ("prajesc", ["frying food sizzling"]),
    ("pun ceapa la prajit", ["frying food sizzling oil"]),
    ("spal vasele", ["washing dishes water", "kitchen sink water"]),
    ("bag la cuptor", ["oven door closing", "kitchen timer"]),

    # Pat / dormit
    ("sunt in pat", ["bedroom quiet ambience", "quiet room"]),
    ("m-am bagat in pat", ["bedroom quiet night", "sheets rustling"]),
    ("ma pun in pat", ["bedroom quiet ambience", "pillow soft"]),
    ("incerc sa adorm", ["bedroom quiet night", "breathing calm"]),
    ("nu pot sa dorm", ["bedroom quiet night", "clock ticking"]),

    # Tren / transport
    ("sunt in tren", ["train interior ride", "train on tracks"]),
    ("calatoresc cu trenul", ["train interior ride", "railway sounds"]),
    ("sunt in metrou", ["subway metro interior", "underground train"]),
    ("iau metroul", ["subway metro ambience"]),
    ("sunt in autobuz", ["bus interior ride", "bus engine"]),
    ("sunt in masina", ["car interior driving", "road noise"]),
    ("conduc masina", ["car driving road", "engine hum"]),

    # Natură / exterior
    ("sunt in padure", ["forest ambience birds", "forest nature sounds"]),
    ("merg prin padure", ["walking forest leaves", "forest birds"]),
    ("am iesit la plimbare", ["park birds ambience", "outdoor walking"]),
    ("sunt pe plaja", ["ocean waves beach", "beach shore"]),
    ("sunt la mare", ["ocean waves sea", "seaside ambience"]),
    ("sunt la munte", ["mountain wind nature", "mountain ambience"]),
    ("urc pe munte", ["hiking mountain wind", "gravel walking"]),

    # Oraș
    ("sunt in oras", ["city street ambience", "city traffic"]),
    ("merg pe strada", ["city street walking", "footsteps sidewalk"]),
    ("stau la coada", ["crowd queue waiting", "people talking"]),

    # Ploaie / vreme
    ("ploua afara", ["rain outside window", "rain ambience"]),
    ("a inceput ploaia", ["rain starting", "rain drops"]),
    ("tunet si fulger", ["thunder lightning storm", "thunder rumble"]),
    ("ninge afara", ["snow falling quiet", "winter wind"]),
    ("e ceata afara", ["fog ambience mist", "fog horn distant"]),
    ("e vant afara", ["wind blowing outdoor", "wind gusts"]),

    # Animale
    ("aud cainele", ["dog barking", "dog woofing"]),
    ("pisica toarce", ["cat purring", "cat meowing"]),
    ("aud pasari", ["birdsong singing", "birds chirping"]),
    ("pasari dimineata", ["morning birds dawn chorus", "dawn birds singing"]),
]

# Word-level: cuvinte cheie românești → query-uri Freesound engleze
_CONTEXT_KEYWORDS: dict[str, list[str]] = {
    # ── Natură ──
    "ploaie": ["rain ambience", "rain drops on leaves"],
    "furtuna": ["thunderstorm rain", "thunder rumble"],
    "tunet": ["thunder rumble", "distant thunder"],
    "zapada": ["snow crunching walking", "winter cold"],
    "vânt": ["wind blowing outdoor", "wind gusts trees"],
    "padure": ["forest ambience nature", "forest birds singing"],
    "plaja": ["ocean waves beach", "seaside ambience"],
    "mare": ["ocean waves sea", "waves shore"],
    "lac": ["lake water ambience", "water lapping"],
    "apa": ["water stream creek", "flowing water"],
    "munte": ["mountain wind nature", "mountain ambience"],
    "rau": ["river flowing water", "river stream"],
    "cascada": ["waterfall rushing water"],

    # ── Oraș / transport ──
    "oras": ["city ambience street", "city traffic background"],
    "strada": ["city street ambience", "traffic cars passing"],
    "trafic": ["traffic cars passing", "street vehicles"],
    "metrou": ["subway metro interior", "underground train ride"],
    "tren": ["train interior ride", "train on tracks"],
    "autobuz": ["bus interior ride", "bus engine hum"],
    "avion": ["airplane cabin interior", "airplane engine drone"],
    "masina": ["car interior driving", "road noise car"],
    "bicicleta": ["bicycle riding", "bike chain pedaling"],
    "barca": ["boat rowing water", "paddle water splashing"],

    # ── Acasă / interior ──
    "bucatarie": ["kitchen cooking sounds", "pots pans clanking"],
    "gatit": ["cooking sizzling pan", "frying food oil"],
    "prajit": ["frying food sizzling", "oil crackling pan"],
    "taiat": ["chopping vegetables cutting board", "knife chopping"],
    "baie": ["bathroom shower water", "bathroom echo tap"],
    "dus": ["shower water running", "bathroom shower"],
    "duș": ["shower water running", "bathroom shower"],
    "pat": ["bedroom quiet ambience", "sheets rustling"],
    "canapea": ["living room quiet", "couch sitting"],
    "birou": ["office ambience keyboard", "typing keyboard"],
    "tastatura": ["keyboard typing", "mechanical keyboard"],
    "uscator": ["hair dryer blowing", "hair dryer noise"],
    "uscător": ["hair dryer blowing"],
    "frigider": ["refrigerator hum", "kitchen fridge"],
    "ventilator": ["fan spinning ambience", "electric fan"],
    "masina spalat": ["washing machine cycle", "washing machine drum"],
    "uscator rufe": ["tumble dryer running"],

    # ── Animale ──
    "caine": ["dog barking", "dog woofing happy"],
    "pisica": ["cat purring content", "cat meowing"],
    "pasari": ["birdsong singing", "birds chirping nature"],
    "păsări": ["birdsong singing", "birds morning chorus"],
    "greieri": ["crickets chirping night", "night insects"],
    "broasca": ["frog croaking pond", "frogs calling night"],
    "cal": ["horse galloping", "horse clip clop"],
    "vaca": ["cow mooing farm", "cattle farm ambience"],
    "gaina": ["chicken clucking", "rooster crowing"],
    "oaie": ["sheep bleating", "sheep baa"],

    # ── Cafenea / social ──
    "cafea": ["coffee shop ambience", "cafe chatter"],
    "bar": ["bar pub ambience", "pub crowd chatter"],
    "restaurant": ["restaurant ambience dining", "restaurant chatter"],
    "magazin": ["store ambience shop", "shopping mall background"],
    "biblioteca": ["library quiet ambience", "library whispers"],
    "cimitir": ["cemetery quiet wind", "quiet outdoor"],

    # ── Activități ──
    "scris": ["writing pen paper", "pencil scratching paper"],
    "citit": ["pages turning book", "book page turning"],
    "cantec": ["singing vocal music", "human singing"],
    "pian": ["piano playing music", "piano melody"],
    "chitara": ["guitar strumming", "acoustic guitar playing"],
    "perie": ["brushing teeth", "toothbrush scrubbing"],
    "machiaj": ["cosmetics makeup brush", "makeup applying"],
    "parfum": ["perfume spray bottle", "spray mist"],
    "curatenie": ["vacuum cleaner", "cleaning sweeping"],

    # ── Timp / vreme ──
    "noapte": ["night ambience quiet", "night crickets"],
    "dimineata": ["morning birds dawn", "morning rooster birds"],
    "dimineață": ["morning birds dawn chorus"],
    "seara": ["evening ambience sunset", "evening birds"],
    "vara": ["summer ambience insects", "summer cicadas"],
    "iarna": ["winter cold wind", "snow crunching"],
    "primavara": ["spring birds nature", "spring morning birds"],
    "toamna": ["autumn wind leaves", "leaves rustling wind"],
    "ploios": ["rain ambience continuous", "rain on roof"],
    "innorat": ["cloudy wind ambience", "wind overcast"],
    "insorit": ["sunny day birds", "birds cheerful singing"],
    "ceata": ["fog ambience misty", "fog horn distant"],
    "ceață": ["fog ambience misty"],

    # ── Activități specifice ──
    "alerg": ["running footsteps", "jogging footsteps fast"],
    "inot": ["swimming pool splashing", "water splashing swimming"],
    "dans": ["dancing music rhythm", "dance music"],
    "joaca": ["playing children playground", "kids laughing playing"],
    "meditez": ["meditation ambient calm", "singing bowl peaceful"],
    "yoga": ["meditation peaceful ambient", "gentle breathing"],
    "masaj": ["massage spa ambient", "relaxing spa music"],
    "tatuaj": ["tattoo machine buzzing"],
    "frizer": ["hair clipper buzzing", "barbershop scissors"],
}


def context_to_queries(text: str) -> list[str]:
    """Extrage query-uri de căutare din textul conversației (în engleză).

    Prioritate:
    1. Detectare frază (propoziție întreagă) — rezultate mai precise
    2. Detectare cuvânt cheie — acoperire mai largă
    3. Niciun rezultat → returnează listă goală

    Toate query-urile returnate sunt în limba engleză pentru Freesound.
    """
    text_lower = text.lower()
    all_queries: list[str] = []

    # 1. Detecție la nivel de frază (prioritate maximă)
    for phrase, queries in _CONTEXT_PHRASES:
        if phrase in text_lower:
            all_queries.extend(queries)

    if all_queries:
        # Am găsit fraze — returnăm query-urile de la frază
        return all_queries[:5]

    # 2. Detecție la nivel de cuvânt cheie
    for keyword, queries in _CONTEXT_KEYWORDS.items():
        # Folosim word boundary pentru a evita potriviri parțiale
        # (ex: "pad" nu trebuie să potrivească "padure")
        if re.search(r'(?:^|\s)' + re.escape(keyword) + r'(?:\s|$|,|\.|!|\?)', text_lower):
            all_queries.extend(queries)

    return all_queries[:5]


def context_to_query(text: str) -> str | None:
    """Extrage un singur query de căutare din textul conversației.

    Funcția legacy — preferă context_to_queries() pentru multi-search.
    """
    queries = context_to_queries(text)
    if not queries:
        return None
    return random.choice(queries)


def search_sounds(query: str, tags: list[str] | None = None,
                  duration_max: int = 120, page_size: int = 15) -> list[dict]:
    """Caută sunete pe Freesound.

    Args:
        query: Text de căutare în engleză (ex: "rain", "cooking", "forest birds")
        tags: Tags opționale pentru filtrare
        duration_max: Durata maximă în secunde (default 120s)
        page_size: Câte rezultate să returneze (max 150)

    Returns:
        Listă de dict-uri cu info despre sunete (fără sunete de calitate slabă):
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
        # Sortează după relevanță (scoring), nu după data încărcării
        "sort": "rating_desc",
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


def search_for_context(text: str, max_duration: int = 60) -> dict | None:
    """Caută un sunet REAL și potrivit pentru contextul conversației.

    Acceptă atât text românesc (detectare automată → query englez) cât și
    query-uri englezești directe (când e apelat din voice.py cu preset names).

    Strategie:
    1. Generează query-uri din contextul românesc (traduse în engleză)
    2. Dacă nu găsește potriviri românești, folosește textul direct ca query
    3. Pentru fiecare query, caută pe Freesound (max 15 rezultate)
    4. Filtrează sunetele de calitate slabă
    5. Alege cel mai potrivit sunet (durată medie, relevant)

    Args:
        text: Textul mesajului utilizatorului SAU un query englez direct
        max_duration: Durata maximă a sunetului în secunde

    Returns:
        Dict cu info sunet + preview_url + info autor/licență,
        sau None dacă nu găsește nimic.
    """
    queries = context_to_queries(text)
    if not queries:
        # Dacă textul conține doar cuvinte englezești (preset name),
        # folosește-l direct ca query — voice.py trece query-uri engleze
        if text and re.search(r'[a-zA-Z]{3,}', text):
            queries = [text.strip()]
        else:
            return None

    # Încearcă fiecare query până găsește un sunet bun
    for query in queries[:3]:  # Max 3 query-uri diferite
        results = search_sounds(query, duration_max=max_duration, page_size=15)
        if not results:
            continue

        # Filtrează sunetele de calitate slabă
        good_results = [r for r in results if not _is_low_quality(r)]

        if not good_results:
            # Dacă toate sunt slabe, încearcă următorul query
            continue

        # Alege cel mai potrivit sunet:
        # - preferă durata medie (5-30s) — suficient pentru ambient
        # - exclude sunete prea scurte (<3s) sau prea lungi (>60s)
        ideal_min = 3
        ideal_max = min(30, max_duration)

        # Sortează: preferă durata în intervalul ideal
        def _score(s):
            dur = s.get("duration", 0)
            if ideal_min <= dur <= ideal_max:
                return abs(dur - 10)  # prefer ~10s
            elif dur < ideal_min:
                return 1000 + dur  # penalizează prea scurt
            else:
                return 500 + dur  # penalizează prea lung

        good_results.sort(key=_score)
        selected = good_results[0]

        # Verifică dacă fișierul poate fi descărcat
        path = download_preview(selected["preview_url"], selected["id"])
        if path:
            return selected

    return None


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
