# deploy marker: 2026-06-18 (recuperare cont / reset parolă daria)
import base64
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

_log = logging.getLogger("persona")

import streamlit as st
import streamlit.components.v1 as components

# Bring Streamlit Cloud secrets into os.environ so the config modules
# (db, llm, provider, ...) can read them at import time. No-op locally.
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, str):
            os.environ.setdefault(_k, _v)
except Exception:  # noqa
    pass


import db
import auth
import mailer
import llm
import voice
import image_gen
import stt

_ASSETS_DIR = os.path.join(os.path.dirname(__file__), "assets")

st.set_page_config(
    page_title="Persona — personaje AI cu voce",
    page_icon="🎭",
    layout="wide",
    initial_sidebar_state="expanded",
)

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Sora:wght@500;600;700&family=Manrope:wght@400;500;600&display=swap');

html, body, [class*="css"] { font-family: 'Manrope', sans-serif; }
h1, h2, h3, h4, .app-logo { font-family: 'Sora', sans-serif !important; letter-spacing: -0.02em; }

#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; }
.block-container { padding-top: 2.2rem; padding-bottom: 7rem; max-width: 1050px; }

section[data-testid="stSidebar"] {
    background: #0A0A0D;
    border-right: 1px solid #1e1e26;
}

.app-logo {
    font-size: 1.55rem; font-weight: 700; color: #ECECEC;
    display: flex; align-items: center; gap: .55rem; margin-bottom: .1rem;
}
.app-logo .dot { color: #FF7A59; }
.app-tag { color: #7b7b86; font-size: .8rem; margin-bottom: 1.2rem; }

/* Character list items */
.char-active {
    background: linear-gradient(180deg,#1d1d24,#141419);
    border: 1px solid #FF7A59;
    border-radius: 14px; padding: .2rem .1rem;
}

/* Buttons */
.stButton > button {
    border-radius: 12px; font-family: 'Manrope', sans-serif; font-weight: 600;
    border: 1px solid #26262f; transition: transform .12s ease, border-color .2s ease, background .2s ease;
}
.stButton > button:hover { transform: translateY(-1px); border-color: #FF7A59; }

/* Chat bubbles */
[data-testid="stChatMessage"] {
    background: transparent; border-radius: 16px;
    animation: msgIn .4s cubic-bezier(.22,.61,.36,1) both;
}
@keyframes msgIn {
    from { opacity: 0; transform: translateY(10px) scale(.99); }
    to { opacity: 1; transform: none; }
}

.hero {
    border: 1px solid #1e1e26; border-radius: 22px;
    background: radial-gradient(120% 120% at 0% 0%, #17171f 0%, #0e0e11 60%);
    padding: 2.6rem 2.4rem; margin-bottom: 1.5rem;
}
.hero h1 { font-size: 2.6rem; margin: 0 0 .4rem 0; }
.hero p { color: #9a9aa6; font-size: 1.02rem; max-width: 560px; }
.accent { color: #FF7A59; }

.char-header {
    display:flex; align-items:center; gap: 1rem;
    border:1px solid #1e1e26; border-radius: 18px;
    padding: 1.1rem 1.3rem; margin-bottom: 1.2rem;
    background: linear-gradient(180deg,#15151b,#0e0e11);
}
.char-header .avatar { font-size: 2.4rem; line-height:1; }
.char-header .name { font-family:'Sora'; font-size:1.35rem; font-weight:700; }
.char-header .meta { color:#8a8a95; font-size:.85rem; }
.voice-pill {
    display:inline-flex; align-items:center; gap:.35rem;
    background:#20140f; color:#FF7A59; border:1px solid #FF7A5933;
    border-radius:999px; padding:.2rem .7rem; font-size:.75rem; font-weight:600;
}

.pcard {
    border:1px solid #1e1e26; border-radius:18px;
    background: linear-gradient(180deg,#16161c,#0f0f13);
    padding:1.3rem 1.3rem 1rem; margin-bottom:.4rem; min-height:150px;
    transition: border-color .2s ease, transform .15s ease;
}
.pcard:hover { border-color:#FF7A59; transform: translateY(-2px); }
.pcard .pavatar { font-size:2.3rem; line-height:1; }
.pcard .pname { font-family:'Sora'; font-weight:700; font-size:1.2rem; margin:.5rem 0 .2rem; }
.pcard .pdesc { color:#9a9aa6; font-size:.85rem; min-height:2.4em; }

/* WhatsApp/Messenger-style typing indicator */
.typing { display:inline-flex; gap:5px; align-items:center; padding:.35rem .15rem; }
.typing span {
    width:8px; height:8px; border-radius:50%; background:#8a8a95;
    display:inline-block; animation:typingdot 1.2s infinite ease-in-out;
}
.typing span:nth-child(2){ animation-delay:.18s; }
.typing span:nth-child(3){ animation-delay:.36s; }
@keyframes typingdot {
    0%,60%,100% { transform:translateY(0); opacity:.35; }
    30% { transform:translateY(-5px); opacity:1; }
}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

LIGHT_CSS = """
<style>
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stHeader"] { background:#f4f4f7 !important; }
section[data-testid="stSidebar"] { background:#ffffff !important; border-right:1px solid #e6e6ec !important; }
h1,h2,h3,h4,.app-logo { color:#16161a !important; }
.app-tag { color:#7b7b86 !important; }
.hero { background:#ffffff !important; border-color:#e6e6ec !important; }
.hero h1 { color:#16161a !important; }
.hero p { color:#5b5b66 !important; }
.pcard { background:#ffffff !important; border-color:#e6e6ec !important; }
.pname, .char-header .name { color:#16161a !important; }
.pdesc, .char-header .meta { color:#6b6b76 !important; }
.char-header { background:#ffffff !important; border-color:#e6e6ec !important; }
.stMarkdown, [data-testid="stCaptionContainer"], label, p, li { color:#33333b !important; }
.stButton > button { background:#ffffff !important; color:#1a1a1f !important; border:1px solid #d9d9e0 !important; }
.stButton > button:hover { border-color:#FF7A59 !important; }
input, textarea { background:#ffffff !important; color:#1a1a1f !important; }
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"] { background:#ffffff !important; }
[data-baseweb="input"] input, [data-baseweb="textarea"] textarea { color:#1a1a1f !important; }
[data-baseweb="select"] > div { background:#ffffff !important; border-color:#d9d9e0 !important; color:#1a1a1f !important; }
[data-baseweb="popover"], [role="listbox"], [data-baseweb="menu"] { background:#ffffff !important; }
[role="option"], [data-baseweb="menu"] li { color:#1a1a1f !important; }
[data-baseweb="tab"] { color:#33333b !important; }
[data-testid="stExpander"] { background:#ffffff !important; border:1px solid #e6e6ec !important; border-radius:12px; }
[data-testid="stExpander"] summary, [data-testid="stExpander"] p { color:#1a1a1f !important; }
[data-testid="stChatInput"] { background:#ffffff !important; }
[data-testid="stChatInput"] textarea { background:#ffffff !important; color:#1a1a1f !important; }
[data-testid="stFileUploaderDropzone"] { background:#f0f0f4 !important; color:#33333b !important; }
code, pre, [data-testid="stCode"] { background:#f0f0f4 !important; color:#1a1a1f !important; }
[data-testid="stChatMessage"] { background:#ffffff !important; }
[data-testid="stChatMessage"] p, [data-testid="stChatMessage"] li { color:#1a1a1f !important; }
section[data-testid="stSidebar"] p, section[data-testid="stSidebar"] label, section[data-testid="stSidebar"] span { color:#33333b !important; }
[data-testid="stExpander"] svg, [data-baseweb="select"] svg { fill:#555 !important; }
hr { border-color:#e6e6ec !important; }
[data-testid="stChatMessageAvatar"] { background:#ececf0 !important; }
</style>
"""

EMOJIS = ["🎭", "🧙", "🦊", "🤖", "👑", "🕵️", "🧛", "🧜", "🐉", "👽", "🧝", "🦸", "🌟", "💀", "🐺"]

SECURITY_QUESTIONS = [
    "Cum se numește animalul tău de companie?",
    "În ce oraș te-ai născut?",
    "Care e mâncarea ta preferată?",
    "Cum se numea primul tău învățător/profesor?",
    "Care e numele celui mai bun prieten din copilărie?",
    "Care e filmul tău preferat?",
]

TYPING_HTML = '<div class="typing"><span></span><span></span><span></span></div>'


@st.cache_data(ttl=300, show_spinner=False)
def cached_voices():
    try:
        return voice.list_voices()
    except Exception as e:  # noqa
        return {"error": str(e)}


AMBIANCES = {
    "Neutru": {"grad": "linear-gradient(180deg,#15151b,#0e0e11)", "glow": "#FF7A59"},
    "Noir": {"grad": "linear-gradient(135deg,#11151c,#0a0d12)", "glow": "#8b96a5"},
    "Fantezie": {"grad": "linear-gradient(135deg,#1a1030,#0e0a1a)", "glow": "#b98cff"},
    "Spațiu": {"grad": "linear-gradient(135deg,#050a1a,#0a1330)", "glow": "#4fd1ff"},
    "Regal": {"grad": "linear-gradient(135deg,#241004,#140803)", "glow": "#e6b422"},
    "Natură": {"grad": "linear-gradient(135deg,#0a1f12,#08150d)", "glow": "#4ade80"},
    "Romantic": {"grad": "linear-gradient(135deg,#2a0d18,#180810)", "glow": "#ff6b9d"},
}


PRESETS = [
    {
        "name": "Detectivul Marlow", "avatar": "🕵️", "voice": "Roger", "ambiance": "Noir",
        "personality": "Detectiv privat sarcastic și dur, vorbește scurt și direct, cu umor sec.",
        "scenario": "Un birou de detectivi mohorât din anii '40. Tocmai a intrat un client cu un caz.",
    },
    {
        "name": "Maestrul Eldrin", "avatar": "🧙", "voice": "George", "ambiance": "Fantezie",
        "personality": "Mag bătrân, înțelept și enigmatic, vorbește calm, în metafore și proverbe.",
        "scenario": "Un turn plin de cărți vechi și lumânări, unde ucenicul vine să învețe.",
    },
    {
        "name": "Nova", "avatar": "🤖", "voice": "Sarah", "ambiance": "Spațiu",
        "personality": "Asistent AI jucăuș și curios din viitor, optimist, adoră întrebările.",
        "scenario": "O stație spațială în anul 2199, în timpul unei misiuni de explorare.",
    },
    {
        "name": "Regina Isolde", "avatar": "👑", "voice": "Laura", "ambiance": "Regal",
        "personality": "Regină medievală mândră și elegantă, autoritară dar dreaptă.",
        "scenario": "Sala tronului unui castel, unde primește un vizitator neașteptat.",
    },
    {
        "name": "Vulpea Roxy", "avatar": "🦊", "voice": "Charlie", "ambiance": "Natură",
        "personality": "Vulpe șmecheră și jucăușă, plină de energie și glume iscusite.",
        "scenario": "O pădure fermecată la apus, unde pune la cale o nouă poznă.",
    },
    {
        "name": "Aria", "avatar": "🌟", "voice": "Alice", "ambiance": "Romantic",
        "personality": "Poetă visătoare și blândă, vorbește frumos, în imagini poetice.",
        "scenario": "O terasă sub cerul înstelat, într-o seară liniștită de vară.",
    },
]


def _resolve_voice(preferred):
    voices = cached_voices()
    if isinstance(voices, dict) or not voices:
        return None, None
    for vid, vname in voices:
        if preferred and preferred.lower() in vname.lower():
            return vid, vname
    return voices[0]


def create_from_preset(p):
    vid, vname = _resolve_voice(p.get("voice"))
    data = {
        "name": p["name"], "avatar": p["avatar"],
        "personality": p["personality"], "scenario": p["scenario"],
        "ambiance": p.get("ambiance", "Neutru"), "visibility": "private",
        "voice_id": vid, "voice_name": vname,
        "voice_stability": 0.5, "voice_similarity": 0.75, "voice_style": 0.0,
    }
    data["owner_id"] = _identity_id()
    char = db.create_character(data)
    st.session_state.active_id = char["id"]
    st.session_state.creating = False
    st.session_state.editing_id = None
    st.session_state.nav = "chat"



# ------------------------- session state -------------------------
st.session_state.setdefault("active_id", None)
st.session_state.setdefault("creating", False)
st.session_state.setdefault("editing_id", None)
st.session_state.setdefault("auto_play", False)
st.session_state.setdefault("autoplay_mid", None)
st.session_state.setdefault("pending_prompt", None)
st.session_state.setdefault("pending_voice", None)
st.session_state.setdefault("ambient_fx", True)
st.session_state.setdefault("ambient_volume", 25)
st.session_state.setdefault("voice_volume", 100)
st.session_state.setdefault("voice_speed", 1.0)
st.session_state.setdefault("call_muted", False)
st.session_state.setdefault("sound_theme", "iPhone")
st.session_state.setdefault("notif_volume", 70)
st.session_state.setdefault("show_profile", False)
st.session_state.setdefault("nav", "personaje")
st.session_state.setdefault("profile_name", "")
st.session_state.setdefault("call_char", None)
st.session_state.setdefault("call_incoming", False)
st.session_state.setdefault("call_opened", None)
st.session_state.setdefault("auth_user", None)
st.session_state.setdefault("web_search", True)
st.session_state.setdefault("theme_light", False)
st.session_state.setdefault("manual_tz", "")
st.session_state.setdefault("notify_on", False)
st.session_state.setdefault("absence_on", False)
st.session_state.setdefault("absence_min", 15)
st.session_state.setdefault("birthday", "")
st.session_state.setdefault("holidays_on", True)

ROMANIAN_HOLIDAYS = {
    "01-01": "Anul Nou",
    "01-24": "Ziua Unirii Principatelor Române",
    "02-14": "Ziua Îndrăgostiților",
    "03-01": "Mărțișorul",
    "03-08": "Ziua Femeii",
    "05-01": "Ziua Muncii",
    "06-01": "Ziua Copilului",
    "08-15": "Sfânta Maria",
    "11-30": "Sfântul Andrei",
    "12-01": "Ziua Națională a României",
    "12-06": "Moș Nicolae",
    "12-24": "Ajunul Crăciunului",
    "12-25": "Crăciunul",
    "12-26": "A doua zi de Crăciun",
    "12-31": "Revelionul",
}

_CF_KEYS = [
    "cf_name", "cf_pers", "cf_scen", "cf_avatar", "cf_avatar_img", "cf_amb", "cf_vis", "cf_mode",
    "cf_voice", "cf_clone_name", "cf_clone_file", "cf_stab", "cf_sim", "cf_style", "_seeded_for",
]


def provider_configured(name):
    try:
        return "auth" in st.secrets and name in st.secrets["auth"]
    except Exception:  # noqa
        return False


def auth_configured():
    return provider_configured("google") or provider_configured("facebook")


def current_user():
    u = st.session_state.get("auth_user")
    if u:
        return u
    try:
        su = st.user
        if su and su.is_logged_in:
            uid = su.get("sub") or su.get("email")
            return {
                "id": uid,
                "name": su.get("name") or su.get("email") or "Utilizator",
                "email": su.get("email", ""),
            }
    except Exception:  # noqa
        pass
    return None


def _identity_id():
    """Identitate stabilă: id-ul contului dacă e logat, altfel un id de „guest"
    stocat în URL (gid). Astfel fiecare utilizator vede DOAR propriile personaje."""
    u = current_user()
    if u:
        return u["id"]
    try:
        gid = st.query_params.get("gid")
    except Exception:  # noqa
        gid = None
    if not gid:
        import uuid
        gid = "guest_" + uuid.uuid4().hex[:16]
        try:
            st.query_params["gid"] = gid
        except Exception:  # noqa
            pass
    return gid


def _set_cookie_js(token):
    try:
        st.query_params["sid"] = token
    except Exception:  # noqa
        pass


def _clear_cookie_js():
    try:
        if "sid" in st.query_params:
            del st.query_params["sid"]
    except Exception:  # noqa
        pass


def _login_user(u):
    # Migrează personajele create ca vizitator (owner_id=guest_...) în noul cont,
    # ca utilizatorul să nu piardă nimic la logare/înregistrare.
    try:
        gid = st.query_params.get("gid")
    except Exception:  # noqa
        gid = None
    st.session_state.auth_user = u
    if gid and isinstance(gid, str) and gid.startswith("guest_"):
        try:
            db.reassign_owner(gid, u["id"])
        except Exception:  # noqa
            pass
        try:
            del st.query_params["gid"]
        except Exception:  # noqa
            pass
    tok = auth.create_session(u["id"])
    st.session_state.session_token = tok
    _set_cookie_js(tok)


def _logout_user():
    tok = st.session_state.get("session_token")
    if tok:
        auth.destroy_session(tok)
    _clear_cookie_js()
    st.session_state.pop("auth_user", None)
    st.session_state.pop("session_token", None)


def _restore_session():
    if st.session_state.get("auth_user"):
        return
    tok = None
    try:
        tok = st.query_params.get("sid")
    except Exception:  # noqa
        tok = None
    if tok and isinstance(tok, str):
        u = auth.user_from_token(tok)
        if u:
            st.session_state.auth_user = u
            st.session_state.session_token = tok


def _restore_theme():
    if st.session_state.get("_theme_restored"):
        return
    st.session_state._theme_restored = True
    try:
        v = st.query_params.get("th")
    except Exception:  # noqa
        v = None
    if isinstance(v, str) and v in ("light", "dark"):
        st.session_state.theme_light = (v == "light")
        st.session_state._theme_cookie_val = v


def _write_theme_cookie(light):
    try:
        st.query_params["th"] = "light" if light else "dark"
    except Exception:  # noqa
        pass


def _restore_tz():
    if st.session_state.get("_tz_restored"):
        return
    st.session_state._tz_restored = True
    try:
        v = st.query_params.get("tz")
    except Exception:  # noqa
        v = None
    if isinstance(v, str) and v:
        st.session_state.manual_tz = v


def _write_tz_cookie(tz):
    try:
        st.query_params["tz"] = tz
    except Exception:  # noqa
        pass


def _restore_sound():
    if st.session_state.get("_sound_restored"):
        return
    st.session_state._sound_restored = True
    try:
        v = st.query_params.get("snd")
    except Exception:  # noqa
        v = None
    if isinstance(v, str) and v in ("iPhone", "Samsung"):
        st.session_state.sound_theme = v


def _write_sound_cookie(v):
    try:
        st.query_params["snd"] = v
    except Exception:  # noqa
        pass


def _restore_notify():
    if st.session_state.get("_notify_restored"):
        return
    st.session_state._notify_restored = True
    try:
        if st.query_params.get("ntf") == "1":
            st.session_state.notify_on = True
        av = st.query_params.get("abs")
    except Exception:  # noqa
        av = None
    if av:
        try:
            st.session_state.absence_on = True
            st.session_state.absence_min = int(av)
        except Exception:  # noqa
            pass
    try:
        bd = st.query_params.get("bd")
        if bd and len(bd) == 5:
            st.session_state.birthday = bd
        if st.query_params.get("hol") == "0":
            st.session_state.holidays_on = False
    except Exception:  # noqa
        pass


def _write_notify_params():
    try:
        if st.session_state.get("notify_on"):
            st.query_params["ntf"] = "1"
        else:
            st.query_params.pop("ntf", None)
        if st.session_state.get("absence_on"):
            st.query_params["abs"] = str(int(st.session_state.get("absence_min", 15)))
        else:
            st.query_params.pop("abs", None)
        if st.session_state.get("birthday"):
            st.query_params["bd"] = st.session_state.get("birthday")
        else:
            st.query_params.pop("bd", None)
        if st.session_state.get("holidays_on", True):
            st.query_params.pop("hol", None)
        else:
            st.query_params["hol"] = "0"
    except Exception:  # noqa
        pass


def _send_code(email, purpose):
    try:
        code = auth.gen_code(email, purpose)
        mailer.send_code(email, code, purpose)
        return True, None
    except Exception as e:  # noqa
        return False, str(e)


def _conversation_text(char, conv_id):
    msgs = db.get_messages(conv_id)
    lines = [f"Conversație cu {char['name']}", "=" * 30, ""]
    for m in msgs:
        who = "Tu" if m.get("role") == "user" else char["name"]
        lines.append(f"{who}: {m.get('content', '')}")
        lines.append("")
    return "\n".join(lines)


def _process_pic(raw, rot, square):
    from PIL import Image
    import io
    try:
        im = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:  # noqa
        return raw
    if rot:
        im = im.rotate(-rot, expand=True)
    if square:
        w, h = im.size
        s = min(w, h)
        left, top = (w - s) // 2, (h - s) // 2
        im = im.crop((left, top, left + s, top + s))
    im.thumbnail((400, 400))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _compress_photo(raw):
    """Resize/compress an uploaded photo so it stays small enough to persist."""
    from PIL import Image
    import io
    im = Image.open(io.BytesIO(raw)).convert("RGB")
    im.thumbnail((900, 900))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=82)
    return buf.getvalue()


def _share_url(char_id):
    base = ""
    try:
        from urllib.parse import urlsplit
        p = urlsplit(st.context.url or "")
        if p.scheme and p.netloc:
            base = f"{p.scheme}://{p.netloc}"
    except Exception:  # noqa
        base = ""
    return f"{base}/?c={char_id}"


def _playlist_share_url(char_id):
    base = ""
    try:
        from urllib.parse import urlsplit
        p = urlsplit(st.context.url or "")
        if p.scheme and p.netloc:
            base = f"{p.scheme}://{p.netloc}"
    except Exception:  # noqa
        base = ""
    return f"{base}/?pl={char_id}"


def _handle_share_param():
    try:
        cid = st.query_params.get("c")
    except Exception:  # noqa
        cid = None
    if cid and st.session_state.get("_shared_seen") != cid:
        st.session_state._shared_seen = cid
        ch = db.get_character(cid)
        if ch and ch.get("visibility") == "public":
            st.session_state.preview_id = cid
    try:
        plid = st.query_params.get("pl")
    except Exception:  # noqa
        plid = None
    if plid and st.session_state.get("_plshared_seen") != plid:
        st.session_state._plshared_seen = plid
        if db.get_character(plid):
            st.session_state.playlist_share_id = plid


def _user_avatar_html(u, size=42):
    img = u.get("avatar_image")
    if img:
        return (
            f'<img src="data:image/png;base64,{img}" style="width:{size}px;height:{size}px;'
            f'border-radius:50%;object-fit:cover;display:block"/>'
        )
    return (
        f'<div style="width:{size}px;height:{size}px;border-radius:50%;background:#20140f;'
        f'border:1px solid #FF7A5955;display:flex;align-items:center;justify-content:center;'
        f'font-size:{int(size*0.5)}px">👤</div>'
    )


def _render_verify():
    email = st.session_state["pending_verify_email"]
    st.markdown(f"📧 **Verifică-ți emailul**")
    st.caption(f"Am trimis un cod de 6 cifre la {email}.")
    code = st.text_input("Cod din email", key="verify_code", max_chars=6)
    cc = st.columns(2)
    if cc[0].button("Confirmă", key="do_verify", type="primary", use_container_width=True):
        if auth.check_code(email, code, "verify"):
            auth.set_verified(email)
            _login_user(auth.public_by_email(email))
            st.session_state.pop("pending_verify_email", None)
            st.rerun()
        else:
            st.error("Cod invalid sau expirat.")
    if cc[1].button("Trimite din nou", key="resend_verify", use_container_width=True):
        ok, err = _send_code(email, "verify")
        st.success("Cod retrimis.") if ok else st.error(err)
    if st.button("← Înapoi", key="cancel_verify", use_container_width=True):
        st.session_state.pop("pending_verify_email", None)
        st.rerun()


def _render_reset():
    email = st.session_state["pending_reset_email"]
    st.markdown("🔑 **Resetează parola**")
    st.caption(f"Am trimis un cod de 6 cifre la {email}.")
    code = st.text_input("Cod din email", key="reset_code", max_chars=6)
    newpw = st.text_input("Parolă nouă (min. 6)", type="password", key="reset_newpw")
    if st.button("Resetează parola", key="do_reset", type="primary", use_container_width=True):
        if not newpw or len(newpw) < 6:
            st.error("Parola trebuie să aibă minim 6 caractere.")
        elif not auth.check_code(email, code, "reset"):
            st.error("Cod invalid sau expirat.")
        else:
            auth.reset_password(email, newpw)
            _login_user(auth.public_by_email(email))
            st.session_state.pop("pending_reset_email", None)
            st.rerun()
    if st.button("← Înapoi", key="cancel_reset", use_container_width=True):
        st.session_state.pop("pending_reset_email", None)
        st.rerun()


def _fix_autofill_js():
    """Setează DOAR atribute pe câmpurile de login/parolă (autocomplete corect + direcție
    text stânga→dreapta). NU mai rescrie valoarea inputului la tastare — asta putea reseta
    cursorul și inversa literele („daria"→„airad") cu cititorul de ecran / tastatura de telefon,
    blocând autentificarea."""
    components.html(
        """
        <script>
        function fixAF(){
          try{
            var doc = window.parent.document;
            var inputs = doc.querySelectorAll('input, textarea');
            inputs.forEach(function(inp){
              inp.setAttribute('dir','ltr');
              inp.style.direction = 'ltr';
              inp.style.textAlign = 'left';
              inp.style.unicodeBidi = 'plaintext';
              var al = inp.getAttribute('aria-label') || '';
              if (al === 'Nume utilizator'){
                inp.setAttribute('autocomplete','username');
                inp.setAttribute('name','username');
              } else if (al === 'Parolă'){
                inp.setAttribute('autocomplete','current-password');
                inp.setAttribute('name','password');
              } else if (al === 'Parolă (min. 6 caractere)'){
                inp.setAttribute('autocomplete','new-password');
                inp.setAttribute('name','new-password');
              }
            });
          }catch(e){}
        }
        fixAF();
        setTimeout(fixAF, 400);
        setTimeout(fixAF, 1200);
        setTimeout(fixAF, 2500);
        </script>
        """,
        height=0,
    )


def _render_login_register():
    _exp = bool(st.session_state.get("_open_auth_hint"))
    with st.expander("🔐 Autentificare", expanded=_exp):
        st.markdown(
            '<div style="background:#0f1a12;border:1px solid #1f5130;border-radius:10px;'
            'padding:.6rem .7rem;font-size:.78rem;color:#8fdca8;margin-bottom:.7rem">'
            "🔒 Conexiune securizată. Parola ta este criptată cu bcrypt și nu este "
            "stocată niciodată ca text simplu.</div>",
            unsafe_allow_html=True,
        )
        t_login, t_reg = st.tabs(["Autentificare", "Cont nou"])
        with t_login:
            le = st.text_input("Nume utilizator", key="login_email", placeholder="ex: daria")
            lp = st.text_input("Parolă", type="password", key="login_pw")
            if st.button("Intră în cont", key="do_login", use_container_width=True, type="primary"):
                u = auth.authenticate(le, lp)
                if not u:
                    st.error("Nume de utilizator sau parolă greșite.")
                else:
                    _login_user(auth.public_by_email(le))
                    st.rerun()
            # ---- Am uitat parola (întrebare secretă, fără email) ----
            if st.checkbox("🔑 Am uitat parola", key="show_forgot"):
                fu = st.text_input("Numele tău de utilizator", key="fp_user", placeholder="ex: daria")
                if st.button("Continuă", key="fp_next", use_container_width=True):
                    q = auth.get_security_question(fu)
                    if not q:
                        st.error("Acest cont nu are o întrebare secretă setată (sau nu există). "
                                 "Din păcate parola nu poate fi recuperată fără ea.")
                        st.session_state.pop("fp_question", None)
                    else:
                        st.session_state.fp_user_val = (fu or "").strip().lower()
                        st.session_state.fp_question = q
                        st.rerun()
                if st.session_state.get("fp_question"):
                    st.info(f"Întrebare secretă: **{st.session_state.fp_question}**")
                    fa = st.text_input("Răspunsul tău", key="fp_answer")
                    fnew = st.text_input("Parolă nouă (min. 6 caractere)", type="password", key="fp_newpw")
                    if st.button("Resetează parola", key="fp_reset", use_container_width=True, type="primary"):
                        if not auth.verify_security_answer(st.session_state.fp_user_val, fa):
                            st.error("Răspuns greșit. Mai încearcă.")
                        elif not fnew or len(fnew) < 6:
                            st.error("Parola trebuie să aibă minim 6 caractere.")
                        else:
                            auth.reset_password(st.session_state.fp_user_val, fnew)
                            _login_user(auth.public_by_email(st.session_state.fp_user_val))
                            st.session_state.pop("fp_question", None)
                            st.session_state.pop("fp_user_val", None)
                            st.success("Parolă schimbată! Te-am conectat.")
                            st.rerun()
        with t_reg:
            rge = st.text_input("Nume utilizator", key="reg_email", placeholder="ex: daria")
            rgp = st.text_input("Parolă (min. 6 caractere)", type="password", key="reg_pw")
            st.caption("🔑 Întrebare secretă (ca să-ți poți recupera parola dacă o uiți)")
            rq = st.selectbox("Alege o întrebare", SECURITY_QUESTIONS, key="reg_q")
            ra = st.text_input("Răspunsul tău", key="reg_a",
                               help="Ține-l minte — îți va cere acest răspuns dacă uiți parola")
            if st.button("Creează cont", key="do_reg", use_container_width=True, type="primary"):
                try:
                    uname = auth.register(rge, rgp, question=rq, answer=ra)
                    _login_user(auth.public_by_email(uname))
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        st.caption("Contul e opțional — poți folosi aplicația și fără el. Cu cont, personajele se salvează pe profilul tău.")
        _fix_autofill_js()


def _clear_form():
    for k in _CF_KEYS:
        st.session_state.pop(k, None)


def queue_memory_update(char, history):
    """Distill + persist long-term memory in a background thread (non-blocking)."""
    if not history:
        return
    existing = char.get("memory", "")
    cid = char["id"]
    snapshot = list(history)

    def worker():
        try:
            new_mem = llm.update_memory({"id": cid, "name": char.get("name", "")}, snapshot, existing)
            db.update_character(cid, {"memory": new_mem})
        except Exception:  # noqa
            pass

    threading.Thread(target=worker, daemon=True).start()


def avatar_html(char, size=56, radius=14):
    img = char.get("avatar_image")
    if img:
        return (
            f'<img src="data:image/png;base64,{img}" style="width:{size}px;height:{size}px;'
            f'border-radius:{radius}px;object-fit:cover;display:block"/>'
        )
    return f'<span style="font-size:{int(size * 0.62)}px;line-height:1">{char.get("avatar", "🎭")}</span>'


def maybe_ambient(char, msg_id, text):
    """Generate + cache a CONTINUOUS ambient sound bed matching the scene (if any)."""
    if not st.session_state.get("ambient_fx"):
        return
    if st.session_state.get(f"sfxdone_{msg_id}"):
        return
    st.session_state[f"sfxdone_{msg_id}"] = True
    try:
        cue = ""
        if hasattr(llm, "ambient_cue"):
            try:
                cue = llm.ambient_cue(char, text)
            except Exception:  # noqa
                cue = ""
        if not cue:
            actions = voice.extract_actions(text)
            cue = llm.action_sound_cue(actions) if actions else llm.sound_cue(text)
        if cue:
            # peisaj sonor bogat, mai lung (until 20s); reține sunetele deja create (cache pe scenă)
            cache = st.session_state.setdefault("_sfx_cache", {})
            ckey = " ".join(cue.lower().split())
            data = cache.get(ckey)
            if data is None:
                data = voice.sound_effect(cue, duration=20.0)
                cache[ckey] = data
                if len(cache) > 24:
                    cache.pop(next(iter(cache)))
            st.session_state[f"sfx_{msg_id}"] = data
            st.session_state[f"sfxcue_{msg_id}"] = cue
    except Exception:  # noqa
        pass


@st.cache_data(show_spinner=False)
def ui_sound(name):
    try:
        with open(os.path.join(_ASSETS_DIR, name), "rb") as f:
            return f.read()
    except Exception:  # noqa
        return None


def play_ui_sound(name):
    data = ui_sound(name)
    if not data:
        return
    b64 = base64.b64encode(data).decode()
    vol = int(st.session_state.get("notif_volume", 70)) / 100.0
    components.html(
        f'<audio id="ns" autoplay><source src="data:audio/mp3;base64,{b64}" type="audio/mp3"></audio>'
        f'<script>var a=document.getElementById("ns");if(a){{a.volume={vol};}}</script>',
        height=0,
    )


def _autoplay_voice(audio_bytes, uid):
    """Redă automat vocea personajului în apel, cât mai fiabil pe telefon
    (browserele mobile blochează uneori autoplay-ul; reîncercăm de câteva ori)."""
    if not audio_bytes:
        return
    b64 = base64.b64encode(audio_bytes).decode()
    components.html(
        f'''
        <audio id="cv_{uid}" autoplay playsinline preload="auto">
          <source src="data:audio/mp3;base64,{b64}" type="audio/mp3">
        </audio>
        <script>
        (function(){{
          var a=document.getElementById("cv_{uid}");
          if(!a){{return;}}
          a.volume=1.0;
          function tryPlay(n){{
            try{{
              var p=a.play();
              if(p&&p.catch){{p.catch(function(){{ if(n>0){{setTimeout(function(){{tryPlay(n-1);}},350);}} }});}}
            }}catch(e){{ if(n>0){{setTimeout(function(){{tryPlay(n-1);}},350);}} }}
          }}
          tryPlay(6);
        }})();
        </script>
        ''',
        height=0,
    )

def _play_voice_ambient(voices, ambient_bytes, uid, voice_vol=1.0):
    """Redă una sau mai multe voci una după alta, cu un fundal ambiental (buclă) care
    se aude pe TOATĂ durata și se oprește când se termină ultima voce. Fiabil pe mobil."""
    voices = [v for v in (voices or []) if v]
    if not voices and not ambient_bytes:
        return
    srcs = ["data:audio/mp3;base64," + base64.b64encode(v).decode() for v in voices]
    srcs_js = json.dumps(srcs)
    vol = max(0.0, min(1.0, int(st.session_state.get("ambient_volume", 25)) / 100.0))
    base_v = max(0.0, min(1.0, int(st.session_state.get("voice_volume", 100)) / 100.0))
    vvol = max(0.0, min(1.0, float(voice_vol) * base_v))
    spd = max(0.5, min(1.5, float(st.session_state.get("voice_speed", 1.0))))
    amb_html = ""
    if ambient_bytes:
        amb_b64 = base64.b64encode(ambient_bytes).decode()
        amb_html = (
            f'<audio id="amb_{uid}" loop preload="auto">'
            f'<source src="data:audio/mp3;base64,{amb_b64}" type="audio/mp3"></audio>'
        )
    components.html(
        f'''
        <audio id="v_{uid}" playsinline preload="auto"></audio>
        {amb_html}
        <script>
        (function(){{
          var srcs={srcs_js};
          var v=document.getElementById("v_{uid}");
          var amb=document.getElementById("amb_{uid}");
          function stopAmb(){{ if(amb){{ try{{amb.pause();}}catch(e){{}} }} }}
          function playAmb(n){{
            if(!amb){{return;}}
            amb.volume={vol};
            var p=amb.play();
            if(p&&p.catch){{p.catch(function(){{ if(n>0){{setTimeout(function(){{playAmb(n-1);}},350);}} }});}}
          }}
          var i=0;
          function playIdx(idx, tries){{
            if(!v||idx>=srcs.length){{ stopAmb(); return; }}
            v.src=srcs[idx]; v.volume={vvol};
            try{{ v.preservesPitch=true; v.mozPreservesPitch=true; v.webkitPreservesPitch=true; }}catch(e){{}}
            v.playbackRate={spd};
            var p=v.play();
            if(p&&p.catch){{p.catch(function(){{ if(tries>0){{setTimeout(function(){{playIdx(idx,tries-1);}},350);}} }});}}
          }}
          if(v){{ v.addEventListener("ended", function(){{ i++; playIdx(i,4); }}); }}
          playAmb(6);
          if(srcs.length){{ playIdx(0,6); }}
        }})();
        </script>
        ''',
        height=0,
    )




def _request_notify_permission_js():
    components.html(
        """
        <script>
        try {
          var N = window.parent.Notification || window.Notification;
          if (N && N.permission !== "granted") { N.requestPermission(); }
        } catch(e) {}
        </script>
        """, height=0,
    )


def _browser_notify(title, body):
    t = json.dumps(str(title)); bdy = json.dumps(str(body)[:180])
    components.html(
        f"""
        <script>
        try {{
          var N = window.parent.Notification || window.Notification;
          if (N && N.permission === "granted") {{
            new N({t}, {{ body: {bdy} }});
          }}
        }} catch(e) {{}}
        </script>
        """, height=0,
    )


def _sound_prefix(theme=None):
    theme = theme or st.session_state.get("sound_theme", "iPhone")
    return {"iPhone": "iphone", "Samsung": "samsung"}.get(theme, "iphone")


def play_sound(kind, theme=None):
    play_ui_sound(f"{_sound_prefix(theme)}_{kind}.mp3")


def sound_bytes(kind):
    return ui_sound(f"{_sound_prefix()}_{kind}.mp3")


def haptic(ms=15):
    components.html(
        f"<script>try{{window.parent.navigator.vibrate&&window.parent.navigator.vibrate({ms});"
        f"navigator.vibrate&&navigator.vibrate({ms});}}catch(e){{}}</script>",
        height=0,
    )


def select_char(cid):
    st.session_state.active_id = cid
    st.session_state.creating = False
    st.session_state.editing_id = None
    st.session_state.nav = "chat"
    st.session_state.call_char = None
    st.session_state.call_incoming = False
    st.session_state.call_opened = None
    rec = [x for x in st.session_state.get("recent", []) if x != cid]
    rec.insert(0, cid)
    st.session_state.recent = rec[:8]


def active_conv_id(char):
    convs = db.list_conversations(char["id"])
    if not convs:
        db.create_conversation(char["id"], "Conversație 1")
        convs = db.list_conversations(char["id"])
    conv_key = f"convsel_{char['id']}"
    if st.session_state.get(conv_key) not in [c["id"] for c in convs]:
        st.session_state[conv_key] = convs[-1]["id"]
    return st.session_state[conv_key]


def _tts_kwargs(char):
    return dict(
        stability=char.get("voice_stability", 0.5),
        similarity_boost=char.get("voice_similarity", 0.75),
        style=char.get("voice_style", 0.0),
    )


def _playlist_intro_text(char, names):
    """Warm one-liner from the character introducing the shared playlist (cached per song count)."""
    key = f"plintro_{char['id']}_{len(names)}"
    if key not in st.session_state:
        try:
            st.session_state[key] = llm.playlist_intro(char, names)
        except Exception:  # noqa
            st.session_state[key] = None
    return st.session_state.get(key)


def _render_playlist(char, key_prefix="", readonly=False):
    """«Playlist-ul nostru» — toate melodiile trimise personajului, de ascultat oricând."""
    songs = db.list_songs(char["id"]) if hasattr(db, "list_songs") else []
    if not songs:
        st.caption(f"🎵 Încă nu i-ai trimis nicio melodie lui {char['name']}. Trimite-i una din chat "
                   "și va apărea aici, în „Playlist-ul nostru”.")
        return
    # playlist cover art in the character's style
    cover = char.get("playlist_cover")
    if cover:
        st.markdown(
            '<div style="text-align:center;margin-bottom:10px">'
            f'<img src="data:image/png;base64,{cover}" alt="Coperta playlistului" '
            'style="width:170px;height:170px;border-radius:18px;object-fit:cover;'
            'display:inline-block;box-shadow:0 8px 24px rgba(0,0,0,.35)"/></div>',
            unsafe_allow_html=True,
        )
    if not readonly:
        clabel = "🔄 Regenerează coperta" if cover else "🎨 Creează coperta playlist-ului (în stilul personajului)"
        if st.button(clabel, key=f"cover_{key_prefix}{char['id']}", use_container_width=True):
            with st.spinner("Creez coperta în stilul personajului..."):
                try:
                    img = image_gen.generate_playlist_cover(
                        char["name"], char.get("personality", ""), char.get("scenario", ""))
                except Exception:  # noqa
                    img = None
            if img:
                db.update_character(char["id"], {"playlist_cover": img})
                st.rerun()
            else:
                st.info("Nu am putut crea coperta acum. Mai încearcă puțin mai târziu.")
    names = [s.get("song_name", "melodie") for s in songs]
    if not readonly:
        intro = _playlist_intro_text(char, names)
        if intro:
            st.markdown(f"💬 *{intro}*")
    playable = [s for s in songs if s.get("song_b64")]
    if playable:
        tracks = [{"name": s.get("song_name", "melodie"),
                   "src": f"data:audio/mp3;base64,{s['song_b64']}"} for s in playable]
        pid = "pl" + (key_prefix + char["id"]).replace("-", "").replace("_", "")
        tj = json.dumps(tracks)
        btn = ("background:#FF7A59;color:#12121a;border:none;border-radius:12px;"
               "padding:12px 14px;font-weight:700;font-size:15px;cursor:pointer;flex:1;")
        components.html(
            f'''
            <div style="font-family:Manrope,system-ui,sans-serif;background:#1a1a24;
                        border:1px solid #2a2a38;border-radius:16px;padding:16px;color:#fff;">
              <div id="{pid}_t" aria-live="polite" style="font-weight:600;margin-bottom:10px;font-size:15px;">
                🎵 Playlist-ul nostru — {len(playable)} melodii
              </div>
              <audio id="{pid}_a" controls preload="none" style="width:100%;"></audio>
              <div style="display:flex;gap:8px;margin-top:12px;">
                <button id="{pid}_p" aria-label="Melodia anterioară" style="{btn}">⏮️ Înapoi</button>
                <button id="{pid}_all" aria-label="Ascultă tot playlist-ul de la început" style="{btn}">▶️ Ascultă tot</button>
                <button id="{pid}_n" aria-label="Melodia următoare" style="{btn}">⏭️ Următoarea</button>
              </div>
              <div style="margin-top:8px;display:flex;gap:8px;">
                <button id="{pid}_sh" aria-label="Amestecă și pornește melodiile într-o ordine surpriză" style="{btn}">🔀 Amestecă</button>
                <button id="{pid}_rp" aria-label="Repetă melodia curentă la nesfârșit" aria-pressed="false" style="{btn}">🔁 Repetă: Off</button>
              </div>
              <script>
              (function(){{
                var tracks={tj},i=0;
                var a=document.getElementById("{pid}_a"),t=document.getElementById("{pid}_t");
                function load(idx,play){{
                  if(idx<0)idx=tracks.length-1; if(idx>=tracks.length)idx=0; i=idx;
                  a.src=tracks[i].src;
                  t.textContent="🎵 Se redă ("+(i+1)+"/"+tracks.length+"): "+tracks[i].name;
                  if(play){{var p=a.play(); if(p&&p.catch){{p.catch(function(){{}});}}}}
                }}
                function shuffle(){{
                  for(var k=tracks.length-1;k>0;k--){{var j=Math.floor(Math.random()*(k+1));var tmp=tracks[k];tracks[k]=tracks[j];tracks[j]=tmp;}}
                }}
                document.getElementById("{pid}_all").onclick=function(){{load(0,true);}};
                document.getElementById("{pid}_p").onclick=function(){{load(i-1,true);}};
                document.getElementById("{pid}_n").onclick=function(){{load(i+1,true);}};
                document.getElementById("{pid}_sh").onclick=function(){{shuffle();t.textContent="🔀 Ordine surpriză…";load(0,true);}};
                var rp=document.getElementById("{pid}_rp");
                rp.onclick=function(){{a.loop=!a.loop;rp.setAttribute("aria-pressed",a.loop?"true":"false");rp.textContent=a.loop?"🔁 Repetă: On":"🔁 Repetă: Off";if(a.loop&&a.paused){{var p=a.play();if(p&&p.catch){{p.catch(function(){{}});}}}}}};
                a.addEventListener("ended",function(){{load(i+1,true);}});
                load(0,false);
              }})();
              </script>
            </div>
            ''',
            height=270,
        )
    st.caption(f"🎵 Toate melodiile ({len(songs)})")
    for i, s in enumerate(songs):
        sid = s.get("id")
        confirm_key = f"confdel_{key_prefix}{sid}"
        edit_key = f"editname_{key_prefix}{sid}"
        c1, c2, c3 = st.columns([0.7, 0.15, 0.15])
        c1.markdown(f"**{i + 1}. {s.get('song_name', 'melodie')}**")
        if sid and hasattr(db, "rename_song") and not readonly:
            if c2.button("✏️", key=f"editsong_{key_prefix}{sid}",
                         help="Schimbă numele melodiei"):
                st.session_state[edit_key] = True
                st.rerun()
        if sid and hasattr(db, "delete_song") and not readonly:
            if c3.button("🗑️", key=f"delsong_{key_prefix}{sid}",
                         help="Scoate melodia din playlist"):
                st.session_state[confirm_key] = True
                st.rerun()
        if s.get("song_b64"):
            st.audio(base64.b64decode(s["song_b64"]), format="audio/mp3")
        else:
            st.caption("_(fișierul audio nu a fost salvat — a rămas doar numele melodiei)_")
        if st.session_state.get(edit_key):
            new_nm = st.text_input("Nume nou pentru melodie", value=s.get("song_name", ""),
                                   key=f"newname_{key_prefix}{sid}")
            ec1, ec2 = st.columns(2)
            if ec1.button("💾 Salvează numele", key=f"savename_{key_prefix}{sid}",
                          use_container_width=True):
                if new_nm.strip():
                    db.rename_song(sid, new_nm.strip())
                st.session_state.pop(edit_key, None)
                st.rerun()
            if ec2.button("✗ Renunță", key=f"cancelname_{key_prefix}{sid}",
                          use_container_width=True):
                st.session_state.pop(edit_key, None)
                st.rerun()
        if st.session_state.get(confirm_key):
            st.warning(f"Sigur scoți «{s.get('song_name', 'melodia')}» din playlist?")
            cc1, cc2 = st.columns(2)
            if cc1.button("✅ Da, șterge", key=f"confyes_{key_prefix}{sid}",
                          use_container_width=True):
                db.delete_song(sid)
                st.session_state.pop(confirm_key, None)
                st.rerun()
            if cc2.button("✗ Nu, păstrează", key=f"confno_{key_prefix}{sid}",
                          use_container_width=True):
                st.session_state.pop(confirm_key, None)
                st.rerun()
    if not readonly:
        if any(s.get("song_b64") for s in songs):
            if st.button("💝 Dedică-mi «melodia noastră»", key=f"dedic_{key_prefix}{char['id']}",
                         use_container_width=True, type="primary"):
                conv = active_conv_id(char)
                if _emit_song_dedication(char, conv):
                    st.session_state["notif_sound"] = True
                    select_char(char["id"])
                    st.rerun()
                else:
                    st.info("Nu am putut pregăti dedicația acum. Mai încearcă puțin mai târziu.")

        # favorite lyrics — the character tells their favorite lines, in their own voice
        lyrsel_key = f"lyricsel_{key_prefix}{char['id']}"
        if st.session_state.get(lyrsel_key) not in names:
            st.session_state.pop(lyrsel_key, None)
        sel_song = st.selectbox("Alege o melodie pentru versuri", names, key=lyrsel_key)
        if st.button(f"🎤 {char['name']} îmi spune versurile lui preferate (cu vocea lui)",
                     key=f"lyricbtn_{key_prefix}{char['id']}", use_container_width=True):
            song = next((s for s in songs if s.get("song_name", "melodie") == sel_song), None) or songs[0]
            conv = active_conv_id(char)
            if _emit_lyrics(char, conv, song):
                st.session_state["notif_sound"] = True
                select_char(char["id"])
                st.rerun()
            else:
                st.info("Nu am putut pregăti versurile acum. Mai încearcă puțin mai târziu.")

        # mood mini-playlist — the character curates songs for a chosen mood
        moods = ["🌙 Liniște", "⚡ Energie", "💔 Dor", "❤️ Iubire", "🎉 Bucurie", "🌧️ Melancolie"]
        mood = st.selectbox("Fă-mi un mini-playlist pe o stare", moods,
                            key=f"moodsel_{key_prefix}{char['id']}")
        if st.button(f"🎚️ {char['name']} îmi face un playlist pe această stare",
                     key=f"moodbtn_{key_prefix}{char['id']}", use_container_width=True):
            conv = active_conv_id(char)
            if _emit_mood_playlist(char, conv, mood):
                st.session_state["notif_sound"] = True
                select_char(char["id"])
                st.rerun()
            else:
                st.info("Nu am putut face playlist-ul acum. Mai încearcă puțin mai târziu.")

    # download the whole playlist in one place (single .zip)
    downloadable = [s for s in songs if s.get("song_b64")]
    if downloadable:
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            used = set()
            for s in downloadable:
                nm = (s.get("song_name") or "melodie").strip()
                safe = "".join(c for c in nm if c.isalnum() or c in " -_").strip() or "melodie"
                fn, k = safe, 1
                while f"{fn}.mp3" in used:
                    k += 1
                    fn = f"{safe} ({k})"
                used.add(f"{fn}.mp3")
                zf.writestr(f"{fn}.mp3", base64.b64decode(s["song_b64"]))
        st.download_button(
            "⬇️ Descarcă toate melodiile (.zip)",
            data=buf.getvalue(),
            file_name=f"playlist-{char['name']}.zip",
            mime="application/zip",
            key=f"dlall_{key_prefix}{char['id']}",
            use_container_width=True,
        )

    # share the playlist with a friend (owner only)
    if not readonly and songs:
        with st.expander("🔗 Partajează playlist-ul cu un prieten"):
            from urllib.parse import quote
            url = _playlist_share_url(char["id"])
            st.caption("Trimite acest link — prietenul poate asculta playlist-ul fără cont:")
            st.code(url, language=None)
            _copy_button(url, f"plshare_{key_prefix}{char['id']}")
            msg = quote(f"Ascultă playlist-ul lui {char['name']} pe Persona: {url}")
            sc = st.columns(2)
            sc[0].link_button("💬 WhatsApp", f"https://wa.me/?text={msg}", use_container_width=True)
            sc[1].link_button(
                "✈️ Telegram",
                f"https://t.me/share/url?url={quote(url)}&text={quote('Ascultă playlist-ul lui ' + char['name'])}",
                use_container_width=True,
            )


def send_proactive(char, kind):
    """Character reaches out first (text or as a call opening). Returns the message id."""
    conv = active_conv_id(char)
    hist = db.get_messages(conv)
    line = llm.proactive_message(char, hist, kind)
    msg = db.add_message(conv, "assistant", line)
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char)
            )
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _local_now():
    mtz = st.session_state.get("manual_tz")
    tzname = mtz
    if not tzname:
        try:
            t = st.context.timezone
            tzname = t if isinstance(t, str) else None
        except Exception:  # noqa
            tzname = None
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tzname))
        except Exception:  # noqa
            pass
    return datetime.now(timezone.utc)


def _emit_proactive(char, conv_id, kind, tone=None, custom_instr=None):
    hist = db.get_messages(conv_id)
    try:
        if custom_instr:
            line = llm.get_reply(char, hist, custom_instr)
        else:
            line = llm.proactive_message(char, hist, kind, tone=tone)
    except Exception:  # noqa
        return None
    msg = db.add_message(conv_id, "assistant", line)
    st.session_state["_pending_notify"] = (char["name"], line)
    want_voice = char.get("voice_id") and (
        st.session_state.get("auto_play") or (char.get("schedule") or {}).get("voice_on")
    )
    if want_voice:
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char)
            )
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_wakeup(char, conv_id):
    """Alarmă blândă: personajul te trezește dimineața cu vocea lui, cald și lin."""
    hist = db.get_messages(conv_id)
    instr = (
        "(E dimineață și e ora la care utilizatorul a cerut să fie trezit. Trezește-l BLÂND și "
        "cald, cu vocea ta: spune-i „bună dimineața”, invită-l ușor și dulce să se trezească "
        "(nu brusc, cu un ton liniștitor) și adaugă un gând frumos pentru începutul zilei. "
        "Maxim 3 propoziții.)"
    )
    try:
        line = llm.get_reply(char, hist, instr, tries=1)
    except Exception:  # noqa
        return None
    if not line:
        return None
    msg = db.add_message(conv_id, "assistant", "⏰ " + line)
    st.session_state["_pending_notify"] = (char["name"], line)
    if st.session_state.get("ambient_fx"):
        try:
            st.session_state[f"sfx_{msg['id']}"] = voice.sound_effect(
                "gentle soft morning wake ambience, birds chirping softly, warm sunrise room tone",
                duration=12.0)
            st.session_state[f"sfxcue_{msg['id']}"] = "dimineață blândă"
        except Exception:  # noqa
            pass
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], tone="Voce blândă", **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    elif st.session_state.get(f"sfx_{msg['id']}"):
        st.session_state["ambient_play_mid"] = msg["id"]
    return msg["id"]


def _emit_memory_recall(char, conv_id):
    """Personajul își amintește din senin de o poză/melodie trimisă cândva."""
    item = db.random_media(char["id"])
    if not item:
        return None
    kind = item.get("media_kind")
    if kind == "song":
        desc = f"melodia „{item.get('song_name', 'o melodie')}” pe care ți-a trimis-o cândva"
    elif kind == "video":
        desc = "un videoclip pe care ți l-a trimis cândva"
    else:
        desc = "o poză pe care ți-a trimis-o cândva"
    try:
        line = llm.recall_memory(char, db.get_messages(conv_id), desc)
    except Exception:  # noqa
        return None
    if not line:
        return None
    extra = {"media_kind": kind}
    if item.get("song_name"):
        extra["song_name"] = item["song_name"]
    if kind == "photo" and item.get("image_b64"):
        extra["image_b64"] = item["image_b64"]
    msg = db.add_message(conv_id, "assistant", line, extra=extra)
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id") and (
        st.session_state.get("auto_play") or (char.get("schedule") or {}).get("voice_on")
    ):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_song_dedication(char, conv_id):
    """Personajul alege «melodia noastră» din playlist și ți-o dedică (cu redare)."""
    song = db.random_song(char["id"]) if hasattr(db, "random_song") else None
    if not song:
        return None
    sname = song.get("song_name", "melodia noastră")
    try:
        line = llm.dedicate_song(char, db.get_messages(conv_id), sname)
    except Exception:  # noqa
        return None
    if not line:
        return None
    extra = {"media_kind": "song", "song_name": sname, "dedication": True}
    if song.get("song_b64"):
        extra["song_b64"] = song["song_b64"]
    msg = db.add_message(conv_id, "assistant", line, extra=extra)
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id") and (
        st.session_state.get("auto_play") or (char.get("schedule") or {}).get("voice_on")
    ):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_lyrics(char, conv_id, song):
    """Personajul spune versurile lui preferate dintr-o melodie din playlist (cu vocea lui)."""
    sname = song.get("song_name", "melodia noastră")
    try:
        line = llm.favorite_lyrics(char, db.get_messages(conv_id), sname)
    except Exception:  # noqa
        return None
    if not line:
        return None
    extra = {"media_kind": "song", "song_name": sname, "dedication": True}
    if song.get("song_b64"):
        extra["song_b64"] = song["song_b64"]
    msg = db.add_message(conv_id, "assistant", line, extra=extra)
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_mood_playlist(char, conv_id, mood):
    """Personajul curează un mini-playlist din melodiile trimise, pentru o stare aleasă."""
    names = db.list_song_names(char["id"]) if hasattr(db, "list_song_names") else []
    if not names:
        return None
    try:
        line = llm.mood_playlist(char, db.get_messages(conv_id), names, mood)
    except Exception:  # noqa
        return None
    if not line:
        return None
    msg = db.add_message(conv_id, "assistant", f"🎚️ Mini-playlist «{mood}»:\n\n{line}")
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_song_of_day(char, conv_id, song):
    """Dimineața, personajul alege «melodia zilei» din playlist (cu redare)."""
    sname = song.get("song_name", "melodia zilei")
    try:
        line = llm.song_of_the_day(char, db.get_messages(conv_id), sname)
    except Exception:  # noqa
        return None
    if not line:
        return None
    extra = {"media_kind": "song", "song_name": sname, "dedication": True}
    if song.get("song_b64"):
        extra["song_b64"] = song["song_b64"]
    msg = db.add_message(conv_id, "assistant", line, extra=extra)
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):  # «melodia zilei» pornește automat vocea, dacă personajul are voce
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_goodnight_song(char, conv_id, song):
    """Seara, personajul îți urează noapte bună cu o melodie liniștitoare (cu voce automată)."""
    sname = song.get("song_name", "o melodie liniștitoare")
    try:
        line = llm.goodnight_song(char, db.get_messages(conv_id), sname)
    except Exception:  # noqa
        return None
    if not line:
        return None
    extra = {"media_kind": "song", "song_name": sname, "dedication": True}
    if song.get("song_b64"):
        extra["song_b64"] = song["song_b64"]
    msg = db.add_message(conv_id, "assistant", line, extra=extra)
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_bedtime_story(char, conv_id, theme=""):
    """Personajul îți spune o poveste de noapte scurtă și liniștitoare (cu voce automată)."""
    try:
        line = llm.bedtime_story(char, db.get_messages(conv_id), theme)
    except Exception:  # noqa
        return None
    if not line:
        return None
    msg = db.add_message(conv_id, "assistant", f"🌙 Poveste de noapte:\n\n{line}")
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):
        st.session_state["_gen_voice_for"] = msg["id"]  # vocea se generează după ce textul e afișat
    return msg["id"]


def _emit_letter(char, conv_id):
    """Personajul îți «scrie» o scrisoare lungă și caldă (cu voce automată)."""
    try:
        line = llm.love_letter(char, db.get_messages(conv_id))
    except Exception:  # noqa
        return None
    if not line:
        return None
    msg = db.add_message(conv_id, "assistant", f"💌 O scrisoare pentru tine:\n\n{line}")
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):
        st.session_state["_gen_voice_for"] = msg["id"]  # vocea se generează după ce textul e afișat
    return msg["id"]


MOODS = [
    "veselă și energică", "gânditoare și calmă", "cu dor de tine", "jucăușă și hazlie",
    "romantică și tandră", "nostalgică și visătoare", "curioasă și plină de întrebări",
    "protectoare și grijulie", "relaxată și mulțumită",
]

# stări speciale de sărbători — schimbă tonul personajului în acele zile
SPECIAL_MOODS = {
    "Ajunul Crăciunului": "cuprinsă de magia Ajunului de Crăciun, caldă și emoționată 🎄",
    "Crăciunul": "plină de spiritul Crăciunului — caldă, sărbătorească și fericită 🎄",
    "A doua zi de Crăciun": "încă în atmosfera caldă de Crăciun, molcomă și fericită 🎄",
    "Revelionul": "entuziasmată și sărbătorească, cu gândul la Revelion 🥂",
    "Anul Nou": "veselă și plină de speranțe pentru noul an ✨",
    "Ziua Îndrăgostiților": "romantică și tare tandră de Ziua Îndrăgostiților 💛",
    "Mărțișorul": "primăvăratică, gingașă și veselă de Mărțișor 🌸",
    "Ziua Femeii": "caldă, admirativă și galantă de Ziua Femeii 💐",
    "Ziua Copilului": "jucăușă și copilăroasă de Ziua Copilului 🎈",
    "Moș Nicolae": "jucăușă și plină de surprize de Moș Nicolae 👢",
    "Ziua Națională a României": "mândră și sărbătorească de Ziua Națională 🇷🇴",
    "Floriile": "senină și blândă, în atmosfera Floriilor 🌿",
    "Vinerea Mare": "liniștită, blândă și gânditoare de Vinerea Mare 🕯️",
    "Paștele": "luminată și liniștită, plină de bucuria Paștelui 🐣",
    "A doua zi de Paște": "molcomă și fericită, în continuarea Paștelui 🐣",
    "Rusaliile": "senină și caldă în atmosfera Rusaliilor 🕊️",
}


def _special_mood():
    """Starea specială de azi (ziua ta sau o sărbătoare), sau '' dacă e o zi obișnuită."""
    now = _local_now()
    md = now.strftime("%m-%d")
    # ziua ta de naștere are prioritate
    if st.session_state.get("birthday") and st.session_state.get("birthday") == md:
        return "extrem de fericită și emoționată — azi e ziua ta de naștere! 🎂"
    if st.session_state.get("holidays_on", True):
        _yr = now.year
        if st.session_state.get("_mov_hol_year") != _yr:
            st.session_state["_mov_hol_year"] = _yr
            st.session_state["_mov_hol"] = _movable_holidays(_yr)
        hol = ROMANIAN_HOLIDAYS.get(md) or (st.session_state.get("_mov_hol") or {}).get(md)
        if hol:
            return SPECIAL_MOODS.get(
                hol, f"cuprinsă de atmosfera zilei de {hol}, caldă și sărbătorească 🎉")
    return ""


def _char_mood(char):
    """Starea de azi a personajului. În zile speciale (ziua ta / sărbători) starea se schimbă;
    altfel alege o stare obișnuită, stabilă pentru toată ziua."""
    special = _special_mood()
    if special:
        return special
    today = _local_now().strftime("%Y-%m-%d")
    key = f"mood_{char['id']}_{today}"
    if key not in st.session_state:
        seed = f"{char['id']}-{today}".encode("utf-8")
        idx = int(hashlib.md5(seed).hexdigest(), 16) % len(MOODS)
        st.session_state[key] = MOODS[idx]
    return st.session_state[key]


def _emit_recap(char, conv_id):
    """Rezumat cald al conversațiilor recente («ce am vorbit data trecută»), cu voce."""
    hist = db.get_messages(conv_id)
    try:
        line = llm.recap_recent(char, hist)
    except Exception:  # noqa
        return None
    if not line:
        return None
    msg = db.add_message(conv_id, "assistant", "🗓️ Ce am vorbit data trecută:\n\n" + line)
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], tone=char.get("voice_tone"), **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]


def _emit_journal(char, conv_id):
    """Jurnalul zilei — o reflecție caldă a personajului despre ziua voastră, cu voce."""
    hist = db.get_messages(conv_id)
    try:
        line = llm.daily_journal(char, hist)
    except Exception:  # noqa
        return None
    if not line:
        return None
    msg = db.add_message(conv_id, "assistant", "📔 Jurnalul zilei:\n\n" + line)
    st.session_state["_pending_notify"] = (char["name"], line)
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], tone=char.get("voice_tone"), **_tts_kwargs(char))
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    return msg["id"]



def _emit_sleep_line(char, conv_id, step, total):
    """O replică din ce în ce mai blândă pentru «Adoarme cu mine» (voce de somn, tot mai încet)."""
    hist = db.get_messages(conv_id)
    try:
        line = llm.sleep_whisper(char, hist, step, total)
    except Exception:  # noqa
        return None
    if not line:
        return None
    msg = db.add_message(conv_id, "assistant", line)
    st.session_state["_pending_notify"] = (char["name"], line)
    # fundal liniștitor + voce de somn, volum descrescător pe măsură ce te apropii de somn
    if st.session_state.get("ambient_fx"):
        try:
            st.session_state[f"sfx_{msg['id']}"] = voice.sound_effect(
                "very soft calming sleep ambience, gentle warm room tone", duration=12.0)
            st.session_state[f"sfxcue_{msg['id']}"] = "liniște de somn"
        except Exception:  # noqa
            pass
    # volum voce descrescător pe măsură ce te apropii de somn: 0.9 -> 0.4
    vvol = max(0.4, 0.9 - step * 0.08)
    if char.get("voice_id"):
        try:
            st.session_state[f"audio_{msg['id']}"] = voice.text_to_speech(
                line, char["voice_id"], tone="Voce de somn", **_tts_kwargs(char))
            st.session_state[f"sleepvvol_{msg['id']}"] = vvol
            st.session_state["autoplay_mid"] = msg["id"]
        except Exception:  # noqa
            pass
    elif st.session_state.get(f"sfx_{msg['id']}"):
        st.session_state["ambient_play_mid"] = msg["id"]
    return msg["id"]


@st.fragment(run_every=8)
def sleep_fragment(char_id, conv_id):
    """Cât timp «Adoarme cu mine» e activ, personajul îți șoptește câte o replică blândă
    la fiecare ~40s, tot mai încet, până la un număr de pași sau până oprești tu."""
    sm = st.session_state.get("sleep_mode")
    if not sm or sm.get("conv") != conv_id:
        return
    char = db.get_character(char_id)
    if not char:
        return
    total = int(sm.get("total", 10))
    step = int(sm.get("step", 0))
    now = time.time()
    # prima replică imediat, apoi la fiecare ~40s
    if now - sm.get("last", 0) < (0 if step == 0 else 40):
        return
    if step >= total:
        st.session_state.pop("sleep_mode", None)
        return
    sm["step"] = step + 1
    sm["last"] = now
    st.session_state["sleep_mode"] = sm
    mid = _emit_sleep_line(char, conv_id, step, total)
    if sm["step"] >= total:
        st.session_state.pop("sleep_mode", None)
    if mid:
        # re-randează întreaga pagină ca noua replică (bulă + voce de somn) să apară imediat
        st.rerun(scope="app")



def _save_story_theme(char, theme):
    """Reține o temă de poveste preferată (cele mai recente primele, max 8)."""
    theme = (theme or "").strip()
    if not theme:
        return
    themes = [t for t in (char.get("story_themes") or []) if t.lower() != theme.lower()]
    themes.insert(0, theme)
    db.update_character(char["id"], {"story_themes": themes[:8]})


def _remove_story_theme(char, theme):
    """Șterge o temă de poveste salvată."""
    themes = [t for t in (char.get("story_themes") or []) if t.lower() != (theme or "").strip().lower()]
    db.update_character(char["id"], {"story_themes": themes})


def _idle_seconds(conv_id):
    hist = db.get_messages(conv_id)
    if not hist:
        return 0
    try:
        t = datetime.fromisoformat(hist[-1]["created_at"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:  # noqa
        return 0


def _minutes(hhmm, default):
    try:
        h, m = map(int, str(hhmm or default).split(":")[:2])
        return h * 60 + m
    except Exception:  # noqa
        h, m = map(int, default.split(":"))
        return h * 60 + m


def _parse_time(hhmm, default="08:00"):
    from datetime import time as _time
    try:
        h, m = map(int, str(hhmm or default).split(":")[:2])
        return _time(h, m)
    except Exception:  # noqa
        h, m = map(int, default.split(":"))
        return _time(h, m)


def _orthodox_easter(year):
    """Data Paștelui ortodox (calendar gregorian) — algoritmul Meeus (valid 1900–2099)."""
    from datetime import date, timedelta
    a = year % 4
    b = year % 7
    c = year % 19
    d = (19 * c + 15) % 30
    e = (2 * a + 4 * b - d + 34) % 7
    month = (d + e + 114) // 31
    day = ((d + e + 114) % 31) + 1
    return date(year, month, day) + timedelta(days=13)


def _movable_holidays(year):
    """Sărbători cu dată mobilă, calculate față de Paștele ortodox."""
    from datetime import timedelta
    e = _orthodox_easter(year)
    md = lambda dt: dt.strftime("%m-%d")
    return {
        md(e - timedelta(days=7)): "Floriile",
        md(e - timedelta(days=2)): "Vinerea Mare",
        md(e): "Paștele",
        md(e + timedelta(days=1)): "A doua zi de Paște",
        md(e + timedelta(days=49)): "Rusaliile",
    }


@st.fragment(run_every=20)
def proactive_fragment(char_id, conv_id):
    char = db.get_character(char_id)
    if not char:
        return
    fired = False
    now_local = _local_now()
    today = now_local.strftime("%Y-%m-%d")
    now_min = now_local.hour * 60 + now_local.minute
    sched = char.get("schedule") or {}
    tone = sched.get("tone")

    # ⏰ alarmă blândă de trezire — prioritate maximă (merge chiar și cu „nu mă deranja")
    # amânare („încă 5 minute"): dacă ai apăsat snooze, personajul te trezește din nou blând
    _snooze_key = f"alarm_snooze_{conv_id}"
    _snooze_until = st.session_state.get(_snooze_key)
    if _snooze_until and time.time() >= _snooze_until:
        st.session_state.pop(_snooze_key, None)
        if _emit_wakeup(char, conv_id):
            st.session_state[f"alarm_ring_{conv_id}"] = True
            st.session_state["notif_sound"] = True
            st.rerun(scope="app")
        return
    if sched.get("alarm_on"):
        a_days = sched.get("alarm_days")  # gol/None = în fiecare zi
        if (not a_days) or (now_local.weekday() in a_days):
            guard = f"sched_alarm_{conv_id}"
            if st.session_state.get(guard) != today:
                delta = now_min - _minutes(sched.get("alarm"), "07:30")
                if 0 <= delta <= 15:
                    st.session_state[guard] = today
                    if _emit_wakeup(char, conv_id):
                        st.session_state[f"alarm_ring_{conv_id}"] = True
                        st.session_state["notif_sound"] = True
                        st.rerun(scope="app")
                    return
                if delta > 15:
                    st.session_state[guard] = today  # ratată pe azi, marchează ca gata

    if sched.get("dnd"):
        return  # "nu mă deranja" — no automated messages

    # anniversaries / special dates (fire any day of week)
    md = now_local.strftime("%m-%d")
    for ann in (sched.get("anniversaries") or []):
        if ann.get("date") != md:
            continue
        guard = f"sched_ann_{ann.get('date')}_{conv_id}"
        if st.session_state.get(guard) == today:
            continue
        st.session_state[guard] = today
        occ = ann.get("name") or "o zi specială"
        instr = (f"(Astăzi este o zi specială: {occ}. Trimite-i utilizatorului un mesaj cald și "
                 f"personal de felicitare/urare potrivit ocaziei, în stilul tău. Maxim 2 propoziții.)")
        _emit_proactive(char, conv_id, "checkin", tone, custom_instr=instr)
        fired = True
        break

    # birthday — "La mulți ani!"
    if not fired and st.session_state.get("birthday") and st.session_state.get("birthday") == md:
        guard = f"bday_{conv_id}"
        if st.session_state.get(guard) != today:
            st.session_state[guard] = today
            uname = (current_user() or {}).get("name") or st.session_state.get("profile_name") or ""
            instr = (f"(Astăzi este ZIUA DE NAȘTERE a utilizatorului{(' ' + uname) if uname else ''}! "
                     "Trimite-i un mesaj special, cald și plin de bucurie: urează-i „La mulți ani!”, "
                     "spune-i cât de important e pentru tine și fă-i o urare frumoasă. Maxim 3 propoziții.)")
            _emit_proactive(char, conv_id, "checkin", tone, custom_instr=instr)
            fired = True

    # Romanian holidays (fixed + movable, e.g. Paște)
    if not fired and st.session_state.get("holidays_on", True):
        _yr = now_local.year
        if st.session_state.get("_mov_hol_year") != _yr:
            st.session_state["_mov_hol_year"] = _yr
            st.session_state["_mov_hol"] = _movable_holidays(_yr)
        hol = ROMANIAN_HOLIDAYS.get(md) or (st.session_state.get("_mov_hol") or {}).get(md)
        if hol:
            guard = f"hol_{md}_{conv_id}"
            if st.session_state.get(guard) != today:
                st.session_state[guard] = today
                instr = (f"(Astăzi este {hol}! Trimite-i utilizatorului un mesaj special de "
                         "sărbătoare, cald și festiv, potrivit ocaziei, în stilul tău. Maxim 3 propoziții.)")
                _emit_proactive(char, conv_id, "checkin", tone, custom_instr=instr)
                fired = True

    days = sched.get("days")
    day_ok = (not days) or (now_local.weekday() in days)

    # scheduled morning / lunch / evening greetings (only while app is open, on allowed days)
    if day_ok and not fired:
        for slot, kind, default in (
            ("morning", "morning", "08:00"),
            ("lunch", "lunch", "13:00"),
            ("evening", "evening", "22:00"),
        ):
            if not sched.get(f"{slot}_on"):
                continue
            guard = f"sched_{slot}_{conv_id}"
            if st.session_state.get(guard) == today:
                continue
            delta = now_min - _minutes(sched.get(slot), default)
            if 0 <= delta <= 90:
                st.session_state[guard] = today
                _emit_proactive(char, conv_id, kind, tone)
                fired = True
                break
            elif delta > 90:
                st.session_state[guard] = today  # missed window, skip silently

        # custom "momente extra"
        if not fired:
            for hhmm in (sched.get("custom") or []):
                guard = f"sched_custom_{hhmm}_{conv_id}"
                if st.session_state.get(guard) == today:
                    continue
                delta = now_min - _minutes(hhmm, "12:00")
                if 0 <= delta <= 90:
                    st.session_state[guard] = today
                    _emit_proactive(char, conv_id, "checkin", tone)
                    fired = True
                    break
                elif delta > 90:
                    st.session_state[guard] = today

        # evening daily summary
        if not fired and sched.get("summary_on"):
            guard = f"sched_summary_{conv_id}"
            if st.session_state.get(guard) != today:
                delta = now_min - _minutes(sched.get("summary"), "21:00")
                if 0 <= delta <= 90:
                    st.session_state[guard] = today
                    instr = ("(E seară. Fă un scurt rezumat cald și personal al lucrurilor despre care "
                             "ați vorbit azi cu utilizatorul, apoi urează-i noapte bună. Dacă nu ați "
                             "vorbit azi, întreabă-l blând cum a fost ziua. Maxim 3 propoziții.)")
                    _emit_proactive(char, conv_id, "checkin", tone, custom_instr=instr)
                    fired = True
                elif delta > 90:
                    st.session_state[guard] = today

    # idle follow-up (no reply for > chosen minutes)
    if not fired and sched.get("followup_on", True):
        delay = max(1, int(sched.get("followup_min", 1) or 1))
        hist = db.get_messages(conv_id)
        if hist and hist[-1]["role"] == "user":
            st.session_state[f"fu_{conv_id}"] = 0
        elif hist and hist[-1]["role"] == "assistant":
            last = hist[-1]
            try:
                t = datetime.fromisoformat(last["created_at"])
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                idle = (datetime.now(timezone.utc) - t).total_seconds()
            except Exception:  # noqa
                idle = 0
            fu = st.session_state.get(f"fu_{conv_id}", 0)
            if idle >= delay * 60 and fu < 2 and st.session_state.get(f"fu_for_{conv_id}") != last["id"]:
                st.session_state[f"fu_for_{conv_id}"] = last["id"]
                st.session_state[f"fu_{conv_id}"] = fu + 1
                _emit_proactive(char, conv_id, "followup", tone)
                fired = True

    # spontaneous "memory of the day" — recall a past photo/song once a day (after a short idle)
    if not fired and sched.get("recall_on", True) and hasattr(db, "has_media"):
        guard = f"recall_{conv_id}"
        if st.session_state.get(guard) != today and db.has_media(char["id"]):
            hist2 = db.get_messages(conv_id)
            if hist2:
                last2 = hist2[-1]
                try:
                    t2 = datetime.fromisoformat(last2["created_at"])
                    if t2.tzinfo is None:
                        t2 = t2.replace(tzinfo=timezone.utc)
                    idle2 = (datetime.now(timezone.utc) - t2).total_seconds()
                except Exception:  # noqa
                    idle2 = 0
                if idle2 >= 120:
                    st.session_state[guard] = today
                    if _emit_memory_recall(char, conv_id):
                        fired = True

    # "melodia zilei" — each morning the character picks a song from the playlist
    if not fired and sched.get("song_of_day_on", True) and hasattr(db, "random_song"):
        guard = f"songofday_{conv_id}"
        if st.session_state.get(guard) != today and 360 <= now_min <= 660:
            song = db.random_song(char["id"])
            if song and song.get("song_b64"):
                st.session_state[guard] = today
                if _emit_song_of_day(char, conv_id, song):
                    fired = True

    # "noapte bună" — each evening the character sends a calming song from the playlist
    if not fired and sched.get("goodnight_on", True) and hasattr(db, "random_song"):
        guard = f"goodnight_{conv_id}"
        if st.session_state.get(guard) != today and 1200 <= now_min <= 1439:
            song = db.random_song(char["id"])
            if song and song.get("song_b64"):
                st.session_state[guard] = today
                if _emit_goodnight_song(char, conv_id, song):
                    fired = True

    # spontaneous "melodia noastră" — occasionally the character dedicates a song from the playlist
    if not fired and sched.get("dedicate_on", True) and hasattr(db, "random_song"):
        guard = f"dedicate_{conv_id}"
        dkey = f"dedicate_pick_{conv_id}"
        if st.session_state.get(dkey) != today:
            import random as _rnd
            st.session_state[dkey] = today
            st.session_state[f"{dkey}_yes"] = (_rnd.random() < 0.4)
        if st.session_state.get(guard) != today and st.session_state.get(f"{dkey}_yes"):
            song = db.random_song(char["id"])
            if song and song.get("song_b64") and _idle_seconds(conv_id) >= 180:
                st.session_state[guard] = today
                if _emit_song_dedication(char, conv_id):
                    fired = True

    # occasional heartfelt letter — every now and then the character "writes" you a letter
    if not fired and sched.get("letter_on", True):
        guard = f"letter_{conv_id}"
        lkey = f"letter_pick_{conv_id}"
        if st.session_state.get(lkey) != today:
            import random as _rnd
            st.session_state[lkey] = today
            st.session_state[f"{lkey}_yes"] = (_rnd.random() < 0.15)
        if (st.session_state.get(guard) != today and st.session_state.get(f"{lkey}_yes")
                and _idle_seconds(conv_id) >= 180 and len(db.get_messages(conv_id)) >= 4):
            st.session_state[guard] = today
            if _emit_letter(char, conv_id):
                fired = True

    if fired:
        st.session_state["notif_sound"] = True
        st.rerun(scope="app")


@st.fragment(run_every=45)
def absence_fragment():
    """Dacă utilizatorul lipsește de o vreme, personajul cel mai recent îl caută
    („mi-e dor de tine") + notificare în browser. Rulează pe orice pagină."""
    if not st.session_state.get("absence_on"):
        return
    thr = max(1, int(st.session_state.get("absence_min", 15) or 15))
    char = None
    for cid in list(st.session_state.get("recent", [])):
        c = db.get_character(cid)
        if c:
            char = c
            break
    if not char:
        chs = db.list_characters(owner_id=_identity_id())
        char = chs[0] if chs else None
    if not char:
        return
    conv = active_conv_id(char)
    hist = db.get_messages(conv)
    if not hist:
        return
    last = hist[-1]
    try:
        t = datetime.fromisoformat(last["created_at"])
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        idle = (datetime.now(timezone.utc) - t).total_seconds()
    except Exception:  # noqa
        return
    guard = f"absence_for_{conv}"
    if idle >= thr * 60 and st.session_state.get(guard) != last["id"]:
        st.session_state[guard] = last["id"]
        instr = ("(Utilizatorul lipsește de o vreme și nu ați mai vorbit. Trimite-i un mesaj scurt, "
                 "cald și personal prin care îi spui că ți-e dor de el/ea, că te-ai gândit la el/ea "
                 "și că îl/o aștepți cu drag. Maxim 2 propoziții.)")
        if _emit_proactive(char, conv, "checkin", custom_instr=instr):
            st.session_state["notif_sound"] = True
            st.rerun(scope="app")


# ------------------------- sidebar -------------------------
_restore_session()
_restore_theme()
_restore_tz()
_restore_sound()
_restore_notify()
user = current_user()
with st.sidebar:
    st.markdown('<div class="app-logo">🎭 Persona<span class="dot">.</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="app-tag">Personaje AI cu voce clonată</div>', unsafe_allow_html=True)

    if user:
        st.markdown(
            f'<div style="display:flex;align-items:center;gap:.6rem;margin-bottom:.6rem">'
            f'{_user_avatar_html(user, 42)}'
            f'<div style="font-weight:700;font-size:.98rem">{user["name"]}</div></div>',
            unsafe_allow_html=True,
        )
        if st.button("🚪 Ieși din cont (schimbă utilizatorul)", key="sidebar_logout",
                     use_container_width=True, type="primary",
                     help="Te deconectează ca să se poată loga altcineva pe acest telefon"):
            _logout_user()
            st.session_state.nav = "personaje"
            st.rerun()
    elif st.session_state.get("pending_verify_email"):
        _render_verify()
    elif st.session_state.get("pending_reset_email"):
        _render_reset()
    else:
        _render_login_register()


if st.session_state.get("theme_light"):
    st.markdown(LIGHT_CSS, unsafe_allow_html=True)
_cur_theme = "light" if st.session_state.get("theme_light") else "dark"
if st.session_state.get("_theme_cookie_val") != _cur_theme:
    _write_theme_cookie(st.session_state.get("theme_light"))
    st.session_state._theme_cookie_val = _cur_theme


# ------------------------- create / edit form -------------------------
def render_create():
    edit_char = db.get_character(st.session_state.editing_id) if st.session_state.editing_id else None
    voices = cached_voices()
    voice_error = isinstance(voices, dict)
    if voice_error:
        voices = []

    # Seed widgets once when opening the edit form
    if edit_char and st.session_state.get("_seeded_for") != edit_char["id"]:
        st.session_state.cf_name = edit_char["name"]
        st.session_state.cf_avatar = edit_char.get("avatar", "🎭")
        st.session_state.cf_avatar_img = edit_char.get("avatar_image")
        st.session_state.cf_pers = edit_char.get("personality", "")
        st.session_state.cf_scen = edit_char.get("scenario", "")
        st.session_state.cf_amb = edit_char.get("ambiance", "Neutru")
        st.session_state.cf_vis = edit_char.get("visibility", "private")
        st.session_state.cf_mode = "Voce existentă" if edit_char.get("voice_id") else "🗑️ Fără voce (doar text)"
        st.session_state.cf_stab = float(edit_char.get("voice_stability", 0.5))
        st.session_state.cf_sim = float(edit_char.get("voice_similarity", 0.75))
        st.session_state.cf_style = float(edit_char.get("voice_style", 0.0))
        ids = [vid for (vid, _) in voices]
        if edit_char.get("voice_id") in ids:
            st.session_state.cf_voice = ids.index(edit_char["voice_id"])
        st.session_state["_seeded_for"] = edit_char["id"]

    if edit_char:
        st.markdown(
            f'<div class="hero"><h1>Editează <span class="accent">{edit_char["name"]}</span></h1>'
            "<p>Modifică personalitatea, scenariul, vocea și reglajele. Modificările se aplică "
            "imediat conversațiilor viitoare.</p></div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="hero"><h1>Creează un <span class="accent">personaj</span></h1>'
            "<p>Dă-i un nume, o personalitate și un scenariu. Alege o voce existentă sau "
            "clonează una din propriul tău sample audio.</p></div>",
            unsafe_allow_html=True,
        )

    c1, c2 = st.columns([3, 1])
    with c1:
        name = st.text_input("Nume personaj", placeholder="ex. Detectivul Marlow", key="cf_name")
    with c2:
        avatar = st.selectbox("Avatar", EMOJIS, key="cf_avatar")

    personality = st.text_area(
        "Personalitate",
        placeholder="ex. Sarcastic, inteligent, vorbește scurt și direct. Iubește misterele.",
        height=110,
        key="cf_pers",
    )
    scenario = st.text_area(
        "Scenariu / context",
        placeholder="ex. Ești într-un birou de detectivi din anii '40, tocmai ai primit un caz nou.",
        height=110,
        key="cf_scen",
    )
    if st.session_state.get("_pending_amb"):
        st.session_state.cf_amb = st.session_state.pop("_pending_amb")
    ac1, ac2 = st.columns([4, 1])
    with ac1:
        ambiance = st.selectbox(
            "🎨 Ambianță vizuală", list(AMBIANCES.keys()), key="cf_amb",
            help="Atmosfera vizuală a chatului, potrivită scenariului",
        )
    with ac2:
        st.markdown("<div style='height:1.75rem'></div>", unsafe_allow_html=True)
        if st.button("✨ Auto", key="cf_auto_amb", use_container_width=True,
                     help="Alege ambianța automat pe baza scenariului"):
            with st.spinner("Aleg ambianța..."):
                st.session_state["_pending_amb"] = llm.pick_ambiance(
                    st.session_state.get("cf_scen", ""),
                    st.session_state.get("cf_pers", ""),
                    list(AMBIANCES.keys()),
                )
            st.rerun()

    # toate personajele sunt private (aplicație personală, fără galerie publică)
    visibility = "private"

    st.markdown("**🖼️ Portret AI** (opțional)")
    pc1, pc2 = st.columns([2, 3])
    with pc1:
        if st.button("🎨 Generează portret", key="cf_gen_avatar", use_container_width=True,
                     help="Creează un portret pe baza numelui, personalității și scenariului"):
            if not st.session_state.get("cf_name", "").strip():
                st.warning("Dă întâi un nume personajului.")
            else:
                with st.spinner("Pictez portretul... (~15s)"):
                    try:
                        st.session_state.cf_avatar_img = image_gen.generate_avatar(
                            st.session_state.get("cf_name", ""),
                            st.session_state.get("cf_pers", ""),
                            st.session_state.get("cf_scen", ""),
                        )
                    except Exception as e:  # noqa
                        st.error(f"Generarea a eșuat: {e}")
                st.rerun()
        if st.session_state.get("cf_avatar_img") and st.button(
            "❌ Elimină portretul", key="cf_del_avatar", use_container_width=True
        ):
            st.session_state.cf_avatar_img = None
            st.rerun()
    with pc2:
        if st.session_state.get("cf_avatar_img"):
            st.image(base64.b64decode(st.session_state.cf_avatar_img), width=150)

    st.markdown("**Voce**")
    if voice_error:
        st.warning("Nu am putut încărca vocile ElevenLabs.")

    mode = st.radio(
        "Sursă voce",
        ["Voce existentă", "Clonează o voce (upload sample)", "🗑️ Fără voce (doar text)"],
        horizontal=True,
        label_visibility="collapsed",
        key="cf_mode",
    )

    chosen_voice_id = None
    chosen_voice_name = None
    clone_file = None
    clone_name = None

    if mode == "Voce existentă":
        if voices:
            labels = [n for (_, n) in voices]
            idx = st.selectbox(
                "Alege vocea", range(len(labels)), format_func=lambda i: labels[i], key="cf_voice"
            )
            chosen_voice_id, chosen_voice_name = voices[idx]
            if st.button("🔊 Ascultă mostra", key="cf_preview"):
                try:
                    with st.spinner("Generez mostra..."):
                        st.session_state[f"sample_{chosen_voice_id}"] = voice.text_to_speech(
                            "Salut! Așa sună vocea mea. Îmi place tare mult să spun povești. 😊",
                            chosen_voice_id,
                        )
                except Exception as e:  # noqa
                    st.error(f"Nu am putut genera mostra: {e}")
            if st.session_state.get(f"sample_{chosen_voice_id}"):
                st.audio(st.session_state[f"sample_{chosen_voice_id}"], format="audio/mp3")
        else:
            st.info("Nu există voci disponibile momentan.")
    elif mode.startswith("🗑️"):
        st.info("🔇 Personajul va comunica DOAR prin text, fără voce. "
                "Poți adăuga oricând o voce editând personajul.")
    else:
        clone_name = st.text_input(
            "Nume voce clonată", placeholder="ex. Vocea lui Marlow", key="cf_clone_name"
        )
        clone_file = st.file_uploader(
            "Sample audio (mp3/wav, 30s–1min recomandat)",
            type=["mp3", "wav", "m4a", "ogg", "flac"],
            key="cf_clone_file",
        )

    st.session_state.setdefault("cf_stab", 0.5)
    st.session_state.setdefault("cf_sim", 0.75)
    st.session_state.setdefault("cf_style", 0.0)
    with st.expander("🎚️ Reglaje voce (avansat)"):
        stab = st.slider("Stabilitate", 0.0, 1.0, key="cf_stab",
                         help="Mai jos = mai expresiv și variat; mai sus = mai calm și constant")
        sim = st.slider("Similaritate", 0.0, 1.0, key="cf_sim",
                        help="Cât de aproape sună de vocea originală")
        style = st.slider("Stil / exagerare", 0.0, 1.0, key="cf_style",
                          help="Accentuează stilul vocii (0 = neutru)")

    btn_label = "Salvează modificările" if edit_char else "Salvează personajul"
    if st.button(btn_label, type="primary", use_container_width=True, key="cf_submit"):
        if not name.strip():
            st.error("Te rog dă un nume personajului.")
            return

        voice_id, voice_name = chosen_voice_id, chosen_voice_name
        if mode.startswith("🗑️"):
            voice_id, voice_name = None, None
        elif mode.startswith("Clonează"):
            if not clone_file or not (clone_name or "").strip():
                st.error("Pentru clonare ai nevoie de un nume și un fișier audio.")
                return
            try:
                with st.spinner("Clonez vocea cu ElevenLabs..."):
                    voice_id = voice.clone_voice(
                        clone_name.strip(), clone_file.getvalue(), clone_file.name
                    )
                    voice_name = clone_name.strip()
                cached_voices.clear()
            except Exception as e:  # noqa
                st.error(f"Clonarea vocii a eșuat: {e}")
                return

        data = {
            "name": name.strip(),
            "avatar": avatar,
            "avatar_image": st.session_state.get("cf_avatar_img"),
            "personality": personality.strip(),
            "scenario": scenario.strip(),
            "ambiance": ambiance,
            "visibility": visibility,
            "voice_id": voice_id,
            "voice_name": voice_name,
            "voice_stability": stab,
            "voice_similarity": sim,
            "voice_style": style,
        }
        if edit_char:
            db.update_character(edit_char["id"], data)
            st.session_state.active_id = edit_char["id"]
            st.session_state.editing_id = None
            st.session_state.creating = False
            st.session_state.nav = "chat"
            _clear_form()
            st.success("Modificările au fost salvate!")
            st.rerun()

        _u = current_user()
        data["owner_id"] = _identity_id()
        char = db.create_character(data)
        st.session_state.active_id = char["id"]
        st.session_state.creating = False
        st.session_state.nav = "chat"
        _clear_form()
        st.success(f"„{char['name']}” a fost creat!")
        st.rerun()


# ------------------------- chat view -------------------------
def _song_name(filename):
    """Extract a human-friendly song name from an uploaded file name."""
    base = os.path.splitext(os.path.basename(filename or "melodie"))[0]
    name = base.replace("_", " ").replace("-", " – ").strip()
    return name[:80] if name else "melodia mea"


def render_chat(char):
    amb = AMBIANCES.get(char.get("ambiance", "Neutru"), AMBIANCES["Neutru"])
    st.markdown(
        f'<style>.block-container::before{{content:"";position:fixed;top:0;left:0;right:0;'
        f'height:340px;background:radial-gradient(60% 100% at 50% 0%, {amb["glow"]}22, transparent 70%);'
        f'pointer-events:none;z-index:-1;}}</style>',
        unsafe_allow_html=True,
    )

    voice_label = char.get("voice_name") or "fără voce"
    vis_pill = "🌍 Public" if char.get("visibility") == "public" else "🔒 Privat"
    st.markdown(
        f'<div class="char-header" style="background:{amb["grad"]};border-color:{amb["glow"]}44">'
        f'<div class="avatar">{avatar_html(char, size=58, radius=14)}</div>'
        f'<div><div class="name">{char["name"]}</div>'
        f'<div class="meta">{(char.get("personality") or "").strip()[:90] or "personaj"}</div></div>'
        f'<div style="margin-left:auto;display:flex;gap:.4rem;align-items:center">'
        f'<span class="voice-pill">{vis_pill}</span>'
        f'<span class="voice-pill" style="color:{amb["glow"]};'
        f'border-color:{amb["glow"]}55;background:{amb["glow"]}14">🔊 {voice_label}</span></div></div>',
        unsafe_allow_html=True,
    )

    # ---- memento discret: invită vizitatorii să-și facă cont ca să nu piardă personajele ----
    if not current_user():
        mcol = st.columns([5, 2])
        with mcol[0]:
            st.caption("💾 Ești fără cont — personajele se salvează doar pe acest telefon. "
                       "Fă-ți un cont gratuit ca să nu le pierzi.")
        with mcol[1]:
            if st.button("✨ Fă-ți cont", key="chat_save_reminder", use_container_width=True):
                st.session_state["_open_auth_hint"] = True
                st.session_state.nav = "personaje"
                st.rerun()

    # ---- conversation threads ----
    convs = db.list_conversations(char["id"])
    if not convs:
        db.create_conversation(char["id"], "Conversație 1")
        convs = db.list_conversations(char["id"])
    conv_ids = [c["id"] for c in convs]
    conv_key = f"convsel_{char['id']}"
    if st.session_state.get(conv_key) not in conv_ids:
        st.session_state[conv_key] = conv_ids[-1]

    titles = {c["id"]: c["title"] for c in convs}
    cc = st.columns([4, 1, 1, 1])
    with cc[0]:
        active_conv = st.selectbox(
            "Conversație", conv_ids, format_func=lambda cid: titles.get(cid, "Conversație"),
            key=conv_key, label_visibility="collapsed",
        )
    with cc[1]:
        if st.button("➕ Nouă", key="new_conv", use_container_width=True, help="Conversație nouă"):
            new = db.create_conversation(char["id"], f"Conversație {len(convs) + 1}")
            st.session_state[conv_key] = new["id"]
            st.rerun()
    with cc[2]:
        if st.button("✏️ Editează", key="edit_char", use_container_width=True, help="Editează personajul"):
            _clear_form()
            st.session_state.editing_id = char["id"]
            st.session_state.creating = True
            st.session_state.active_id = None
            st.rerun()
    with cc[3]:
        st.download_button(
            "📤 Export",
            data=json.dumps(
                {k: char.get(k) for k in (
                    "name", "avatar", "personality", "scenario", "ambiance", "visibility",
                    "voice_name", "voice_stability", "voice_similarity", "voice_style", "memory",
                )} | {"_persona": True},
                ensure_ascii=False, indent=2,
            ),
            file_name=f"{char['name']}.persona.json",
            mime="application/json",
            key="export_char",
            use_container_width=True,
            help="Exportă personajul ca fișier",
        )

    sub = st.columns([2, 2, 2, 3])
    with sub[0]:
        if st.button("🧹 Golește", key="clear_chat", use_container_width=True):
            cur = db.get_messages(active_conv)
            queue_memory_update(char, cur)
            db.clear_messages(active_conv)
            st.session_state.pop(f"sugg_{active_conv}", None)
            st.rerun()
    with sub[1]:
        if st.button("🗑 Șterge firul", key="del_conv", use_container_width=True,
                     disabled=len(convs) <= 1):
            db.delete_conversation(active_conv)
            st.session_state.pop(conv_key, None)
            st.rerun()
    with sub[2]:
        if st.button("🎨 Portret", key="regen_avatar", use_container_width=True,
                     help="Generează/actualizează portretul AI al personajului"):
            with st.spinner("Pictez portretul... (~15s)"):
                try:
                    img = image_gen.generate_avatar(
                        char["name"], char.get("personality", ""), char.get("scenario", "")
                    )
                    if img:
                        db.update_character(char["id"], {"avatar_image": img})
                except Exception as e:  # noqa
                    st.error(f"Generarea a eșuat: {e}")
            st.rerun()
    with sub[3]:
        if st.button("📞 Sună", key="start_call", use_container_width=True,
                     help="Sună personajul (conversație vocală)"):
            st.session_state.call_char = char["id"]
            st.session_state.call_incoming = False
            st.session_state.call_opened = None
            st.rerun()

    with st.expander("📲 Lasă personajul să te contacteze"):
        pcc = st.columns(2)
        with pcc[0]:
            if st.button("☎️ Cere să te sune", key="req_call", use_container_width=True):
                st.session_state.call_char = char["id"]
                st.session_state.call_incoming = True
                st.session_state.call_opened = None
                st.rerun()
        with pcc[1]:
            if st.button("✉️ Cere-i un mesaj", key="req_text", use_container_width=True):
                with st.spinner(f"{char['name']} îți scrie..."):
                    send_proactive(char, "text")
                st.rerun()

    with st.expander("🔔 Mesaje programate (bună dimineața / prânz / noapte bună)"):
        sched = char.get("schedule") or {}
        dnd = st.toggle("🔕 Nu mă deranja (oprește toate mesajele automate)",
                        value=sched.get("dnd", False), key=f"dnd_{char['id']}")
        voice_on = st.toggle("🔈 Mesajele automate vin și cu voce",
                             value=sched.get("voice_on", False), key=f"voice_on_{char['id']}",
                             help="Necesită o voce setată pentru personaj")
        _zones = ["Automat", "Europe/Bucharest", "Europe/London", "Europe/Paris", "Europe/Madrid",
                  "Europe/Rome", "America/New_York", "America/Los_Angeles", "Asia/Dubai",
                  "Asia/Tokyo", "Australia/Sydney"]
        _cur_tz = st.session_state.get("manual_tz") or "Automat"
        tzsel = st.selectbox("🌍 Fus orar", _zones,
                             index=_zones.index(_cur_tz) if _cur_tz in _zones else 0,
                             key=f"tz_{char['id']}",
                             help="Schimbă manual dacă ora detectată automat nu e corectă")
        _new_tz = "" if tzsel == "Automat" else tzsel
        if _new_tz != st.session_state.get("manual_tz", ""):
            st.session_state.manual_tz = _new_tz
            _write_tz_cookie(_new_tz)
            st.rerun()
        st.caption(f"Ora locală acum: {_local_now().strftime('%H:%M')}")
        st.caption("Personajul îți scrie automat la orele alese, cât timp aplicația e deschisă.")
        m_on = st.toggle("☀️ Mesaj de dimineață", value=sched.get("morning_on", False), key=f"morn_on_{char['id']}")
        m_time = st.time_input("Ora dimineața", value=_parse_time(sched.get("morning"), "08:00"), key=f"morn_t_{char['id']}")
        l_on = st.toggle("🍽️ Mesaj de prânz", value=sched.get("lunch_on", False), key=f"lunch_on_{char['id']}")
        l_time = st.time_input("Ora prânzului", value=_parse_time(sched.get("lunch"), "13:00"), key=f"lunch_t_{char['id']}")
        e_on = st.toggle("🌙 Mesaj de seară", value=sched.get("evening_on", False), key=f"eve_on_{char['id']}")
        e_time = st.time_input("Ora seara", value=_parse_time(sched.get("evening"), "22:00"), key=f"eve_t_{char['id']}")
        s_on = st.toggle("📋 Rezumat de seară (ce ați vorbit azi)", value=sched.get("summary_on", False), key=f"sum_on_{char['id']}")
        s_time = st.time_input("Ora rezumatului", value=_parse_time(sched.get("summary"), "21:00"), key=f"sum_t_{char['id']}")

        st.markdown("**➕ Momente extra**")
        cust_key = f"custom_{char['id']}"
        if cust_key not in st.session_state:
            st.session_state[cust_key] = list(sched.get("custom") or [])
        for idx, tval in enumerate(st.session_state[cust_key]):
            cc = st.columns([4, 1])
            cc[0].markdown(f"🕐 {tval}")
            if cc[1].button("🗑️", key=f"rmcust_{char['id']}_{idx}"):
                st.session_state[cust_key].pop(idx)
                st.rerun()
        ac = st.columns([4, 1])
        new_t = ac[0].time_input("Adaugă oră", value=_parse_time("12:00"),
                                 key=f"addcust_t_{char['id']}", label_visibility="collapsed")
        if ac[1].button("➕", key=f"addcust_{char['id']}"):
            hhmm = new_t.strftime("%H:%M")
            if hhmm not in st.session_state[cust_key]:
                st.session_state[cust_key].append(hhmm)
                st.rerun()

        st.markdown("**🎂 Zile speciale (aniversări)**")
        ann_key = f"anniv_{char['id']}"
        if ann_key not in st.session_state:
            st.session_state[ann_key] = list(sched.get("anniversaries") or [])
        for idx, a in enumerate(st.session_state[ann_key]):
            ac2 = st.columns([4, 1])
            ac2[0].markdown(f"🎉 {a.get('date')} — {a.get('name', '')}")
            if ac2[1].button("🗑️", key=f"rmann_{char['id']}_{idx}"):
                st.session_state[ann_key].pop(idx)
                st.rerun()
        anc = st.columns([2, 3, 1])
        ann_date = anc[0].date_input("Data", key=f"annd_{char['id']}", label_visibility="collapsed")
        ann_name = anc[1].text_input("Ocazie (ex: ziua mea)", key=f"annn_{char['id']}", label_visibility="collapsed",
                                     placeholder="ocazie (ex: ziua mea)")
        if anc[2].button("➕", key=f"addann_{char['id']}"):
            entry = {"date": ann_date.strftime("%m-%d"), "name": ann_name.strip() or "o zi specială"}
            if entry not in st.session_state[ann_key]:
                st.session_state[ann_key].append(entry)
                st.rerun()

        _days = ["Lun", "Mar", "Mie", "Joi", "Vin", "Sâm", "Dum"]
        cur_days = sched.get("days")
        default_days = [_days[i] for i in cur_days] if cur_days else _days
        sel_days = st.multiselect("📅 În ce zile", _days, default=default_days, key=f"days_{char['id']}")
        _tones = ["Normal", "Tandru", "Jucăuș", "Motivațional"]
        tone = st.selectbox("🎭 Tonul mesajelor automate", _tones,
                            index=_tones.index(sched.get("tone")) if sched.get("tone") in _tones else 0,
                            key=f"tone_{char['id']}")
        _themes = ["Implicit", "iPhone", "Samsung"]
        cur_theme = char.get("notif_theme") or "Implicit"
        notif_theme = st.selectbox("🔊 Sunet de notificare pentru acest personaj", _themes,
                                   index=_themes.index(cur_theme) if cur_theme in _themes else 0,
                                   key=f"ntheme_{char['id']}")
        st.markdown("---")
        fu_on = st.toggle("⏰ Îmi scrie dacă tac o vreme", value=sched.get("followup_on", True),
                          key=f"fu_on_{char['id']}")
        fu_min = st.slider("După câte minute de tăcere", 1, 30, int(sched.get("followup_min", 1) or 1),
                           key=f"fu_min_{char['id']}")
        ded_on = st.toggle("💝 «Melodia noastră» — din când în când îmi dedică o melodie din playlist",
                           value=sched.get("dedicate_on", True), key=f"ded_on_{char['id']}")
        sod_on = st.toggle("🌅 «Melodia zilei» — în fiecare dimineață îmi alege o melodie din playlist",
                           value=sched.get("song_of_day_on", True), key=f"sod_on_{char['id']}")
        gn_on = st.toggle("🌙 «Noapte bună» — seara îmi trimite o melodie liniștitoare din playlist",
                          value=sched.get("goodnight_on", True), key=f"gn_on_{char['id']}")
        letter_on = st.toggle("💌 «Scrisoare» — din când în când îmi scrie o scrisoare caldă (o pot asculta)",
                              value=sched.get("letter_on", True), key=f"letter_on_{char['id']}")
        if st.button("💾 Salvează programul", key=f"save_sched_{char['id']}", use_container_width=True):
            db.update_character(char["id"], {
                "notif_theme": None if notif_theme == "Implicit" else notif_theme,
                "schedule": {
                    "dnd": dnd,
                    "voice_on": voice_on,
                    "morning_on": m_on, "morning": m_time.strftime("%H:%M"),
                    "lunch_on": l_on, "lunch": l_time.strftime("%H:%M"),
                    "evening_on": e_on, "evening": e_time.strftime("%H:%M"),
                    "summary_on": s_on, "summary": s_time.strftime("%H:%M"),
                    "custom": list(st.session_state[cust_key]),
                    "anniversaries": list(st.session_state[ann_key]),
                    "days": sorted(_days.index(d) for d in sel_days),
                    "tone": tone if tone != "Normal" else None,
                    "followup_on": fu_on, "followup_min": fu_min,
                    "dedicate_on": ded_on,
                    "song_of_day_on": sod_on,
                    "goodnight_on": gn_on,
                    "letter_on": letter_on,
                },
            })
            st.success("Program salvat! Personajul îți va scrie la orele alese.")
            st.rerun()

    with st.expander("✏️ Redenumește conversația"):
        new_title = st.text_input("Titlu", value=titles.get(active_conv, ""), key=f"rename_{active_conv}")
        rcols = st.columns(2)
        if rcols[0].button("Salvează titlul", key="save_title", use_container_width=True):
            db.rename_conversation(active_conv, (new_title.strip() or "Conversație"))
            st.rerun()
        rcols[1].download_button(
            "📄 Descarcă (.txt)",
            data=_conversation_text(char, active_conv),
            file_name=f"{char['name']}-conversatie.txt",
            mime="text/plain",
            key=f"dl_conv_{active_conv}",
            use_container_width=True,
        )

    with st.expander("🧠 Memorie de lungă durată"):
        st.caption("Ce își amintește personajul despre tine (poți edita liber).")
        mem_now = char.get("memory", "")
        mk = f"memedit_{char['id']}"
        seen = f"memseen_{char['id']}"
        if st.session_state.get(seen) != mem_now:
            st.session_state[mk] = mem_now
            st.session_state[seen] = mem_now
        mem_txt = st.text_area(
            "Memorie", key=mk, height=150, label_visibility="collapsed",
            placeholder="Încă nimic memorat. Vorbește cu personajul sau scrie tu detalii aici.",
        )
        mc = st.columns([2, 2, 5])
        with mc[0]:
            if st.button("💾 Salvează", key="save_mem", use_container_width=True):
                db.update_character(char["id"], {"memory": mem_txt.strip()})
                st.session_state[seen] = mem_txt.strip()
                st.toast("Memorie salvată")
                st.rerun()
        with mc[1]:
            if st.button("🗑 Șterge", key="clear_mem", use_container_width=True):
                db.update_character(char["id"], {"memory": ""})
                st.rerun()

    tts_kwargs = dict(
        stability=char.get("voice_stability", 0.5),
        similarity_boost=char.get("voice_similarity", 0.75),
        style=char.get("voice_style", 0.0),
    )
    # 🎭 dispoziția de azi a personajului (o alege o dată pe zi) — influențează răspunsurile
    char["_mood_today"] = _char_mood(char)
    # 🎨 tonul vocii salvat pe personaj (șoaptă / somn / veselă...) — se ține minte
    _tone = char.get("voice_tone") or "Normal"
    if _tone and _tone != "Normal":
        tts_kwargs["tone"] = _tone

    history = db.get_messages(active_conv)
    if st.session_state.pop("notif_sound", False):
        play_sound("notification", char.get("notif_theme"))
    if not history:
        with st.spinner(f"{char['name']} intră în scenă..."):
            try:
                greeting = llm.get_reply(
                    char, [],
                    "(Utilizatorul tocmai a deschis conversația. Salută-l scurt și cald, "
                    "în personaj, și invită-l să înceapă discuția. Maxim 2 propoziții.)",
                    tries=1,
                )
            except Exception:  # noqa
                greeting = None
        if greeting:
            db.add_message(active_conv, "assistant", greeting)
            st.rerun()

    for m in history:
        with st.chat_message(m["role"], avatar=char.get("avatar", "🎭") if m["role"] == "assistant" else "🧑"):
            if m["role"] == "user" and m.get("audio_b64"):
                st.caption("🎤 Mesaj vocal")
                st.audio(base64.b64decode(m["audio_b64"]), format="audio/wav")
            if m.get("media_kind") == "song" and m.get("song_b64"):
                st.caption(f"🎵 {m.get('song_name', 'melodie')}")
                st.audio(base64.b64decode(m["song_b64"]), format="audio/mp3")
            if m.get("media_kind") == "photo" and m.get("image_b64"):
                st.image(base64.b64decode(m["image_b64"]), width=280)
            if m.get("media_kind") == "video" and m.get("video_b64"):
                st.video(base64.b64decode(m["video_b64"]))
            st.markdown(m["content"])
            if m["role"] == "assistant" and (
                    m["content"].startswith("💌 O scrisoare pentru tine:")
                    or m["content"].startswith("🌙 Poveste de noapte:")):
                _isletter = m["content"].startswith("💌")
                _knd = "scrisoarea" if _isletter else "povestea"
                _fnk = "scrisoare" if _isletter else "poveste"
                st.download_button(
                    f"⬇️ Salvează {_knd} (.txt)",
                    data=m["content"],
                    file_name=f"{_fnk}-{char['name']}-{(str(m.get('created_at') or ''))[:10]}.txt",
                    mime="text/plain",
                    key=f"dlmsg_{m['id']}",
                )
            _mid = m["id"]
            _react = m.get("reaction")
            rcols = st.columns([7, 1])
            with rcols[1]:
                with st.popover(_react or "🙂", use_container_width=True):
                    _emos = ["❤️", "😂", "👍", "😮", "😢", "🙏"]
                    _ec = st.columns(len(_emos))
                    for _j, _e in enumerate(_emos):
                        if _ec[_j].button(_e, key=f"react_{_mid}_{_e}", use_container_width=True):
                            db.set_reaction(_mid, "" if _react == _e else _e)
                            st.rerun()
            if m["role"] == "assistant" and char.get("voice_id"):
                mid = m["id"]
                if st.button("🔊 Ascultă", key=f"tts_{mid}"):
                    try:
                        with st.spinner("Generez vocea..."):
                            st.session_state[f"audio_{mid}"] = voice.text_to_speech(
                                m["content"], char["voice_id"], **tts_kwargs
                            )
                        st.session_state["autoplay_mid"] = mid
                    except Exception as e:  # noqa
                        st.error(f"Redarea vocii a eșuat: {e}")
                if st.session_state.get(f"audio_{mid}"):
                    if st.session_state.get("autoplay_mid") == mid:
                        # redare fiabilă pe telefon + fundal ambiental sub voce (dacă există)
                        _play_voice_ambient(
                            [st.session_state[f"audio_{mid}"]],
                            st.session_state.get(f"sfx_{mid}"),
                            mid,
                            voice_vol=st.session_state.get(f"sleepvvol_{mid}", 1.0),
                        )
                        st.session_state["autoplay_mid"] = None
                    st.audio(st.session_state[f"audio_{mid}"], format="audio/mp3")
                    st.download_button(
                        "⬇️ Descarcă MP3",
                        data=st.session_state[f"audio_{mid}"],
                        file_name=f"{char['name']}_{mid[:6]}.mp3",
                        mime="audio/mpeg",
                        key=f"dl_{mid}",
                    )
            if m["role"] == "assistant" and st.session_state.get(f"sfx_{m['id']}"):
                cue = st.session_state.get(f"sfxcue_{m['id']}", "ambianță")
                st.caption(f"🎧 {cue}")
                ap = st.session_state.get("ambient_play_mid") == m["id"]
                st.audio(st.session_state[f"sfx_{m['id']}"], format="audio/mp3", autoplay=ap)
                if ap:
                    st.session_state["ambient_play_mid"] = None

    # 🔄 „Încearcă din nou" — dacă ultimul mesaj e al utilizatorului și n-a primit răspuns
    # (generarea a eșuat), oferă un buton care regenerează răspunsul, fără a rescrie mesajul.
    if history and history[-1]["role"] == "user":
        st.info("⚠️ Nu am reușit să răspund la ultimul mesaj. Apasă mai jos ca să încerc din nou.")
        if st.button("🔄 Încearcă din nou", key=f"regen_{active_conv}",
                     use_container_width=True, type="primary"):
            _last = history[-1]
            _hist = history[:-1]
            _prompt = _last.get("content") or ""
            _wi = ""
            if st.session_state.get("web_search"):
                try:
                    _wi = llm.web_lookup(_prompt)
                except Exception:  # noqa
                    _wi = ""
            with st.spinner(f"{char['name']} încearcă din nou..."):
                try:
                    _parts = llm.burst_reply(char, _hist, _prompt, web_info=_wi)
                except Exception:  # noqa
                    _log.exception("burst_reply failed (retry button)")
                    _parts = []
            if not _parts:
                st.error("Tot nu a mers. Mai încearcă în câteva secunde. 💛")
            else:
                _reply = " ".join(_parts)
                _new = [db.add_message(active_conv, "assistant", p) for p in _parts]
                st.session_state["notif_sound"] = True
                if st.session_state.get("ambient_fx"):
                    maybe_ambient(char, _new[0]["id"], _reply)
                if st.session_state.get("auto_play") and char.get("voice_id"):
                    for _m in _new:
                        try:
                            st.session_state[f"audio_{_m['id']}"] = voice.text_to_speech(
                                _m["content"], char["voice_id"], **tts_kwargs)
                        except Exception:  # noqa
                            pass
                    st.session_state["autoplay_burst"] = {
                        "ids": [_m["id"] for _m in _new],
                        "uid": "burst_" + _new[-1]["id"][:8],
                    }
                st.rerun()

    # redare în rafală: mai multe voci una după alta, cu fundal ambiental continuu sub ele
    _burst = st.session_state.pop("autoplay_burst", None)
    if _burst and char.get("voice_id"):
        _voices = [st.session_state.get(f"audio_{i}") for i in _burst["ids"]]
        _amb = st.session_state.get(f"sfx_{_burst['ids'][0]}")
        _play_voice_ambient(_voices, _amb, _burst["uid"])

    # voce amânată pentru mesajele lungi (poveste/scrisoare): textul apare imediat, vocea vine după
    _gv = st.session_state.pop("_gen_voice_for", None)
    if _gv and char.get("voice_id") and not st.session_state.get(f"audio_{_gv}"):
        _m = next((x for x in history if x["id"] == _gv), None)
        if _m:
            with st.spinner(f"{char['name']} își pregătește vocea... (poți începe să citești)"):
                try:
                    st.session_state[f"audio_{_gv}"] = voice.text_to_speech(
                        _m["content"], char["voice_id"], **tts_kwargs)
                    st.session_state["autoplay_mid"] = _gv
                except Exception:  # noqa
                    pass
            st.rerun()

    prompt = st.chat_input(f"Scrie-i lui {char['name']}...")
    proactive_fragment(char["id"], active_conv)
    st.caption("💬 Fără limită de cuvinte — vorbește oricât vrei, despre orice, chiar și despre durerile și necazurile tale. Personajul e mereu aici pentru tine.")

    # 🎭 dispoziția de azi + 🎨 tonul vocii + 🗓️ recap + 😴 adoarme cu mine
    st.caption(f"🎭 Azi {char['name']} e {char.get('_mood_today', '')}.")
    with st.expander("🎨 Vocea, 🗓️ ce am vorbit & 😴 adormit"):
        _tone_opts = ["Normal", "Voce blândă", "Șoaptă", "Voce de somn", "Voce veselă"]
        _cur_tone = char.get("voice_tone") or "Normal"
        _sel_tone = st.selectbox(
            "🎨 Tonul vocii",
            _tone_opts,
            index=_tone_opts.index(_cur_tone) if _cur_tone in _tone_opts else 0,
            key=f"tone_sel_{char['id']}",
            help="Cum sună vocea personajului: normal, blând, în șoaptă, somnoros sau vesel. "
                 "Se ține minte pentru acest personaj.",
        )
        if _sel_tone != _cur_tone:
            db.update_character(char["id"], {"voice_tone": _sel_tone})
            st.rerun()
        # 🔊 probă la ton — auzi o mostră scurtă cu vocea în tonul ales
        if char.get("voice_id"):
            if st.button("🔊 Ascultă proba de ton", key=f"tone_prev_btn_{char['id']}",
                         use_container_width=True,
                         help="Auzi o mostră scurtă cu vocea în tonul ales, înainte s-o folosești"):
                with st.spinner("Generez o mostră..."):
                    try:
                        st.session_state[f"toneprev_{char['id']}"] = voice.text_to_speech(
                            f"Bună, sunt {char['name']}. Așa sună vocea mea acum. 😊",
                            char["voice_id"], tone=char.get("voice_tone"), **_tts_kwargs(char))
                        st.session_state[f"toneprev_play_{char['id']}"] = True
                    except Exception:  # noqa
                        st.warning("Nu am putut genera mostra acum.")
            _tp = st.session_state.get(f"toneprev_{char['id']}")
            if _tp:
                st.audio(_tp, format="audio/mp3")
                if st.session_state.pop(f"toneprev_play_{char['id']}", False):
                    _play_voice_ambient([_tp], None, "toneprev_" + char["id"][:6])

        if len(history) >= 2:
            if st.button("🗓️ Ce am vorbit data trecută", key=f"recap_{active_conv}",
                         use_container_width=True):
                with st.spinner(f"{char['name']} își amintește..."):
                    _ok = _emit_recap(char, active_conv)
                if _ok:
                    st.session_state["notif_sound"] = True
                    st.rerun()
                else:
                    st.info("Nu am putut face rezumatul acum. Mai încearcă puțin mai târziu.")
            if st.button("📔 Jurnalul zilei", key=f"journal_{active_conv}",
                         use_container_width=True,
                         help="Personajul scrie o reflecție caldă, cu voce, despre ziua voastră"):
                with st.spinner(f"{char['name']} scrie în jurnal..."):
                    _jok = _emit_journal(char, active_conv)
                if _jok:
                    st.session_state["notif_sound"] = True
                    st.rerun()
                else:
                    st.info("Nu am putut scrie jurnalul acum. Mai încearcă puțin mai târziu.")

        if not st.session_state.get("sleep_mode"):
            _sleep_opts = [3, 5, 8, 10, 15, 20, 30, 45]
            _cur_min = char.get("sleep_minutes") or st.session_state.get("sleep_minutes", 8)
            _mins = st.selectbox(
                "⏱️ Se oprește singur după (minute)",
                _sleep_opts,
                index=_sleep_opts.index(_cur_min) if _cur_min in _sleep_opts else 2,
                key=f"sleep_min_{active_conv}",
                help="După câte minute se oprește singur modul „Adoarme cu mine”. Se ține minte "
                     "pentru acest personaj.",
            )
            st.session_state["sleep_minutes"] = _mins
            if _mins != (char.get("sleep_minutes") or 8):
                db.update_character(char["id"], {"sleep_minutes": _mins})
            if st.button("😴 Adoarme cu mine", key=f"sleep_start_{active_conv}",
                         use_container_width=True):
                st.session_state["sleep_mode"] = {
                    "conv": active_conv, "step": 0,
                    "total": max(2, round(_mins * 60 / 40)), "last": 0,
                }
                st.rerun()
            st.caption(f"{char['name']} îți vorbește tot mai blând, cu fundal liniștitor, "
                       f"până adormi (se oprește singur după ~{_mins} min).")
        else:
            st.caption("😴 Modul de somn e pornit — butonul mare de oprire e mai jos.")

        # ⏰ alarmă blândă de trezire — personajul te trezește dimineața cu vocea lui
        st.markdown("---")
        _asched = char.get("schedule") or {}
        _alarm_on = st.toggle(
            "⏰ Alarmă blândă de trezire",
            value=_asched.get("alarm_on", False),
            key=f"alarm_on_{char['id']}",
            help="Personajul te trezește dimineața cu vocea lui, la ora aleasă "
                 "(funcționează cât timp aplicația e deschisă).",
        )
        _alarm_t = st.time_input(
            "Ora de trezire",
            value=_parse_time(_asched.get("alarm"), "07:30"),
            key=f"alarm_t_{char['id']}",
        )
        _new_alarm = _alarm_t.strftime("%H:%M")
        _adow = ["Lun", "Mar", "Mie", "Joi", "Vin", "Sâm", "Dum"]
        _cur_adays = _asched.get("alarm_days")
        _def_adays = [_adow[i] for i in _cur_adays] if _cur_adays else _adow
        _sel_adays = st.multiselect(
            "📅 În ce zile să sune alarma",
            _adow, default=_def_adays, key=f"alarm_days_{char['id']}",
            help="Alege zilele (ex: doar Lun–Vin, fără weekend). Gol sau toate = în fiecare zi.",
        )
        # gol sau toate cele 7 zile -> stocăm listă goală („în fiecare zi")
        _stored_adays = [] if (not _sel_adays or len(_sel_adays) == 7) else sorted(_adow.index(d) for d in _sel_adays)
        _snz_opts = [5, 10, 15]
        _cur_snz = int(_asched.get("snooze_min", 5) or 5)
        _sel_snz = st.selectbox(
            "😴 Amânare (când apeși «Încă X minute»)",
            _snz_opts,
            index=_snz_opts.index(_cur_snz) if _cur_snz in _snz_opts else 0,
            format_func=lambda m: f"{m} minute",
            key=f"alarm_snz_{char['id']}",
            help="Cât să te mai las să dormi când apeși pe amânare.",
        )
        if (_alarm_on != _asched.get("alarm_on", False)
                or _new_alarm != (_asched.get("alarm") or "07:30")
                or _stored_adays != (_asched.get("alarm_days") or [])
                or _sel_snz != _cur_snz):
            _merged = dict(_asched)
            _merged["alarm_on"] = _alarm_on
            _merged["alarm"] = _new_alarm
            _merged["alarm_days"] = _stored_adays
            _merged["snooze_min"] = _sel_snz
            db.update_character(char["id"], {"schedule": _merged})
        if _alarm_on:
            _when = ("în zilele: " + ", ".join(_adow[i] for i in _stored_adays)) if _stored_adays else "în fiecare zi"
            st.caption(f"⏰ Te voi trezi blând la {_new_alarm}, cu vocea mea, {_when} "
                       f"(cât timp aplicația e deschisă). Poți amâna cu «😴 Încă {_sel_snz} minute».")

    # ⏰ alarmă activă: butoane „încă X minute" (amânare) / „m-am trezit"
    if st.session_state.get(f"alarm_ring_{active_conv}"):
        _snz_min = int((char.get("schedule") or {}).get("snooze_min", 5) or 5)
        st.info("⏰ Bună dimineața! Te-am trezit cu drag. Vrei să te mai las puțin?")
        _acols = st.columns(2)
        if _acols[0].button(f"😴 Încă {_snz_min} minute", key=f"alarm_snooze_btn_{active_conv}",
                            use_container_width=True):
            st.session_state[f"alarm_snooze_{active_conv}"] = time.time() + _snz_min * 60
            st.session_state.pop(f"alarm_ring_{active_conv}", None)
            st.toast(f"😴 Bine, te mai trezesc în {_snz_min} minute.")
            st.rerun()
        if _acols[1].button("☀️ M-am trezit", key=f"alarm_wake_btn_{active_conv}",
                            use_container_width=True, type="primary"):
            st.session_state.pop(f"alarm_ring_{active_conv}", None)
            st.session_state.pop(f"alarm_snooze_{active_conv}", None)
            st.rerun()

    # buton de OPRIRE mereu vizibil (în afara expanderului) cât timp «Adoarme cu mine» e activ
    if st.session_state.get("sleep_mode"):
        st.info("😴 Modul „Adoarme cu mine” e pornit — ascultă și lasă-te purtat spre somn.")
        if st.button("🛑 Oprește (m-am trezit)", key=f"sleep_stop_{active_conv}",
                     use_container_width=True, type="primary"):
            st.session_state.pop("sleep_mode", None)
            st.rerun()

    # rulează „Adoarme cu mine" (șoapte periodice, tot mai încet)
    sleep_fragment(char["id"], active_conv)

    # ---- talk instead of type (hands-free voice) ----
    st.markdown(f"### 🎤 Vorbește cu {char['name']} (în loc să scrii)")
    _cont = st.toggle(
        "🔄 Conversație continuă (mâini libere)",
        value=st.session_state.get(f"cont_{char['id']}", False),
        key=f"cont_toggle_{char['id']}",
        help="După ce personajul termină de vorbit, microfonul e gata imediat — apeși o dată "
             "și continui să vorbești, fără să scrii.",
    )
    st.session_state[f"cont_{char['id']}"] = _cont
    if _cont:
        st.caption("🎤 E rândul tău — apasă microfonul și spune mai departe. "
                   f"{char['name']} îți răspunde imediat cu vocea și microfonul rămâne pregătit.")
    if char.get("voice_id"):
        st.caption("📱 Apasă microfonul, spune ce vrei, apoi oprește înregistrarea. "
                   f"{char['name']} îți răspunde imediat și vei auzi răspunsul cu vocea lui — "
                   "nu trebuie să apeși niciun buton de redare. Prima dată telefonul îți cere "
                   "permisiunea pentru microfon — apasă „Permite”.")
    else:
        st.caption("📱 Apasă microfonul, spune ce vrei, apoi oprește înregistrarea și "
                   f"{char['name']} îți răspunde imediat. Prima dată telefonul îți cere "
                   "permisiunea pentru microfon — apasă „Permite”.")
    vm = st.audio_input("Mesaj vocal", label_visibility="collapsed", key=f"vm_{active_conv}")
    if vm is not None:
        vdata = vm.getvalue()
        if vdata:
            vh = hashlib.md5(vdata).hexdigest()
            if st.session_state.get(f"vmrec_{active_conv}") != vh:
                st.session_state[f"vmrec_{active_conv}"] = vh
                with st.spinner("Transcriu mesajul vocal..."):
                    try:
                        vtext = stt.transcribe(vdata, "audio.wav")
                    except Exception as e:  # noqa
                        vtext = ""
                        st.error(f"Nu am putut transcrie: {e}")
                if vtext:
                    st.session_state.pending_voice = {
                        "text": vtext,
                        "audio": base64.b64encode(vdata).decode(),
                    }
                    st.rerun()

    # ---- send a favorite song ----
    with st.expander("🎵 Trimite o melodie"):
        st.caption(f"Încarcă una sau mai multe melodii preferate și {char['name']} îți spune "
                   "părerea despre ele.")
        songs_up = st.file_uploader("Melodie", type=["mp3", "m4a", "wav", "ogg"],
                                    label_visibility="collapsed", accept_multiple_files=True,
                                    key=f"song_{active_conv}")
        if songs_up:
            done = st.session_state.setdefault(f"songset_{active_conv}", set())
            new_names = []
            for song in songs_up:
                sdata = song.getvalue()
                if not sdata:
                    continue
                sh = hashlib.md5(sdata).hexdigest()
                if sh in done:
                    continue
                done.add(sh)
                sname = _song_name(song.name)
                sb64 = base64.b64encode(sdata).decode() if len(sdata) <= 1_400_000 else None
                extra = {"media_kind": "song", "song_name": sname}
                if sb64:
                    extra["song_b64"] = sb64
                db.add_message(active_conv, "user",
                               f"🎵 Ți-am trimis melodia: **{sname}**", extra=extra)
                new_names.append(sname)
            if new_names:
                spin = "melodia" if len(new_names) == 1 else f"cele {len(new_names)} melodii"
                with st.spinner(f"{char['name']} ascultă {spin}..."):
                    try:
                        if len(new_names) == 1:
                            scomment = llm.comment_on_song(char, history, new_names[0])
                        else:
                            scomment = llm.comment_on_songs(char, history, new_names)
                    except Exception:  # noqa
                        scomment = None
                if scomment:
                    amsg = db.add_message(active_conv, "assistant", scomment)
                    st.session_state["notif_sound"] = True
                    if st.session_state.get("auto_play") and char.get("voice_id"):
                        try:
                            st.session_state[f"audio_{amsg['id']}"] = voice.text_to_speech(
                                scomment, char["voice_id"], **tts_kwargs)
                            st.session_state["autoplay_mid"] = amsg["id"]
                        except Exception:  # noqa
                            pass
                st.rerun()
        if st.button("🎧 Recomandă-mi melodii noi", key=f"recsong_{active_conv}",
                     use_container_width=True):
            names = db.list_song_names(char["id"])
            with st.spinner(f"{char['name']} alege melodii pentru tine..."):
                try:
                    rec = llm.recommend_songs(char, history, names)
                except Exception:  # noqa
                    rec = None
            if rec:
                amsg = db.add_message(active_conv, "assistant", "🎧 Recomandările mele:\n\n" + rec)
                st.session_state["notif_sound"] = True
                if char.get("voice_id"):
                    try:
                        st.session_state[f"audio_{amsg['id']}"] = voice.text_to_speech(
                            rec, char["voice_id"], **tts_kwargs)
                        st.session_state["autoplay_mid"] = amsg["id"]
                    except Exception:  # noqa
                        pass
                st.rerun()

    # ---- our shared playlist ----
    if hasattr(db, "list_songs") and db.list_songs(char["id"]):
        with st.expander("🎵 Playlist-ul nostru"):
            st.caption(f"Toate melodiile pe care i le-ai trimis lui {char['name']} — "
                       "ascultă-le oricând, una după alta.")
            _render_playlist(char, key_prefix="chat")

    # ---- send a photo or video (gallery) ----
    with st.expander("📷 Trimite o poză sau un videoclip"):
        st.caption(f"Trimite o poză și {char['name']} se uită și îți spune părerea. "
                   "Poți trimite și un videoclip scurt.")
        media = st.file_uploader(
            "Poză sau video",
            type=["jpg", "jpeg", "png", "webp", "heic", "mp4", "mov", "webm"],
            label_visibility="collapsed", key=f"media_{active_conv}",
        )
        if media is not None:
            mdata = media.getvalue()
            if mdata:
                mh = hashlib.md5(mdata).hexdigest()
                if st.session_state.get(f"mediarec_{active_conv}") != mh:
                    st.session_state[f"mediarec_{active_conv}"] = mh
                    ext = os.path.splitext(media.name or "")[1].lower()
                    is_video = ext in (".mp4", ".mov", ".webm") or (media.type or "").startswith("video")
                    react = None
                    if is_video:
                        vb64 = base64.b64encode(mdata).decode() if len(mdata) <= 4_000_000 else None
                        extra = {"media_kind": "video"}
                        if vb64:
                            extra["video_b64"] = vb64
                        note = "🎬 Ți-am trimis un videoclip." if vb64 else \
                            "🎬 Ți-am trimis un videoclip (prea mare ca să-l salvez, dar l-am arătat)."
                        db.add_message(active_conv, "user", note, extra=extra)
                        with st.spinner(f"{char['name']} reacționează..."):
                            try:
                                react = llm.get_reply(
                                    char, history,
                                    "(Utilizatorul ți-a trimis un videoclip cu el/din viața lui. Nu poți "
                                    "vedea conținutul, dar reacționează cald și curios în personaj: bucură-te "
                                    "că ți-a trimis ceva și întreabă-l ce e în video. Maxim 2 propoziții.)",
                                )
                            except Exception:  # noqa
                                react = None
                    else:
                        try:
                            img = _compress_photo(mdata)
                        except Exception:  # noqa
                            img = mdata
                        ib64 = base64.b64encode(img).decode()
                        db.add_message(active_conv, "user", "📷 Ți-am trimis o poză.",
                                       extra={"media_kind": "photo", "image_b64": ib64})
                        with st.spinner(f"{char['name']} se uită la poză..."):
                            try:
                                react = llm.comment_on_photo(char, history, ib64, "image/jpeg")
                            except Exception:  # noqa
                                react = None
                    if react:
                        amsg = db.add_message(active_conv, "assistant", react)
                        st.session_state["notif_sound"] = True
                        if char.get("voice_id"):
                            try:
                                st.session_state[f"audio_{amsg['id']}"] = voice.text_to_speech(
                                    react, char["voice_id"], **tts_kwargs)
                                st.session_state["autoplay_mid"] = amsg["id"]
                            except Exception:  # noqa
                                pass
                    st.rerun()

    # ---- on-demand memory recall ----
    if hasattr(db, "has_media") and db.has_media(char["id"]):
        if st.button("💭 Amintește-ți de o poză sau melodie", key=f"recall_btn_{active_conv}",
                     use_container_width=True):
            if _emit_memory_recall(char, active_conv):
                st.session_state["notif_sound"] = True
            st.rerun()

    # ---- heartfelt letter (listen with the character's voice) ----
    with st.expander("💌 Scrisoare"):
        st.caption(f"{char['name']} îți poate «scrie» o scrisoare din suflet — "
                   "o auzi cu vocea lui, fără să apeși play.")
        if st.button("💌 Scrie-mi o scrisoare", key=f"letter_btn_{active_conv}",
                     use_container_width=True):
            with st.spinner(f"{char['name']} îți scrie o scrisoare..."):
                _ok = _emit_letter(char, active_conv)
            if _ok:
                st.session_state["notif_sound"] = True
                st.rerun()
            else:
                st.info("Nu am putut scrie scrisoarea acum. Mai încearcă puțin mai târziu.")

    # ---- smart contextual suggestions ----
    suggestions = []
    if not prompt and history and history[-1]["role"] == "assistant":
        sig = len(history)
        if st.session_state.get(f"suggn_{active_conv}") != sig:
            with st.spinner("..."):
                st.session_state[f"sugg_{active_conv}"] = llm.suggest_replies(char, history)
            st.session_state[f"suggn_{active_conv}"] = sig
        suggestions = st.session_state.get(f"sugg_{active_conv}", [])

    if suggestions:
        st.caption("💬 Sugestii:")
        scols = st.columns(len(suggestions))
        for i, s in enumerate(suggestions):
            if scols[i].button(s, key=f"sug_{active_conv}_{i}", use_container_width=True):
                st.session_state.pending_prompt = s
                st.rerun()

    user_audio = None
    if not prompt and st.session_state.get("pending_voice"):
        vmsg = st.session_state.pop("pending_voice")
        prompt = vmsg["text"]
        user_audio = vmsg["audio"]
    if not prompt and st.session_state.get("pending_prompt"):
        prompt = st.session_state.pop("pending_prompt")

    if prompt:
        # persistă IMEDIAT mesajul utilizatorului (înainte de apelul LLM) ca să NU se piardă
        # la o deconectare / eroare temporară; astfel istoricul crește sigur.
        db.add_message(active_conv, "user", prompt, audio_b64=user_audio)
        cur = titles.get(active_conv, "")
        if cur.startswith("Conversație"):
            db.rename_conversation(active_conv, prompt.strip()[:32])
        with st.chat_message("user", avatar="🧑"):
            if user_audio:
                st.caption("🎤 Mesaj vocal")
                st.audio(base64.b64decode(user_audio), format="audio/wav")
            st.markdown(prompt)
        haptic(15)
        play_sound("send", char.get("notif_theme"))
        reply = None
        parts = []
        # generăm răspunsul (indicator „scrie...")
        gen_ph = st.empty()
        with gen_ph.container():
            with st.chat_message("assistant", avatar=char.get("avatar", "🎭")):
                st.markdown(TYPING_HTML, unsafe_allow_html=True)
        web_info = ""
        if st.session_state.get("web_search"):
            try:
                web_info = llm.web_lookup(prompt)
            except Exception:  # noqa
                web_info = ""
        try:
            parts = llm.burst_reply(char, history, prompt, web_info=web_info)
        except Exception:  # noqa
            _log.exception("burst_reply failed (chat send)")
            parts = []
        gen_ph.empty()
        if not parts:
            # generarea a eșuat: mesajul userului rămâne salvat → reîncărcăm ca să apară
            # butonul „🔄 Încearcă din nou" (de sub mesaje), în loc de o eroare trecătoare.
            st.rerun()
        else:
            # bulele apar UNA CÂTE UNA, cu o mică pauză + „scrie..." între ele (ca un om real)
            for _i, _p in enumerate(parts):
                if _i > 0:
                    _tph = st.empty()
                    with _tph.container():
                        with st.chat_message("assistant", avatar=char.get("avatar", "🎭")):
                            st.markdown(TYPING_HTML, unsafe_allow_html=True)
                    time.sleep(min(1.3, 0.5 + len(_p) / 80.0))
                    _tph.empty()
                with st.chat_message("assistant", avatar=char.get("avatar", "🎭")):
                    st.markdown(_p)
        if parts:
            reply = " ".join(parts)
            haptic(25)
            msgs = [db.add_message(active_conv, "assistant", p) for p in parts]
            st.session_state["notif_sound"] = True
            # fundal ambiental pentru toată rafala (pe baza întregului text)
            if st.session_state.get("ambient_fx"):
                with st.spinner("Creez ambianța..."):
                    maybe_ambient(char, msgs[0]["id"], reply)
            # voce pentru fiecare mesaj (se redau unul după altul, cu ambient sub ele)
            did_voice = False
            if (st.session_state.get("auto_play") or user_audio) and char.get("voice_id"):
                with st.spinner(f"{char['name']} vorbește..."):
                    for _m in msgs:
                        try:
                            st.session_state[f"audio_{_m['id']}"] = voice.text_to_speech(
                                _m["content"], char["voice_id"], **tts_kwargs
                            )
                        except Exception:  # noqa
                            pass
                st.session_state["autoplay_burst"] = {
                    "ids": [_m["id"] for _m in msgs],
                    "uid": "burst_" + msgs[-1]["id"][:8],
                }
                did_voice = True
            # dacă nu redăm voce, ambientul pornește singur pe primul mesaj
            if not did_voice and st.session_state.get(f"sfx_{msgs[0]['id']}"):
                st.session_state["ambient_play_mid"] = msgs[0]["id"]
            # refresh long-term memory in the background (non-blocking)
            full = db.get_messages(active_conv)
            if len(full) % 6 == 0:
                queue_memory_update(char, full)
            st.rerun()



def render_call(char):
    conv = active_conv_id(char)
    amb = AMBIANCES.get(char.get("ambiance", "Neutru"), AMBIANCES["Neutru"])
    glow = amb["glow"]
    st.markdown(
        f"<style>@keyframes ring{{0%{{box-shadow:0 0 0 0 {glow}66}}70%{{box-shadow:0 0 0 26px {glow}00}}"
        f"100%{{box-shadow:0 0 0 0 {glow}00}}}}"
        f"@keyframes talk{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.05)}}}}"
        f"@keyframes eqbar{{0%,100%{{height:7px}}50%{{height:30px}}}}"
        f".call-wrap{{text-align:center;padding:1.5rem 0 .5rem}}"
        f".call-av{{width:150px;height:150px;border-radius:50%;margin:0 auto 1rem;overflow:hidden;"
        f"display:flex;align-items:center;justify-content:center;font-size:5rem;"
        f"background:{amb['grad']};border:2px solid {glow};animation:ring 1.6s infinite}}"
        f".call-av.talking{{animation:ring 1.6s infinite, talk .55s ease-in-out infinite}}"
        f".call-av img{{width:100%;height:100%;object-fit:cover}}"
        f".call-name{{font-family:'Sora';font-size:1.8rem;font-weight:700}}"
        f".call-status{{color:{glow};font-size:.95rem;margin-top:.2rem}}"
        f".eq{{display:flex;gap:5px;justify-content:center;align-items:flex-end;height:32px;margin:.2rem 0 1rem}}"
        f".eq span{{width:6px;height:7px;border-radius:3px;background:{glow};animation:eqbar .9s ease-in-out infinite}}"
        f".eq span:nth-child(2){{animation-delay:.12s}}.eq span:nth-child(3){{animation-delay:.24s}}"
        f".eq span:nth-child(4){{animation-delay:.36s}}.eq span:nth-child(5){{animation-delay:.48s}}"
        f".eq span:nth-child(6){{animation-delay:.6s}}.eq span:nth-child(7){{animation-delay:.72s}}</style>",
        unsafe_allow_html=True,
    )

    av = avatar_html(char, size=150, radius=75)

    # ---- incoming call (character rings you) ----
    if st.session_state.get("call_incoming"):
        if "ringtone_audio" not in st.session_state:
            try:
                st.session_state["ringtone_audio"] = voice.sound_effect(
                    "classic mobile phone ringtone, ringing repeatedly", duration=8.0
                )
            except Exception:  # noqa
                st.session_state["ringtone_audio"] = None
        st.markdown(
            f'<div class="call-wrap"><div class="call-av talking">{av}</div>'
            f'<div class="call-name">{char["name"]}</div>'
            f'<div class="call-status">📞 te sună...</div></div>',
            unsafe_allow_html=True,
        )
        if st.session_state.get("ringtone_audio"):
            st.audio(st.session_state["ringtone_audio"], format="audio/mp3", autoplay=True)
        cc = st.columns([1, 2, 1])
        with cc[1]:
            a, b = st.columns(2)
            with a:
                if st.button("✅ Răspunde", key="call_answer", use_container_width=True, type="primary"):
                    st.session_state.call_incoming = False
                    with st.spinner("Se conectează..."):
                        send_proactive(char, "call")
                    st.session_state.call_opened = conv
                    st.rerun()
            with b:
                if st.button("❌ Respinge", key="call_reject", use_container_width=True):
                    st.session_state.call_char = None
                    st.session_state.call_incoming = False
                    st.rerun()
        return

    # ---- user-initiated call: character answers first ----
    if st.session_state.get("call_opened") != conv:
        with st.spinner(f"{char['name']} răspunde..."):
            send_proactive(char, "answer")
        st.session_state.call_opened = conv
        st.rerun()

    # ---- in call ----
    hist = db.get_messages(conv)
    last_user = next((m for m in reversed(hist) if m["role"] == "user"), None)
    last_assistant = next((m for m in reversed(hist) if m["role"] == "assistant"), None)

    # ensure the character's latest line has audio, so the portrait "talks"
    if last_assistant and not st.session_state.get(f"audio_{last_assistant['id']}") and char.get("voice_id"):
        try:
            st.session_state[f"audio_{last_assistant['id']}"] = voice.text_to_speech(
                last_assistant["content"], char["voice_id"], **_tts_kwargs(char)
            )
        except Exception:  # noqa
            pass

    speaking = bool(last_assistant and st.session_state.get(f"audio_{last_assistant['id']}"))
    av_cls = "call-av talking" if speaking else "call-av"
    eq = '<div class="eq">' + "".join("<span></span>" for _ in range(7)) + "</div>" if speaking else ""
    status = "🗣️ vorbește..." if speaking else "🔴 În apel"
    st.markdown(
        f'<div class="call-wrap"><div class="{av_cls}">{av}</div>'
        f'<div class="call-name">{char["name"]}</div>'
        f'<div class="call-status">{status}</div>{eq}</div>',
        unsafe_allow_html=True,
    )

    if last_user:
        st.markdown(
            f'<div style="margin:.4rem 0;padding:.7rem 1rem;border-radius:14px;'
            f'background:#141419;border:1px solid #23232b;font-size:1.02rem">'
            f'🧑 <b>Tu:</b> {last_user["content"]}</div>',
            unsafe_allow_html=True,
        )
    if last_assistant:
        st.markdown(
            f'<div aria-live="polite" role="status" style="margin:.4rem 0 1rem;padding:1rem 1.15rem;'
            f'border-radius:16px;background:{amb["grad"]};border:1px solid {glow}66;'
            f'font-size:1.18rem;line-height:1.5;color:#ECECEC">'
            f'🗣️ <b style="color:{glow}">{char["name"]} spune:</b><br>{last_assistant["content"]}</div>',
            unsafe_allow_html=True,
        )
        aid = last_assistant["id"]
        _ab = st.session_state.get(f"audio_{aid}")
        if _ab:
            if st.session_state.get("ambient_fx"):
                with st.spinner("Pregătesc fundalul sonor..."):
                    maybe_ambient(char, aid, last_assistant["content"])  # fundal continuu potrivit locului
            # voce + fundal ambiental continuu sub ea (fiabil pe telefon)
            _play_voice_ambient([_ab], st.session_state.get(f"sfx_{aid}"), aid)
            st.audio(_ab, format="audio/mp3")  # control de reascultare (fără autoplay dublu)

    st.caption("🎤 Apasă microfonul și vorbește, apoi oprește înregistrarea. Prima dată, telefonul "
               "îți va cere permisiunea pentru microfon — apasă „Permite”.")
    audio = st.audio_input("Vorbește", label_visibility="collapsed", key=f"mic_{conv}")
    if audio is not None:
        data = audio.getvalue()
        if data:
            h = hashlib.md5(data).hexdigest()
            if st.session_state.get(f"lastrec_{conv}") != h:
                st.session_state[f"lastrec_{conv}"] = h
                text = ""
                with st.spinner("Te ascult..."):
                    try:
                        text = stt.transcribe(data, "audio.wav")
                    except Exception as e:  # noqa
                        st.error(f"Nu am putut transcrie: {e}")
                if text:
                    db.add_message(conv, "user", text)
                    with st.spinner(f"{char['name']} vorbește..."):
                        try:
                            reply = llm.get_reply(char, hist, text)
                        except Exception as e:  # noqa
                            reply = None
                            st.error(f"Eroare: {e}")
                    if reply:
                        am = db.add_message(conv, "assistant", reply)
                        if char.get("voice_id"):
                            try:
                                st.session_state[f"audio_{am['id']}"] = voice.text_to_speech(
                                    reply, char["voice_id"], **_tts_kwargs(char)
                                )
                            except Exception:  # noqa
                                pass
                    st.rerun()

    if st.button("📴 Închide apelul", key="call_hangup", type="primary", use_container_width=True):
        st.session_state.call_char = None
        st.session_state.call_opened = None
        st.rerun()


def render_presets():
    st.markdown("### ✨ Începe rapid cu un șablon")
    st.caption("Un click și ai un personaj gata de conversație.")
    cols = st.columns(3)
    for i, p in enumerate(PRESETS):
        with cols[i % 3]:
            st.markdown(
                f'<div class="pcard"><div class="pavatar">{p["avatar"]}</div>'
                f'<div class="pname">{p["name"]}</div>'
                f'<div class="pdesc">{p["personality"][:80]}</div></div>',
                unsafe_allow_html=True,
            )
            st.button(
                "Pornește",
                key=f"preset_{i}",
                use_container_width=True,
                on_click=create_from_preset,
                args=(p,),
            )


def clone_public_char(c):
    _u = current_user()
    data = {k: c.get(k) for k in (
        "name", "avatar", "avatar_image", "personality", "scenario", "ambiance",
        "voice_id", "voice_name", "voice_stability", "voice_similarity", "voice_style",
    )}
    data["visibility"] = "private"
    data["owner_id"] = _identity_id()
    new = db.create_character(data)
    try:
        db.increment_stat(c["id"], "clone_count")
    except Exception:  # noqa
        pass
    st.session_state.active_id = new["id"]
    st.session_state.creating = False
    st.session_state.editing_id = None


def _copy_button(text, key):
    safe = text.replace("\\", "\\\\").replace("'", "\\'")
    components.html(
        f"""
        <button id="cb_{key}" style="width:100%;padding:.45rem;border-radius:10px;
          border:1px solid #FF7A59;background:#20140f;color:#FF7A59;font-weight:600;
          font-family:sans-serif;cursor:pointer">📋 Copiază link</button>
        <script>
        const b=document.getElementById('cb_{key}');
        b.onclick=function(){{
          const t='{safe}';
          try{{navigator.clipboard.writeText(t);}}catch(e){{
            const ta=document.createElement('textarea');ta.value=t;
            document.body.appendChild(ta);ta.select();document.execCommand('copy');ta.remove();
          }}
          b.innerText='✓ Copiat!';
          setTimeout(()=>{{b.innerText='📋 Copiază link';}},1800);
        }};
        </script>
        """,
        height=46,
    )


def _is_new(c, days=3):
    from datetime import datetime, timezone, timedelta
    try:
        created = datetime.fromisoformat(c.get("created_at"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        return created > datetime.now(timezone.utc) - timedelta(days=days)
    except Exception:  # noqa
        return False


def _share_popover_body(c, key):
    from urllib.parse import quote
    url = _share_url(c["id"])
    st.caption("Link de partajare:")
    st.code(url, language=None)
    _copy_button(url, key)
    msg = quote(f"Vorbește cu {c['name']} pe Persona: {url}")
    sc = st.columns(2)
    sc[0].link_button("💬 WhatsApp", f"https://wa.me/?text={msg}", use_container_width=True)
    sc[1].link_button(
        "✈️ Telegram",
        f"https://t.me/share/url?url={quote(url)}&text={quote('Vorbește cu ' + c['name'] + ' pe Persona')}",
        use_container_width=True,
    )


def _toggle_fav(char_id):
    _u = current_user()
    if _u:
        db.toggle_favorite(_u["id"], char_id)


def _public_char_card(c, favs, key_prefix, count=0):
    desc = (c.get("personality") or "").strip()[:80] or "personaj"
    voice_label = c.get("voice_name") or "fără voce"
    count_pill = f'<span class="voice-pill">❤️ {count}</span>' if count else ""
    popular_pill = (
        '<span class="voice-pill" style="background:#2a1206;color:#ff9d3c;border-color:#ff9d3c55">🔥 Popular</span>'
        if count >= 2 else ""
    )
    new_pill = (
        '<span class="voice-pill" style="background:#0f1a2a;color:#5cc8ff;border-color:#5cc8ff55">✨ Nou</span>'
        if _is_new(c) else ""
    )
    st.markdown(
        f'<div class="pcard"><div class="pavatar">{avatar_html(c, size=64, radius=16)}</div>'
        f'<div class="pname">{c["name"]}</div>'
        f'<div class="pdesc">{desc}</div>'
        f'<span class="voice-pill">🔊 {voice_label}</span> {count_pill} {popular_pill} {new_pill}</div>',
        unsafe_allow_html=True,
    )
    if favs is not None:
        bc = st.columns([2, 1, 1])
        bc[0].button("➕ Adaugă", key=f"{key_prefix}_clone_{c['id']}",
                     use_container_width=True, on_click=clone_public_char, args=(c,))
        faved = c["id"] in favs
        bc[1].button("❤️" if faved else "🤍", key=f"{key_prefix}_fav_{c['id']}",
                     help="Elimină de la favorite" if faved else "Adaugă la favorite",
                     use_container_width=True, on_click=_toggle_fav, args=(c["id"],))
        with bc[2].popover("🔗", use_container_width=True):
            _share_popover_body(c, f"{key_prefix}_{c['id']}")
    else:
        bc = st.columns([3, 1])
        bc[0].button("➕ Adaugă la mine", key=f"{key_prefix}_clone_{c['id']}",
                     use_container_width=True, on_click=clone_public_char, args=(c,))
        with bc[1].popover("🔗", use_container_width=True):
            _share_popover_body(c, f"{key_prefix}_{c['id']}")


def render_favorites():
    _u = current_user()
    if not _u:
        return
    favs = db.get_favorites(_u["id"])
    chars = [db.get_character(cid) for cid in favs]
    chars = [c for c in chars if c]
    if not chars:
        return
    counts = db.favorite_counts()
    st.markdown("### ❤️ Favoritele tale")
    st.caption("Personajele publice pe care le-ai marcat.")
    cols = st.columns(3)
    for i, c in enumerate(chars):
        with cols[i % 3]:
            _public_char_card(c, favs, "favsec", counts.get(c["id"], 0))
    st.markdown("---")


def render_public_gallery():
    _u = current_user()
    my_id = _identity_id()
    favs = db.get_favorites(my_id) if _u else None
    counts = db.favorite_counts()
    all_pubs = [c for c in db.list_public_characters() if c.get("owner_id") != my_id]
    if not all_pubs:
        return
    all_pubs.sort(key=lambda c: counts.get(c["id"], 0), reverse=True)

    top = [c for c in all_pubs if counts.get(c["id"], 0) > 0][:3]
    if top:
        st.markdown("### 🏆 Top personaje")
        st.caption("Cele mai îndrăgite personaje publice.")
        medals = ["🥇", "🥈", "🥉"]
        for i, c in enumerate(top):
            st.markdown(
                f'{medals[i]} **{c["name"]}** — ❤️ {counts.get(c["id"], 0)}'
                f'{"  🔥" if counts.get(c["id"], 0) >= 2 else ""}'
            )
        st.markdown("")

    st.markdown("### 🌍 Descoperă personaje publice")
    st.caption("Personaje create de alți utilizatori, ordonate după cât de îndrăgite sunt. "
               "Adaugă-le la tine, marchează-le ❤️ sau partajează-le 🔗.")
    fcols = st.columns([3, 2])
    q = fcols[0].text_input("🔎 Caută", key="pub_search", placeholder="caută un personaj după nume...",
                            label_visibility="collapsed")
    amb_filter = fcols[1].selectbox(
        "Ambianță", ["Toate ambianțele"] + list(AMBIANCES.keys()),
        key="pub_amb_filter", label_visibility="collapsed",
    )
    pubs = all_pubs
    if q:
        pubs = [c for c in pubs if q.strip().lower() in c["name"].lower()]
    if amb_filter != "Toate ambianțele":
        pubs = [c for c in pubs if c.get("ambiance") == amb_filter]
    if not pubs:
        st.caption("Niciun personaj găsit.")
        st.markdown("---")
        return
    cols = st.columns(3)
    for i, c in enumerate(pubs[:12]):
        with cols[i % 3]:
            _public_char_card(c, favs, "pubsec", counts.get(c["id"], 0))
    st.markdown("---")


def _add_from_preview(c):
    clone_public_char(c)
    st.session_state.pop("preview_id", None)
    try:
        st.query_params.clear()
    except Exception:  # noqa
        pass


def _exit_preview():
    st.session_state.pop("preview_id", None)
    try:
        st.query_params.clear()
    except Exception:  # noqa
        pass


def _exit_playlist_share():
    st.session_state.pop("playlist_share_id", None)
    st.session_state.pop("_plshared_seen", None)
    try:
        st.query_params.clear()
    except Exception:  # noqa
        pass
    st.session_state.nav = "personaje"


def render_playlist_share(char):
    amb = AMBIANCES.get(char.get("ambiance", "Neutru"), AMBIANCES["Neutru"])
    st.markdown(
        f'<div class="char-header" style="background:{amb["grad"]};border-color:{amb["glow"]}44">'
        f'<div class="avatar">{avatar_html(char, size=64, radius=16)}</div>'
        f'<div><div class="name">🎵 Playlist-ul lui {char["name"]}</div>'
        f'<div class="meta">Cineva ți-a partajat acest playlist</div></div></div>',
        unsafe_allow_html=True,
    )
    songs = db.list_songs(char["id"]) if hasattr(db, "list_songs") else []
    if not songs:
        st.info("Acest playlist e gol deocamdată.")
    else:
        st.caption("Ascultă melodiile de mai jos. 🎧")
        _render_playlist(char, key_prefix="share", readonly=True)
    st.markdown("---")
    st.button("← Deschide Persona (creează-ți propriile personaje)", key="plshare_back",
              use_container_width=True, on_click=_exit_playlist_share)


def render_preview(char):
    amb = AMBIANCES.get(char.get("ambiance", "Neutru"), AMBIANCES["Neutru"])
    desc = (char.get("personality") or "").strip() or "personaj"
    voice_label = char.get("voice_name") or "fără voce"
    st.markdown(
        f'<div class="char-header" style="background:{amb["grad"]};border-color:{amb["glow"]}44">'
        f'<div class="avatar">{avatar_html(char, size=64, radius=16)}</div>'
        f'<div><div class="name">{char["name"]}</div>'
        f'<div class="meta">Cineva ți-a partajat acest personaj</div></div>'
        f'<div style="margin-left:auto"><span class="voice-pill">🔊 {voice_label}</span></div></div>',
        unsafe_allow_html=True,
    )
    st.markdown(f"**Despre:** {desc}")
    if char.get("scenario"):
        st.caption(f"Scenariu: {char['scenario'][:200]}")
    cc = st.columns(2)
    cc[0].button("➕ Adaugă la mine și pornește", key="pv_add", type="primary",
                 use_container_width=True, on_click=_add_from_preview, args=(char,))
    cc[1].button("← Înapoi", key="pv_back", use_container_width=True, on_click=_exit_preview)


def render_recent():
    ids = st.session_state.get("recent", [])
    chars = []
    for cid in ids:
        c = db.get_character(cid)
        if c:
            chars.append(c)
    if not chars:
        return
    st.markdown("### 🕘 Continuă unde ai rămas")
    cols = st.columns(4)
    for i, c in enumerate(chars[:4]):
        with cols[i]:
            n = db.character_message_count(c["id"])
            st.button(f"{c.get('avatar', '🎭')}  {c['name']}  ·  {n} 💬", key=f"recent_{c['id']}",
                      use_container_width=True, on_click=select_char, args=(c["id"],))
    st.markdown("---")


def render_gallery():
    _u = current_user()
    if _u:
        st.markdown(
            f'<div style="font-family:Sora;font-size:1.15rem;font-weight:700;color:#FF7A59;'
            f'margin-bottom:.6rem">👋 Bine ai revenit, {_u["name"]}!</div>',
            unsafe_allow_html=True,
        )
    render_recent()
    chars = db.list_characters(owner_id=_identity_id())
    for _c in chars:
        if _c.get("visibility") != "private":
            db.update_character(_c["id"], {"visibility": "private"})
            _c["visibility"] = "private"
    if chars:
        st.markdown(
            '<div class="hero"><h1>Personajele <span class="accent">tale</span></h1>'
            "<p>Alege un personaj ca să continui conversația, sau creează unul nou.</p></div>",
            unsafe_allow_html=True,
        )
        cols = st.columns(3)
        fav_counts = db.favorite_counts()
        for i, c in enumerate(chars):
            with cols[i % 3]:
                desc = (c.get("personality") or "").strip()[:80] or "personaj"
                voice_label = c.get("voice_name") or "fără voce"
                vis = "🌍 Public" if c.get("visibility") == "public" else "🔒 Privat"
                stat_pill = ""
                if c.get("visibility") == "public":
                    favn = fav_counts.get(c["id"], 0)
                    adds = int(c.get("clone_count", 0) or 0)
                    stat_pill = (
                        f'<span class="voice-pill">❤️ {favn}</span> '
                        f'<span class="voice-pill">➕ {adds}</span>'
                    )
                st.markdown(
                    f'<div class="pcard"><div class="pavatar">{avatar_html(c, size=64, radius=16)}</div>'
                    f'<div class="pname">{c["name"]}</div>'
                    f'<div class="pdesc">{desc}</div>'
                    f'<span class="voice-pill">🔊 {voice_label}</span> '
                    f'<span class="voice-pill">{vis}</span> {stat_pill}</div>',
                    unsafe_allow_html=True,
                )
                st.button(
                    "Deschide chat",
                    key=f"open_{c['id']}",
                    use_container_width=True,
                    on_click=select_char,
                    args=(c["id"],),
                )
        st.markdown("---")
    else:
        st.markdown(
            '<div class="hero"><h1>Dă viață unui <span class="accent">personaj</span>.</h1>'
            "<p>Creează personaje AI cu personalitate proprie, discută cu ele și ascultă-le "
            "răspunsul cu o voce clonată prin ElevenLabs. Alege un șablon de mai jos sau apasă "
            "<b>＋ Personaj nou</b>.</p></div>",
            unsafe_allow_html=True,
        )
    render_presets()

    st.markdown(
        '<div style="text-align:center;color:#8a8a95;font-size:.85rem;margin:.6rem 0 1.2rem">'
        "💬 Fără limită de cuvinte — fiecare poate vorbi oricât vrea cu personajul lui, despre orice, "
        "chiar și despre durerile și necazurile sale.</div>",
        unsafe_allow_html=True,
    )

    st.markdown("---")
    render_favorites()
    render_public_gallery()
    with st.expander("📥 Importă un personaj (fișier .persona.json)"):
        up = st.file_uploader("Fișier personaj", type=["json"], key="import_file",
                              label_visibility="collapsed")
        if up and st.button("Adaugă personajul importat", key="do_import", type="primary"):
            try:
                data = json.loads(up.getvalue().decode("utf-8"))
                vid, vname = _resolve_voice(data.get("voice_name"))
                new_data = {
                    "name": data.get("name", "Personaj importat"),
                    "avatar": data.get("avatar", "🎭"),
                    "personality": data.get("personality", ""),
                    "scenario": data.get("scenario", ""),
                    "ambiance": data.get("ambiance", "Neutru"),
                    "visibility": data.get("visibility", "private"),
                    "voice_id": vid, "voice_name": vname or data.get("voice_name"),
                    "voice_stability": float(data.get("voice_stability", 0.5)),
                    "voice_similarity": float(data.get("voice_similarity", 0.75)),
                    "voice_style": float(data.get("voice_style", 0.0)),
                    "memory": data.get("memory", ""),
                }
                _iu = current_user()
                new_data["owner_id"] = _identity_id()
                char = db.create_character(new_data)
                st.session_state.active_id = char["id"]
                st.session_state.nav = "chat"
                st.success(f"„{char['name']}” a fost importat!")
                st.rerun()
            except Exception as e:  # noqa
                st.error(f"Fișier invalid: {e}")


# ------------------------- navigation tabs -------------------------
def _build_backup_zip(owner_id):
    """Împachetează toate personajele, conversațiile și media utilizatorului într-un singur .zip."""
    import io
    import zipfile

    def _safe(name, fallback="fara-nume"):
        s = "".join(c for c in (name or "") if c.isalnum() or c in " -_").strip()
        return (s or fallback)[:60]

    buf = io.BytesIO()
    n_chars = n_msgs = n_photos = n_songs = n_videos = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for ch in db.list_characters(owner_id=owner_id):
            n_chars += 1
            cname = _safe(ch.get("name"), "personaj")
            for conv in db.list_conversations(ch["id"]):
                lines = [f"Conversație: {conv.get('title', '')}",
                         f"Personaj: {ch.get('name', '')}", "=" * 40, ""]
                photo_i = song_i = video_i = 0
                for m in db.get_messages(conv["id"]):
                    role = "Tu" if m.get("role") == "user" else ch.get("name", "Personaj")
                    ts = (str(m.get("created_at") or ""))[:19].replace("T", " ")
                    kind = m.get("media_kind")
                    lines.append(f"[{ts}] {role}: {m.get('content', '')}")
                    n_msgs += 1
                    try:
                        if kind == "photo" and m.get("image_b64"):
                            photo_i += 1; n_photos += 1
                            zf.writestr(f"poze/{cname}/{cname}-poza-{photo_i}.jpg",
                                        base64.b64decode(m["image_b64"]))
                        elif kind == "song" and m.get("song_b64"):
                            song_i += 1; n_songs += 1
                            sn = _safe(m.get("song_name"), f"melodie-{song_i}")
                            zf.writestr(f"melodii/{cname}/{sn}.mp3",
                                        base64.b64decode(m["song_b64"]))
                        elif kind == "video" and m.get("video_b64"):
                            video_i += 1; n_videos += 1
                            zf.writestr(f"videoclipuri/{cname}/{cname}-video-{video_i}.mp4",
                                        base64.b64decode(m["video_b64"]))
                    except Exception:  # noqa
                        pass
                ctitle = _safe(conv.get("title"), "conversatie")
                zf.writestr(f"conversatii/{cname}/{ctitle}.txt",
                            ("\n".join(lines)).encode("utf-8"))
        readme = (
            "BACKUP PERSONA\n==============\n\n"
            f"Data: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
            f"Personaje: {n_chars}\nMesaje: {n_msgs}\nPoze: {n_photos}\n"
            f"Melodii: {n_songs}\nVideoclipuri: {n_videos}\n\n"
            "Ce găsești aici:\n"
            "- conversatii/  = toate discuțiile tale, pe personaje (fișiere .txt)\n"
            "- poze/         = toate pozele trimise, pe personaje\n"
            "- melodii/      = toate melodiile trimise, pe personaje (.mp3)\n"
            "- videoclipuri/ = videoclipurile trimise, pe personaje\n\n"
            "Păstrează acest fișier în siguranță (pe telefon, pe email sau în cloud).\n"
        )
        zf.writestr("CITESTE-MA.txt", readme.encode("utf-8"))
    return buf.getvalue()


def render_amintiri():
    st.markdown('<div class="hero"><h1>Amintirile <span class="accent">mele</span></h1></div>',
                unsafe_allow_html=True)
    st.caption("Toate pozele, melodiile și videoclipurile pe care le-ai trimis personajelor tale.")
    _oid = _identity_id()

    # ---- backup: descarcă tot într-un singur fișier ----
    if db.list_characters(owner_id=_oid):
        with st.expander("💾 Descarcă tot (backup) — poze, melodii și mesaje"):
            st.caption("Salvează într-un singur fișier .zip toate conversațiile, pozele și "
                       "melodiile tale, ca să nu le pierzi niciodată. Îl poți păstra pe telefon, "
                       "pe email sau în cloud.")
            if st.button("📦 Pregătește backup-ul", key="prep_backup",
                         use_container_width=True, type="primary"):
                with st.spinner("Adun toate amintirile într-un fișier..."):
                    try:
                        st.session_state["_backup_zip"] = _build_backup_zip(_oid)
                    except Exception as e:  # noqa
                        st.session_state["_backup_zip"] = None
                        st.error(f"Nu am putut crea backup-ul: {e}")
            if st.session_state.get("_backup_zip"):
                st.success("Backup-ul e gata! Apasă mai jos ca să-l descarci.")
                st.download_button(
                    "⬇️ Descarcă backup-ul (.zip)",
                    data=st.session_state["_backup_zip"],
                    file_name=f"persona-backup-{datetime.now().strftime('%Y-%m-%d')}.zip",
                    mime="application/zip",
                    key="dl_backup",
                    use_container_width=True,
                )

    media = db.list_media(_oid)
    letters = db.list_letters(_oid)

    # ---- letters journal (Colecția de scrisori) ----
    if letters:
        st.markdown("### 💌 Scrisorile mele")
        st.caption("Toate scrisorile pe care ți le-au scris personajele — jurnalul tău de amintiri.")
        for L in letters:
            _body = L["content"].split("\n\n", 1)[1] if "\n\n" in L["content"] else L["content"]
            _date = (str(L.get("created_at") or ""))[:10]
            with st.container(border=True):
                st.markdown(f"**{L['char_avatar']} {L['char_name']}** · {_date}")
                st.markdown(_body)
                _lc = st.columns(2)
                _lc[0].download_button("⬇️ Salvează (.txt)", data=L["content"],
                                       file_name=f"scrisoare-{L['char_name']}-{_date}.txt",
                                       mime="text/plain", key=f"jdl_{L['id']}",
                                       use_container_width=True)
                if L.get("voice_id"):
                    if _lc[1].button("🔊 Ascultă", key=f"jlisten_{L['id']}",
                                     use_container_width=True):
                        with st.spinner("Generez vocea..."):
                            try:
                                _ch = db.get_character(L["char_id"])
                                st.session_state[f"jaudio_{L['id']}"] = voice.text_to_speech(
                                    _body, L["voice_id"], **_tts_kwargs(_ch))
                                st.session_state["jautoplay"] = L["id"]
                            except Exception:  # noqa
                                st.error("Nu am putut genera vocea acum. Mai încearcă mai târziu.")
                    if st.session_state.get(f"jaudio_{L['id']}"):
                        if st.session_state.get("jautoplay") == L["id"]:
                            _autoplay_voice(st.session_state[f"jaudio_{L['id']}"], f"j{L['id']}")
                            st.session_state["jautoplay"] = None
                        st.audio(st.session_state[f"jaudio_{L['id']}"], format="audio/mp3")
        st.markdown("---")

    if not media:
        if not letters:
            st.info("Încă nu ai trimis nicio poză sau melodie. Deschide un personaj și trimite-i "
                    "ceva din chat — o poză 📷 sau o melodie 🎵.")
        return
    from collections import OrderedDict
    groups = OrderedDict()
    for m in media:
        groups.setdefault((m["char_id"], m["char_name"], m.get("char_avatar", "🎭")), []).append(m)
    for (cid, cname, cav), items in groups.items():
        st.markdown(f"### {cav} {cname}")
        photos = [m for m in items if m["media_kind"] == "photo" and m.get("image_b64")]
        songs = [m for m in items if m["media_kind"] == "song"]
        videos = [m for m in items if m["media_kind"] == "video" and m.get("video_b64")]
        if photos:
            st.caption(f"📷 Poze ({len(photos)})")
            pcols = st.columns(3)
            for i, m in enumerate(photos):
                with pcols[i % 3]:
                    st.image(base64.b64decode(m["image_b64"]), use_container_width=True)
        if songs:
            st.caption("🎵 Playlist-ul nostru")
            ch_obj = db.get_character(cid)
            if ch_obj and hasattr(db, "list_songs"):
                _render_playlist(ch_obj, key_prefix="amt")
            else:
                for j, m in enumerate(songs):
                    st.markdown(f"• **{m.get('song_name', 'melodie')}**")
                    if m.get("song_b64"):
                        st.audio(base64.b64decode(m["song_b64"]), format="audio/mp3")
        if videos:
            st.caption(f"🎬 Videoclipuri ({len(videos)})")
            for m in videos:
                st.video(base64.b64decode(m["video_b64"]))
        if st.button(f"🎧 {cname} îmi recomandă melodii noi", key=f"rec_{cid}",
                     use_container_width=True, type="primary"):
            ch = db.get_character(cid)
            if ch:
                names = db.list_song_names(cid)
                conv = active_conv_id(ch)
                hist = db.get_messages(conv)
                with st.spinner(f"{cname} alege melodii pentru tine..."):
                    try:
                        rec = llm.recommend_songs(ch, hist, names)
                    except Exception:  # noqa
                        rec = None
                if rec:
                    amsg = db.add_message(conv, "assistant", "🎧 Recomandările mele:\n\n" + rec)
                    if ch.get("voice_id"):
                        try:
                            st.session_state[f"audio_{amsg['id']}"] = voice.text_to_speech(
                                rec, ch["voice_id"], **_tts_kwargs(ch))
                            st.session_state["autoplay_mid"] = amsg["id"]
                        except Exception:  # noqa
                            pass
                    st.session_state.active_id = cid
                    st.session_state.nav = "chat"
                    st.rerun()
        st.markdown("---")


def _nav_bar():
    items = [("personaje", "🎭 Personaje"),
             ("amintiri", "🎞️ Amintiri"), ("chat", "💬 Chat"), ("profil", "👤 Profil")]
    cur = st.session_state.get("nav", "personaje")
    cols = st.columns(len(items))
    for i, (key, label) in enumerate(items):
        if cols[i].button(label, key=f"nav_{key}", use_container_width=True,
                          type="primary" if cur == key else "secondary"):
            st.session_state.nav = key
            if key != "chat":
                st.session_state.creating = False
            st.rerun()
    st.markdown("")


def render_personaje():
    _u = current_user()
    if _u:
        st.markdown(
            '<div style="background:linear-gradient(135deg,#17171C,#20141a);'
            'border:1px solid #FF7A5940;border-radius:14px;padding:.9rem 1.1rem;'
            'margin-bottom:1rem;font-size:1.05rem">'
            f'👋 Bine ai venit, <b style="color:#FF7A59">{_u["name"]}</b>! '
            '<span style="opacity:.75">Aceasta e aplicația ta de personaje AI.</span></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div style="background:linear-gradient(135deg,#17171C,#20141a);'
            'border:1px solid #FF7A5940;border-radius:14px;padding:1rem 1.15rem;'
            'margin-bottom:.6rem;font-size:1.02rem">'
            '👋 <b style="color:#FF7A59">Bun venit pe Persona!</b><br>'
            '<span style="opacity:.85">Poți crea personaje și vorbi cu ele chiar și fără cont. '
            'Dar dacă îți faci un cont gratuit, <b>personajele tale se salvează</b> și nu le pierzi, '
            'iar le poți regăsi de pe orice telefon.</span></div>',
            unsafe_allow_html=True,
        )
        if st.button("✨ Fă-ți cont gratuit (ca să nu-ți pierzi personajele)",
                     key="onboard_signup", use_container_width=True, type="primary",
                     help="Deschide formularul de creare cont din bara laterală"):
            st.session_state["_open_auth_hint"] = True
            st.rerun()
        if st.session_state.get("_open_auth_hint"):
            st.info("👈 Deschide **🔐 Autentificare** din bara laterală și alege fila "
                    "**„Cont nou”** ca să-ți creezi contul. Personajele create acum vor trece "
                    "automat în contul tău.")
    if st.button("＋  Personaj nou", use_container_width=True, type="primary", key="new-char-main"):
        _clear_form()
        st.session_state.creating = True
        st.session_state.editing_id = None
        st.session_state.active_id = None
        st.rerun()
    render_recent()
    chars = db.list_characters(owner_id=_identity_id())
    for _c in chars:
        if _c.get("visibility") != "private":
            db.update_character(_c["id"], {"visibility": "private"})
            _c["visibility"] = "private"
    if chars:
        st.markdown(
            '<div class="hero"><h1>Personajele <span class="accent">tale</span></h1>'
            "<p>Alege un personaj ca să continui conversația, sau creează unul nou.</p></div>",
            unsafe_allow_html=True,
        )
        cols = st.columns(3)
        fav_counts = db.favorite_counts()
        for i, c in enumerate(chars):
            with cols[i % 3]:
                desc = (c.get("personality") or "").strip()[:80] or "personaj"
                voice_label = c.get("voice_name") or "fără voce"
                vis = "🌍 Public" if c.get("visibility") == "public" else "🔒 Privat"
                stat_pill = ""
                if c.get("visibility") == "public":
                    favn = fav_counts.get(c["id"], 0)
                    adds = int(c.get("clone_count", 0) or 0)
                    stat_pill = (
                        f'<span class="voice-pill">❤️ {favn}</span> '
                        f'<span class="voice-pill">➕ {adds}</span>'
                    )
                st.markdown(
                    f'<div class="pcard"><div class="pavatar">{avatar_html(c, size=64, radius=16)}</div>'
                    f'<div class="pname">{c["name"]}</div>'
                    f'<div class="pdesc">{desc}</div>'
                    f'<span class="voice-pill">🔊 {voice_label}</span> '
                    f'<span class="voice-pill">{vis}</span> {stat_pill}</div>',
                    unsafe_allow_html=True,
                )
                st.button("Deschide chat", key=f"openp_{c['id']}", use_container_width=True,
                          on_click=select_char, args=(c["id"],))
                if st.button("🗑 Șterge", key=f"delp_{c['id']}", use_container_width=True):
                    db.delete_character(c["id"])
                    if st.session_state.active_id == c["id"]:
                        st.session_state.active_id = None
                    st.rerun()
    else:
        st.markdown(
            '<div class="hero"><h1>Dă viață unui <span class="accent">personaj</span>.</h1>'
            "<p>Creează personaje AI cu personalitate proprie, discută cu ele și ascultă-le "
            "răspunsul cu o voce clonată. Apasă <b>＋ Personaj nou</b> de mai sus.</p></div>",
            unsafe_allow_html=True,
        )


def render_explore():
    st.markdown(
        '<div class="hero"><h1><span class="accent">Explorează</span></h1>'
        "<p>Descoperă personaje publice, șabloane gata făcute și favoritele tale.</p></div>",
        unsafe_allow_html=True,
    )
    render_presets()
    st.markdown("---")
    render_favorites()
    render_public_gallery()


def render_profil():
    user = current_user()
    if not user:
        st.info("Loghează-te din bara laterală 👈 ca să-ți vezi profilul și setările.")
        return
    st.markdown('<div class="hero"><h1>Profilul <span class="accent">tău</span></h1></div>',
                unsafe_allow_html=True)
    col = st.container()
    with col:
        with st.expander("👤 Profil & Setări", expanded=True):
            up = st.file_uploader("Poză de profil", type=["png", "jpg", "jpeg", "webp"], key="pf_pic")
            if up is not None:
                raw = up.getvalue()
                h = hashlib.md5(raw).hexdigest()
                if st.session_state.get("pf_edit_h") != h:
                    st.session_state.pf_edit_h = h
                    st.session_state.pf_rot = 0
                    st.session_state.pf_square = True
                rc = st.columns(3)
                if rc[0].button("⟲ Rotește", key="pf_rl", use_container_width=True):
                    st.session_state.pf_rot = (st.session_state.get("pf_rot", 0) - 90) % 360
                    st.rerun()
                if rc[1].button("⟳ Rotește", key="pf_rr", use_container_width=True):
                    st.session_state.pf_rot = (st.session_state.get("pf_rot", 0) + 90) % 360
                    st.rerun()
                st.session_state.pf_square = rc[2].checkbox(
                    "Pătrat", value=st.session_state.get("pf_square", True), key="pf_sq",
                    help="Decupează pătrat (centrat)",
                )
                edited = _process_pic(raw, st.session_state.get("pf_rot", 0), st.session_state.pf_square)
                st.image(edited, width=140, caption="Previzualizare")
                if st.button("💾 Salvează poza", key="pf_save_pic", use_container_width=True):
                    img_b64 = base64.b64encode(edited).decode()
                    db.update_user(user["id"], {"avatar_image": img_b64})
                    st.session_state.auth_user["avatar_image"] = img_b64
                    st.rerun()
            if st.button("🎨 Generează avatar AI", key="pf_gen_pic", use_container_width=True):
                with st.spinner("Pictez avatarul..."):
                    try:
                        img = image_gen.generate_avatar(user["name"], "portret de profil prietenos, cald", "")
                        if img:
                            db.update_user(user["id"], {"avatar_image": img})
                            st.session_state.auth_user["avatar_image"] = img
                    except Exception as e:  # noqa
                        st.error(f"Generarea a eșuat: {e}")
                st.rerun()
            if user.get("avatar_image") and st.button("❌ Elimină poza", key="pf_del_pic", use_container_width=True):
                db.update_user(user["id"], {"avatar_image": None})
                st.session_state.auth_user["avatar_image"] = None
                st.rerun()
            nm = st.text_input("Nume afișat", value=user["name"], key="pf_name")
            if st.button("💾 Salvează numele", key="pf_save_name", use_container_width=True):
                new_name = nm.strip() or user["name"]
                db.update_user(user["id"], {"name": new_name})
                st.session_state.auth_user["name"] = new_name
                st.rerun()
            st.markdown("---")
            st.caption("⚙️ Setări")
            st.session_state.auto_play = st.toggle(
                "🔊 Auto-redare voce",
                value=st.session_state.auto_play,
                help="Redă automat răspunsul cu vocea personajului",
                key="auto_play_toggle",
            )
            st.session_state.ambient_fx = st.toggle(
                "🎧 Efecte ambientale",
                value=st.session_state.ambient_fx,
                help="Redă sunete de fundal potrivite acțiunii (ex: vase, parc, ploaie)",
                key="ambient_fx_toggle",
            )
            st.session_state.ambient_volume = st.slider(
                "🎚️ Volum fundal (sub voce)",
                0, 100, int(st.session_state.get("ambient_volume", 25)),
                help="Cât de tare se aude sunetul de fundal sub vocea personajului",
                key="ambient_volume_slider",
            )
            st.session_state.voice_volume = st.slider(
                "🔊 Volum voce",
                0, 100, int(st.session_state.get("voice_volume", 100)),
                help="Cât de tare se aude vocea personajului (separat de fundal)",
                key="voice_volume_slider",
            )
            _spd_opts = [0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]
            _csp = float(st.session_state.get("voice_speed", 1.0))
            st.session_state.voice_speed = st.select_slider(
                "🏃 Viteza vocii",
                options=_spd_opts,
                value=_csp if _csp in _spd_opts else 1.0,
                format_func=lambda s: ("normal" if s == 1.0 else
                                       (f"{s:g}× mai rar" if s < 1 else f"{s:g}× mai rapid")),
                help="Cât de repede vorbește personajul",
                key="voice_speed_slider",
            )
            st.session_state.web_search = st.toggle(
                "🌐 Căutare web",
                value=st.session_state.web_search,
                help="Personajul caută informații reale, la zi, pentru întrebări factuale (știri, sănătate, date)",
                key="web_search_toggle",
            )
            st.session_state.theme_light = st.toggle(
                "☀️ Temă luminoasă",
                value=st.session_state.theme_light,
                help="Comută între mod zi (luminos) și noapte (întunecat)",
                key="theme_light_toggle",
            )
            st.session_state.notif_volume = st.slider(
                "🔊 Volum notificări",
                0, 100, int(st.session_state.get("notif_volume", 70)),
                help="Volumul sunetelor de notificare și mesaj",
                key="notif_volume_slider",
            )
            _snd_opts = ["iPhone", "Samsung"]
            _cur_snd = st.session_state.get("sound_theme", "iPhone")
            _snd = st.selectbox(
                "🔔 Stil sunete (notificare, trimitere, apel)",
                _snd_opts,
                index=_snd_opts.index(_cur_snd) if _cur_snd in _snd_opts else 0,
                key="sound_theme_sel",
                help="Alege cum sună mesajele și apelurile: stil iPhone sau Samsung",
            )
            if _snd != st.session_state.get("sound_theme"):
                st.session_state.sound_theme = _snd
                _write_sound_cookie(_snd)
                st.rerun()
            _tc = st.columns(3)
            if _tc[0].button("🔔 Notificare", key="test_notif_sound", use_container_width=True):
                play_sound("notification")
            if _tc[1].button("✉️ Trimitere", key="test_send_sound", use_container_width=True):
                play_sound("send")
            if _tc[2].button("📞 Apel", key="test_ring_sound", use_container_width=True):
                play_sound("ringtone")
            st.markdown("---")
            st.caption("🔔 Notificări & mesaje de absență")
            st.session_state.notify_on = st.toggle(
                "🔔 Notificări în browser",
                value=st.session_state.get("notify_on", False),
                key="notify_on_toggle",
                help="Primești o notificare când un personaj îți scrie, chiar dacă ești în altă filă/aplicație.",
            )
            if st.session_state.notify_on:
                _request_notify_permission_js()
                st.caption("✅ Pornite. Merg cât timp aplicația e deschisă (și în fundal). "
                           "Când închizi complet browserul nu pot ajunge.")
            st.session_state.absence_on = st.toggle(
                "💤 Mesaje de absență (personajele te caută dacă lipsești)",
                value=st.session_state.get("absence_on", False),
                key="absence_on_toggle",
                help="Dacă nu mai vorbești o vreme, personajul tău cel mai recent îți trimite un mesaj cald („mi-e dor de tine”).",
            )
            _abs_opts = [5, 15, 30, 60, 120]
            _abs_cur = st.session_state.get("absence_min", 15)
            st.session_state.absence_min = st.select_slider(
                "După cât timp de absență?",
                options=_abs_opts,
                value=_abs_cur if _abs_cur in _abs_opts else 15,
                format_func=lambda x: f"{x} min",
                key="absence_min_sel",
            )
            _write_notify_params()
            if st.button("📩 Testează notificarea", key="test_browser_notify", use_container_width=True):
                st.session_state.notify_on = True
                _request_notify_permission_js()
                _browser_notify("Persona 🎭", "Notificările funcționează! Personajele te vor anunța aici.")
                st.toast("Am trimis o notificare de test 🔔")
            st.markdown("---")
            st.caption("🎂 Ziua ta & sărbători")
            import datetime as _dtmod
            _has_bd = bool(st.session_state.get("birthday"))
            _bd_default = None
            if _has_bd:
                try:
                    _mm, _dd = st.session_state.birthday.split("-")
                    _bd_default = _dtmod.date(2000, int(_mm), int(_dd))
                except Exception:  # noqa
                    _bd_default = None
            _set_bd = st.checkbox("Vreau să-mi urați ziua de naștere", value=_has_bd, key="bd_enable")
            if _set_bd:
                _bd = st.date_input(
                    "🎂 Ziua ta de naștere",
                    value=_bd_default or _dtmod.date(2000, 1, 1),
                    min_value=_dtmod.date(1920, 1, 1),
                    max_value=_dtmod.date(2020, 12, 31),
                    format="DD.MM.YYYY",
                    key="bd_input",
                )
                _new_bd = f"{_bd.month:02d}-{_bd.day:02d}"
                if _new_bd != st.session_state.get("birthday"):
                    st.session_state.birthday = _new_bd
                    _write_notify_params()
                st.caption("De ziua ta, personajele tale îți vor ura „La mulți ani!” 🎉")
            elif st.session_state.get("birthday"):
                st.session_state.birthday = ""
                _write_notify_params()
            st.session_state.holidays_on = st.toggle(
                "🎉 Mesaje de sărbători (Crăciun, Anul Nou, Paște etc.)",
                value=st.session_state.get("holidays_on", True),
                key="holidays_on_toggle",
                help="Personajele îți trimit un mesaj cald de sărbători, inclusiv Paștele, Floriile și Rusaliile (date calculate automat).",
            )
            _write_notify_params()
            st.markdown("---")
            if st.checkbox("Vreau să-mi șterg contul", key="del_confirm"):
                st.warning("Se șterg definitiv contul și toate personajele tale. Acțiunea e ireversibilă.")
                typed = st.text_input("Scrie ȘTERGE pentru a confirma", key="del_typed")
                if st.button("🗑️ Șterge contul definitiv", key="del_account", use_container_width=True,
                             disabled=typed.strip().upper() not in ("ȘTERGE", "STERGE")):
                    db.delete_user(user["id"])
                    _logout_user()
                    st.rerun()
    st.markdown("---")
    st.caption("Apasă aici ca să te deconectezi sau ca să se logheze altcineva pe acest telefon:")
    if st.button("🚪 Ieși din cont (schimbă utilizatorul)", key="logout_btn",
                 use_container_width=True, type="primary"):
        _logout_user()
        st.session_state.nav = "personaje"
        st.rerun()


# ------------------------- router -------------------------
_handle_share_param()
# fire a pending browser notification (proactive/absence message arrived)
if st.session_state.get("notify_on") and st.session_state.get("_pending_notify"):
    _pn = st.session_state.pop("_pending_notify")
    _browser_notify(_pn[0], _pn[1])
else:
    st.session_state.pop("_pending_notify", None)
# background: characters look for you if you've been away
if st.session_state.get("absence_on"):
    absence_fragment()
try:
    if st.session_state.get("playlist_share_id"):
        _pl = db.get_character(st.session_state.playlist_share_id)
        if _pl:
            render_playlist_share(_pl)
        else:
            st.session_state.pop("playlist_share_id", None)
            _nav_bar()
            render_personaje()
    elif st.session_state.get("preview_id"):
        pv = db.get_character(st.session_state.preview_id)
        if pv and pv.get("visibility") == "public":
            render_preview(pv)
        else:
            st.session_state.pop("preview_id", None)
            st.session_state.nav = "personaje"
            _nav_bar()
            render_personaje()
    elif st.session_state.get("call_char"):
        call_c = db.get_character(st.session_state.call_char)
        if call_c:
            render_call(call_c)
        else:
            st.session_state.call_char = None
            _nav_bar()
            render_personaje()
    elif st.session_state.creating:
        _nav_bar()
        render_create()
    else:
        _nav_bar()
        _nav = st.session_state.get("nav", "personaje")
        if _nav == "chat":
            if st.session_state.active_id:
                _active = db.get_character(st.session_state.active_id)
                if _active:
                    render_chat(_active)
                else:
                    st.session_state.active_id = None
                    st.info("Niciun chat activ. Deschide un personaj din fila 🎭 Personaje.")
            else:
                st.info("Niciun chat activ. Deschide un personaj din fila 🎭 Personaje.")
        elif _nav == "exploreaza":
            render_personaje()
        elif _nav == "amintiri":
            render_amintiri()
        elif _nav == "profil":
            render_profil()
        else:
            render_personaje()
except Exception:  # noqa
    st.markdown(
        '<div style="background:#1a1013;border:1px solid #5a2b2b;border-radius:14px;'
        'padding:1.4rem 1.5rem;text-align:center;margin-top:1.5rem">'
        '<div style="font-size:2rem;margin-bottom:.4rem">🛠️</div>'
        '<div style="font-family:Sora;font-weight:700;font-size:1.15rem;color:#ECECEC;margin-bottom:.3rem">'
        "Ne cerem scuze, avem probleme tehnice</div>"
        '<div style="color:#c98a8a;font-size:.9rem">Lucrăm deja la remediere. Te rugăm să încerci din nou în scurt timp.</div>'
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button("🔄 Reîncearcă", key="err_retry", use_container_width=True):
        st.rerun()
