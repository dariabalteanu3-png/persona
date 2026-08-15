"""Generare vocală: Fish Audio (metoda principală) + Chatterbox/F5-TTS (rezervă).

Fish Audio (model s2.1-pro-free, limba română) este metoda principală pentru
clonarea și generarea vocilor când cheia este configurată. Vocea personajului
(voice_id → mostra audio) este clonată zero-shot în fiecare cerere /v1/tts
(câmpul `references`). Clonarea folosește fish-audio-sdk (msgpack, streaming),
cu reîncercare prin REST JSON dacă SDK-ul eșuează. Dacă Fish Audio nu este
configurat sau eșuează, aplicația trece automat la Chatterbox (Hugging Face
Space) și apoi la serviciile de rezervă F5-TTS.
Biblioteca de sunete ambientale (DSP cu numpy) rămâne neschimbată.
"""

import base64
import hashlib
import io
import math
import os
import random
import re
import struct
import tempfile
import wave
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ── Config Spațiu Hugging Face ───────────────────────────────────────────────
_HF_SPACE = os.environ.get("CHATTERBOX_SPACE", "ResembleAI/Chatterbox")
_HF_TOKEN = os.environ.get("HF_TOKEN", "")  # opțional — fără token rate-limit mai strict

# Servicii de REZERVĂ pentru voce (încercate când cel principal e ocupat/căzut).
# NOTĂ: sunt tot ZeroGPU pe Hugging Face → împart aceeași cotă zilnică gratuită a contului,
# deci ajută mai ales când serviciul principal e aglomerat, nu când cota e epuizată.
_FALLBACK_SPACES = [
    s.strip() for s in os.environ.get(
        "FALLBACK_VOICE_SPACES", "mrfakename/E2-F5-TTS"
    ).split(",") if s.strip()
]

# ── Config Fish Audio (metoda principală de clonare/generare voci) ──────────
# Cheia se citește din variabila de mediu FISH_AUDIO_API_KEY (alias acceptat:
# FISH_API_KEY). În Streamlit Cloud: Settings → Secrets — NU se pune în cod/GitHub.
# Modelul implicit este s2.1-pro-free (configurabil prin FISH_AUDIO_MODEL).
_FISH_API_KEY = (
    os.environ.get("FISH_AUDIO_API_KEY", "")
    or os.environ.get("FISH_API_KEY", "")
)
_FISH_MODEL = os.environ.get("FISH_AUDIO_MODEL", "s2.1-pro-free")
_FISH_BASE_URL = os.environ.get("FISH_AUDIO_BASE_URL", "https://api.fish.audio")

_voice_samples: dict = {}   # voice_id → (sample_bytes, suffix, ref_text)
_client = None
_fallback_clients: dict = {}


class VoiceGenerationError(RuntimeError):
    """Eroare user-facing de la serviciul de generare vocală."""


def _get_client():
    """Returnează un client Gradio conectat la Space-ul Chatterbox (lazy init)."""
    global _client
    if _client is not None:
        return _client
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise VoiceGenerationError(
            "Biblioteca gradio_client nu este instalată. "
            "Adaugă 'gradio_client' în requirements.txt."
        ) from exc
    try:
        _client = Client(_HF_SPACE, token=_HF_TOKEN or None)
    except Exception as exc:
        raise VoiceGenerationError(
            "Nu mă pot conecta la serviciul de voce (Hugging Face Space). "
            f"Detalii: {exc}"
        ) from exc
    return _client


# ── Helpers de bază ──────────────────────────────────────────────────────────

def _decode_sample(sample_b64):
    if not sample_b64:
        return None
    if sample_b64.startswith("data:"):
        sample_b64 = sample_b64.split(",", 1)[-1]
    try:
        return base64.b64decode(sample_b64)
    except (ValueError, TypeError) as exc:
        raise VoiceGenerationError("Mostra audio salvată este invalidă.") from exc


def voice_id_for_sample(sample_bytes):
    """Returnează un identificator local stabil pentru o mostră de referință."""
    if not sample_bytes:
        return None
    return "cbx:" + hashlib.sha256(sample_bytes).hexdigest()[:24]


def transcribe_sample(audio_bytes, filename="audio.wav", language="ro"):
    """Transcrie o mostră audio folosind Groq/Gemini (STT).
    Această funcție este folosită OPȚIONAL — Chatterbox TTS nu necesită
    transcriere pentru clonarea vocii.
    """
    try:
        import stt as _stt
        from provider import USE_GROQ, USE_GEMINI
        if USE_GROQ or USE_GEMINI:
            return _stt.transcribe(audio_bytes, filename)
    except Exception:
        pass
    raise VoiceGenerationError(
        "Transcrierea automată nu este disponibilă. "
        "Configurează un furnizor STT (Groq sau Gemini) în setări."
    )


# ── Înregistrare voci ────────────────────────────────────────────────────────

def register_voice(voice_id, sample_b64, reference_text=None, sample_name="reference.wav"):
    """Stochează mostra vocală în memorie pentru utilizare ulterioară.

    `reference_text` este ignorat (Chatterbox nu îl necesită) — păstrat pentru
    compatibilitate cu datele existente din baza de date.
    """
    if not voice_id or not sample_b64:
        return
    sample = _decode_sample(sample_b64)
    if not sample:
        return
    suffix = Path(str(sample_name or "reference.wav")).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"
    _voice_samples[voice_id] = (sample, suffix, (reference_text or "").strip())


def register_character_voice(character):
    """Înregistrează vocea unui personaj din datele salvate în baza de date."""
    register_voice(
        character.get("voice_id"),
        character.get("voice_sample_b64"),
        character.get("voice_ref_text"),
        character.get("voice_sample_name", "reference.wav"),
    )


def forget_registered_voices(voice_ids=None):
    """Șterge mostrele vocale din memorie (după ștergerea de către utilizator)."""
    if voice_ids is None:
        _voice_samples.clear()
        return
    for voice_id in voice_ids:
        _voice_samples.pop(voice_id, None)


# ── Generare prin Fish Audio (metoda principală) ─────────────────────────────
# Fish Audio (s2.1-pro-free) clonează vocea zero-shot direct din mostra audio a
# personajului (câmpul `references`), fără un pas separat de creare a unei voci
# pe server. Clonarea se face prin fish-audio-sdk (protocol msgpack + streaming,
# recomandat de Fish Audio) cu fallback pe REST JSON dacă SDK-ul nu e disponibil.

def fish_audio_available():
    """True când cheia Fish Audio este configurată (metoda principală activă)."""
    return bool(_FISH_API_KEY and _FISH_API_KEY.strip())


def _fish_reference_audio(sample_bytes, sample_name, reference_text):
    """Construiește referința audio (zero-shot cloning) pentru /v1/tts.

    `reference_text` (transcrierea mostrei) îmbunătățește fidelitatea clonării;
    dacă lipsește, încercăm o transcriere automată (Groq/Gemini, dacă sunt
    configurate), altfel gol.
    """
    ref_text = (reference_text or "").strip()
    if not ref_text:
        try:
            ref_text = (transcribe_sample(sample_bytes, sample_name or "reference.wav") or "").strip()
        except Exception:
            ref_text = ""
    return ref_text


def _repair_wav_header(data):
    """Rescrie header-ul RIFF dacă SDK-ul a returnat un WAV cu dimensiuni
    placeholder (streaming), ca fișierul să fie valid pentru playere."""
    if not data or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return data
    pos = 12
    fmt = None
    data_start = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        csize = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cid == b"fmt " and csize >= 16:
            fmt = data[pos + 8:pos + 24]
        elif cid == b"data":
            data_start = pos + 8
            break
        pos += 8 + csize + (csize & 1)
    if fmt is None or data_start is None:
        return data
    channels = struct.unpack("<H", fmt[2:4])[0]
    sample_rate = struct.unpack("<I", fmt[4:8])[0]
    bits = struct.unpack("<H", fmt[14:16])[0] or 16
    sampwidth = bits // 8
    block_align = channels * sampwidth
    byte_rate = sample_rate * block_align
    audio = data[data_start:]
    out = bytearray()
    out += b"RIFF" + struct.pack("<I", 36 + len(audio)) + b"WAVE"
    out += b"fmt " + struct.pack("<IHHIIHH", 16, 1, channels, sample_rate,
                                 byte_rate, block_align, bits)
    out += b"data" + struct.pack("<I", len(audio))
    out += audio
    return bytes(out)


def _fish_generate_sdk(text, sample_bytes, reference_text):
    """Generează WAV cu Fish Audio via fish-audio-sdk (msgpack + streaming).

    Returnează bytes WAV sau ridică VoiceGenerationError (declanșează
    fallback-ul către REST JSON și apoi Chatterbox).
    """
    if not sample_bytes:
        raise VoiceGenerationError(
            "Mostra audio lipsește pentru clonarea vocii Fish Audio."
        )
    try:
        from fish_audio_sdk import Session
        from fish_audio_sdk.schemas import TTSRequest, ReferenceAudio
    except ImportError as exc:
        raise VoiceGenerationError(
            "Biblioteca fish-audio-sdk nu este instalată. "
            "Adaugă 'fish-audio-sdk' în requirements.txt."
        ) from exc
    try:
        session = Session(_FISH_API_KEY.strip(), base_url=_FISH_BASE_URL)
        stream = session.tts(
            TTSRequest(
                text=text,
                references=[ReferenceAudio(audio=sample_bytes, text=reference_text or "")],
                format="wav",
                chunk_length=200,
                normalize=True,
                latency="normal",
            ),
            backend=_FISH_MODEL,
        )
        wav_bytes = b"".join(stream)
    except Exception as exc:
        raise VoiceGenerationError(f"Fish Audio (SDK) a eșuat: {exc}") from exc
    if not wav_bytes:
        raise VoiceGenerationError("Fish Audio (SDK) a returnat un răspuns audio gol.")
    return _repair_wav_header(wav_bytes)


def _fish_error_message(resp):
    """Extrage un mesaj lizibil dintr-un răspuns de eroare Fish Audio."""
    try:
        data = resp.json()
    except Exception:
        return (resp.text or "")[:200] or str(resp.status_code)
    if isinstance(data, dict):
        detail = data.get("detail") or data.get("message") or data.get("error")
        if isinstance(detail, list) and detail:
            parts = []
            for d in detail:
                if isinstance(d, dict):
                    parts.append(str(d.get("msg") or d))
                else:
                    parts.append(str(d))
            return "; ".join(parts)[:200]
        return str(detail or data)[:200]
    return str(data)[:200]


def _fish_generate_json(text, sample_bytes, sample_name, reference_text):
    """Fallback REST JSON pentru Fish Audio /v1/tts (clonare zero-shot).

    Folosit dacă fish-audio-sdk nu este instalat sau eșuează. Returnează
    bytes WAV sau ridică VoiceGenerationError.
    """
    if not sample_bytes:
        raise VoiceGenerationError(
            "Mostra audio lipsește pentru clonarea vocii Fish Audio."
        )
    payload = {
        "text": text,
        "references": [{
            "audio": base64.b64encode(sample_bytes).decode("utf-8"),
            "text": reference_text or "",
        }],
        "format": "wav",
        "chunk_length": 200,
        "normalize": True,
        "latency": "normal",
    }
    headers = {
        "Authorization": f"Bearer {_FISH_API_KEY.strip()}",
        "Content-Type": "application/json",
        "model": _FISH_MODEL,
    }
    try:
        resp = requests.post(
            f"{_FISH_BASE_URL}/v1/tts",
            headers=headers,
            json=payload,
            timeout=120,
        )
    except requests.RequestException as exc:
        raise VoiceGenerationError(
            f"Nu mă pot conecta la Fish Audio: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise VoiceGenerationError(
            f"Fish Audio a returnat eroare ({resp.status_code}): "
            f"{_fish_error_message(resp)}"
        )

    ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        audio_b64 = (data or {}).get("audio")
        if not audio_b64:
            raise VoiceGenerationError(
                "Fish Audio nu a returnat audio în răspuns."
            )
        return base64.b64decode(audio_b64)
    if resp.content:
        return _repair_wav_header(resp.content)
    raise VoiceGenerationError("Fish Audio a returnat un răspuns audio gol.")


def _fish_generate(text, sample_bytes, sample_name="reference.wav", reference_text=None):
    """Generează WAV cu Fish Audio (s2.1-pro-free) — clonare zero-shot din mostră.

    Metoda principală de clonare/generare a vocii în limba română. Încearcă mai
    întâi fish-audio-sdk (msgpack), apoi REST JSON. Returnează bytes WAV sau
    ridică VoiceGenerationError (declanșează fallback-ul către Chatterbox).
    """
    ref_text = _fish_reference_audio(sample_bytes, sample_name, reference_text)
    try:
        return _fish_generate_sdk(text, sample_bytes, ref_text)
    except VoiceGenerationError as sdk_exc:
        print(f"[voice] Fish Audio SDK eșuat, încerc REST JSON: {sdk_exc}")
    return _fish_generate_json(text, sample_bytes, sample_name, ref_text)


def _call_primary(text, sample_bytes, suffix, exaggeration=0.5, cfg_weight=0.5, reference_text=None):
    """Metoda principală: Fish Audio → la eșec, Chatterbox/HF (cu rezervele lui)."""
    if fish_audio_available():
        try:
            return _fish_generate(text, sample_bytes, suffix, reference_text=reference_text)
        except VoiceGenerationError as exc:
            print(f"[voice] Fish Audio indisponibil, trec la Chatterbox: {exc}")
    return _call_chatterbox_space(text, sample_bytes, suffix, exaggeration, cfg_weight)


# ── Generare prin Hugging Face Space ─────────────────────────────────────────

def _save_temp_sample(sample_bytes, suffix=".wav"):
    """Salvează bytes audio într-un fișier temporar pe care gradio_client îl poate încărca."""
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(sample_bytes)
    tmp.close()
    return tmp.name


def _call_chatterbox_space(text, sample_bytes, suffix, exaggeration=0.5, cfg_weight=0.5):
    """Apelează Space-ul Chatterbox de pe Hugging Face și returnează WAV bytes.

    API-ul Space-ului (/generate_tts_audio) acceptă:
      text, audio_prompt_path, exaggeration, temperature, seed, cfgw, vad_trim
    și returnează calea către fișierul WAV generat (string).

    Dacă serviciul principal eșuează (ocupat/căzut), încearcă serviciile de rezervă.
    """
    from gradio_client import handle_file

    tmp_path = _save_temp_sample(sample_bytes, suffix)
    try:
        try:
            result = _predict_with_retry(tmp_path, text, exaggeration, cfg_weight)
            return _result_to_wav_bytes(result)
        except VoiceGenerationError as primary_exc:
            # Încearcă serviciile de rezervă (o singură dată fiecare, fără GPU în plus garantat).
            for space in _FALLBACK_SPACES:
                try:
                    result = _predict_fallback(space, tmp_path, text)
                    return _result_to_wav_bytes(result)
                except Exception:
                    continue
            # Toate au eșuat → păstrăm mesajul clar de la serviciul principal.
            raise primary_exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _get_fallback_client(space):
    """Client Gradio (lazy) pentru un Space de rezervă."""
    cli = _fallback_clients.get(space)
    if cli is not None:
        return cli
    from gradio_client import Client
    cli = Client(space, token=_HF_TOKEN or None, verbose=False)
    _fallback_clients[space] = cli
    return cli


def _predict_fallback(space, tmp_path, text):
    """Apel către un Space de rezervă (F5-TTS style: /predict).

    Semnătură /predict: (ref_audio, ref_text, gen_text, remove_silence).
    ref_text gol → Space-ul transcrie automat mostra. Ridică excepție dacă eșuează.
    """
    from gradio_client import handle_file
    client = _get_fallback_client(space)
    return client.predict(
        handle_file(tmp_path),  # ref_audio
        "",                       # ref_text (auto-transcriere)
        text[:300],               # gen_text
        True,                     # remove_silence
        api_name="/predict",
    )


def _predict_with_retry(tmp_path, text, exaggeration, cfg_weight, max_retries=2):
    """Apelează predict() cu reîncercare automată pentru erorile de rate-limit."""
    from gradio_client import handle_file
    import time

    last_exc = None
    for attempt in range(max_retries + 1):
        client = _get_client()
        try:
            return client.predict(
                text[:300],
                handle_file(tmp_path),
                float(exaggeration),
                0.8,
                0,
                float(cfg_weight),
                False,
                api_name="/generate_tts_audio",
            )
        except Exception as exc:
            last_exc = exc
            msg = str(exc).lower()
            is_quota = "zerogpu" in msg or "quota" in msg
            # NU reîncercăm pe eroare de cotă (nu se resetează în câteva secunde) — eșuăm rapid.
            if attempt < max_retries and not is_quota and (
                "503" in msg or "queue" in msg or "busy" in msg or "rate" in msg
            ):
                time.sleep(8)
                continue
            if is_quota:
                raise VoiceGenerationError(
                    "🔇 Vocea clonată gratuită s-a epuizat pentru azi (limita gratuită "
                    "Hugging Face — circa 5 minute de voce pe zi, împărțite de toți). "
                    "Se reîncarcă automat în ~24 de ore. Între timp poți folosi chatul "
                    "normal — cititorul de ecran îți citește mesajele."
                ) from exc
            if "rate" in msg or "queue" in msg or "busy" in msg or "503" in msg:
                raise VoiceGenerationError(
                    "Serviciul de voce e ocupat acum (mulți utilizatori). "
                    "Așteaptă 30 de secunde și încearcă din nou."
                ) from exc
            if "error" in msg or "runtime" in msg:
                raise VoiceGenerationError(
                    f"Serviciul de voce a întâmpinat o problemă. Încearcă din nou. ({exc})"
                ) from exc
            raise VoiceGenerationError(f"Eroare la generarea vocii: {exc}") from exc
    raise VoiceGenerationError(f"Eroare la generarea vocii după {max_retries} încercări: {last_exc}")


def _result_to_wav_bytes(result):
    """Convertește rezultatul Space-ului în WAV bytes.

    Space-ul poate returna:
      - un string (cale fișier WAV pe serverul Gradio) — descărcat și citit
      - un tuplu (sample_rate, numpy_array) — convertit direct
    """
    # Caz 1: string = cale fișier pe serverul Gradio → descărcăm conținutul
    if isinstance(result, str):
        return _download_gradio_file(result)

    # Caz 2: tuplu/listă
    if isinstance(result, (list, tuple)) and result:
        first = result[0]
        # 2a: primul element e o cale de fișier (ex. F5-TTS: (audio_path, ...))
        if isinstance(first, str):
            return _download_gradio_file(first)
        # 2b: (sample_rate, numpy_array)
        if len(result) >= 2:
            return _numpy_to_wav(result[1], int(first))

    raise VoiceGenerationError("Răspuns invalid de la serviciul de voce.")


def _download_gradio_file(path):
    """Descarcă un fișier rezultat de la serverul Gradio și returnează bytes.

    gradio_client.predict() descarcă automat fișierele rezultate local (/tmp/gradio/).
    Dacă fișierul există local, îl citim direct. Altfel, descărcare HTTP fallback.
    """
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()

    import requests
    space_url = "https://" + _HF_SPACE.lower().replace("/", "-") + ".hf.space"
    fname = os.path.basename(path)
    for url in [
        f"{space_url}/gradio_api/file={fname}",
        f"{space_url}/file={fname}",
        path if path.startswith("http") else None,
    ]:
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200 and resp.content:
                return resp.content
        except Exception:
            continue

    raise VoiceGenerationError(
        "Nu am putut descărca fișierul audio generat de la serviciul de voce."
    )


def _numpy_to_wav(audio_np, sample_rate):
    """Convertește un numpy array (float) în WAV bytes."""
    import numpy as np

    audio = np.asarray(audio_np).squeeze()
    if audio.dtype.kind == "f":
        audio = np.clip(audio, -1.0, 1.0)
        pcm = (audio * 32767).astype("<i2")
    else:
        pcm = audio.astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def _generate(text, voice_id, exaggeration=0.5, cfg_weight=0.5):
    """Generează audio pentru un voice_id salvat, folosind Space-ul HF."""
    info = _voice_samples.get(voice_id)
    if not info:
        raise VoiceGenerationError(
            "Vocea acestui personaj nu are o mostră salvată. Editează personajul "
            "și încarcă din nou mostra audio."
        )
    sample_bytes, suffix = info[0], info[1]
    ref_text = info[2] if len(info) > 2 else ""
    return _call_primary(text, sample_bytes, suffix, exaggeration, cfg_weight, reference_text=ref_text)


def _generate_preview(text, sample_bytes, sample_name, exaggeration=0.5, cfg_weight=0.5, reference_text=None):
    """Generează un preview direct din bytes (înainte de salvarea personajului)."""
    suffix = Path(str(sample_name or "reference.wav")).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"
    return _call_primary(text, sample_bytes, suffix, exaggeration, cfg_weight, reference_text=reference_text)


# ── Normalizare text ─────────────────────────────────────────────────────────

# Emoji → eliminăm pentru TTS
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\uFE0F\u2764]"
)
_ACTION_MAP = [
    (("râd", "rad", "hah", "haha", "chicot", "laugh", "giggl"), " "),
    (("oftea", "suspin", "sigh"), " "),
    (("șopt", "sopt", "whisper", "murmur"), " "),
    (("țip", "tip", "strig", "url", "scream", "shout", "răcnesc"), " "),
    (("plâng", "plang", "cry", "lăcrim", "lacrim"), " "),
    (("gâfâ", "gafa", "gasp", "icnesc"), " "),
    (("mormă", "morma", "mutter"), " "),
]


def _is_emotional(word):
    lower = word.lower()
    return any(key in lower for keys, _ in _ACTION_MAP for key in keys)


def extract_actions(text):
    """Extrage acțiunile fizice (non-vocale) pentru stratul ambient opțional."""
    return [
        action.strip()
        for action in re.findall(r"\*([^*]+)\*", text or "")
        if action.strip() and not _is_emotional(action)
    ]


def expressify(text):
    """Curăță markup-ul și normalizează textul românesc pentru TTS."""
    text = str(text or "")
    # Elimină formatarea markdown (păstrează cuvintele)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)   # **bold** → text
    text = re.sub(r"\*([^*]+)\*", " ", text)           # *acțiune* → spațiu
    text = re.sub(r"__([^_]+)__", r"\1", text)         # __subliniat__ → text
    text = re.sub(r"_([^_]+)_", r"\1", text)           # _italic_ → text
    text = re.sub(r"#+\s*", "", text)                   # # titluri
    text = re.sub(r"`[^`]+`", "", text)                 # `cod`
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [link](url) → text
    # Simboluri → cuvinte românești
    text = text.replace("&", " și ")
    text = text.replace("%", " la sută")
    text = re.sub(r"\.{3}", "… ", text)                # ... → pauză
    # Elimină emoji
    text = _EMOJI_RE.sub("", text)
    # Curăță spații multiple
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or "..."


# ── API public TTS ───────────────────────────────────────────────────────────

def text_to_speech(
    text,
    voice_id,
    stability=0.5,
    similarity_boost=0.75,
    style=0.0,
    expressive=True,
    tone=None,
):
    """Generează WAV cu vocea persoanei salvate (clonare Chatterbox)."""
    if not _voice_samples.get(voice_id):
        raise VoiceGenerationError(
            "Vocea acestui personaj nu are o mostră salvată. Editează personajul "
            "și încarcă din nou mostra audio."
        )
    spoken = expressify(text) if expressive else (text or "...")
    # Mapare parametri ElevenLabs-style → Chatterbox
    exaggeration = max(0.0, min(1.0, float(style) * 1.5 + 0.25))
    cfg_weight = max(0.0, min(1.0, float(similarity_boost)))
    return _generate(spoken, voice_id, exaggeration=exaggeration, cfg_weight=cfg_weight)


def text_to_speech_from_sample(text, sample_bytes, reference_text=None, sample_name="reference.wav"):
    """Generează un preview direct din mostră (înainte de salvarea personajului).
    `reference_text` (transcrierea mostrei) îmbunătățește clonarea Fish Audio,
    dar e opțional — Chatterbox nu necesită transcriere.
    """
    return _generate_preview(expressify(text), sample_bytes, sample_name, reference_text=reference_text)



def _movavg(a, k):
    """Medie mobilă rapidă O(n) (numpy) pentru netezirea spectrului."""
    import numpy as np
    k = max(1, int(k) | 1)  # impar
    if k <= 1 or a.size <= k:
        return a.copy()
    pad = k // 2
    ap = np.pad(a, pad, mode="edge")
    c = np.cumsum(np.insert(ap, 0, 0.0))
    out = (c[k:] - c[:-k]) / k
    return out[: a.size]


def _naturalize(sig, sr):
    """Înmuiere globală a sunetelor sintetizate ca să NU mai sune „electronic"/bip:
    1) plafonează vârfurile spectrale înguste (tonuri pure) la un multiplu al mediei locale;
    2) rulou blând peste ~2.6 kHz (scoate stridența digitală din înalte);
    3) o unduire lentă de amplitudine (LFO) ca fundalul să pară „viu", nu un ton electronic fix.
    Zgomotele late (ploaie, foc, vânt) rămân aproape neatinse."""
    import numpy as np
    n = int(sig.size)
    if n < 128 or not np.any(sig):
        return sig
    X = np.fft.rfft(sig)
    mag = np.abs(X)
    f = np.fft.rfftfreq(n, 1.0 / sr)
    # 1) de-tonalizare: limitează vârfurile înguste la ~3.5x media locală (~110 Hz fereastră)
    win = max(5, int(round(110.0 / (sr / n))) | 1)
    env = _movavg(mag, win) + 1e-9
    cap = env * 3.5
    over = mag > cap
    if np.any(over):
        scale = np.ones_like(mag)
        scale[over] = cap[over] / mag[over]
        X = X * scale
    # 2) rulou blând în înalte (peste 2.6 kHz)
    roll = np.ones_like(f)
    hi = f > 2600.0
    roll[hi] = np.maximum(0.20, (2600.0 / f[hi]) ** 1.1)
    X = X * roll
    sig = np.fft.irfft(X, n)
    # 3) unduire lentă de amplitudine → mai organic, mai puțin „electronic fix"
    t = np.arange(n) / sr
    lfo = 1.0 + 0.12 * np.sin(2 * np.pi * 0.4 * t + 1.3) + 0.06 * np.sin(2 * np.pi * 0.13 * t)
    sig = sig * lfo
    return sig


# ── Sinteza ambientală DSP (neschimbată) ─────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════
# Sunete REALE (Google Sound Library, CC-BY 4.0) — au prioritate față de sinteză
# ══════════════════════════════════════════════════════════════════════════
try:
    from real_sounds import REAL_SOUND_MAP as _REAL_SOUND_MAP
except Exception:  # noqa
    try:
        from streamlit_app.real_sounds import REAL_SOUND_MAP as _REAL_SOUND_MAP
    except Exception:  # noqa
        _REAL_SOUND_MAP = {}

_REAL_WAV_CACHE = {}


def _fetch_real_ogg(url):
    """Descarcă .ogg-ul real (cu cache pe disc în /tmp). Returnează bytes sau None."""
    import os as _os
    import hashlib as _hl
    d = "/tmp/persona_real_ogg"
    try:
        _os.makedirs(d, exist_ok=True)
    except Exception:  # noqa
        pass
    fp = _os.path.join(d, _hl.md5(url.encode()).hexdigest() + ".ogg")
    try:
        if _os.path.exists(fp) and _os.path.getsize(fp) > 500:
            with open(fp, "rb") as f:
                return f.read()
    except Exception:  # noqa
        pass
    try:
        import requests
        r = requests.get(url, timeout=15)
        if r.status_code == 200 and len(r.content) > 500:
            try:
                with open(fp, "wb") as f:
                    f.write(r.content)
            except Exception:  # noqa
                pass
            return r.content
    except Exception:  # noqa
        pass
    return None


def _real_sound_wav(preset, duration=10.0, sample_rate=22050):
    """Returnează bytes WAV cu o înregistrare REALĂ pentru preset, sau None (fallback la sinteză)."""
    import os as _os
    if _os.environ.get("AMBIENT_REAL", "1") != "1":
        return None
    url = _REAL_SOUND_MAP.get(preset)
    if not url:
        return None
    try:
        import numpy as np
        import soundfile as sf
    except Exception:  # noqa
        return None
    dur = max(2.0, min(float(duration), 20.0))
    key = "%s|%d|%d" % (preset, int(dur), int(sample_rate))
    if key in _REAL_WAV_CACHE:
        return _REAL_WAV_CACHE[key]
    try:
        raw = _fetch_real_ogg(url)
        if not raw:
            return None
        data, srate = sf.read(io.BytesIO(raw), dtype="float32", always_2d=False)
        if getattr(data, "ndim", 1) > 1:
            data = data.mean(axis=1)
        data = np.asarray(data, dtype=np.float32)
        if len(data) < 8:
            return None
        # resample liniar la sample_rate
        if int(srate) != int(sample_rate):
            tgt = int(round(len(data) * float(sample_rate) / float(srate)))
            if tgt > 8:
                xp = np.linspace(0.0, 1.0, len(data), endpoint=False)
                xq = np.linspace(0.0, 1.0, tgt, endpoint=False)
                data = np.interp(xq, xp, data).astype(np.float32)
        need = int(sample_rate * dur)
        if len(data) < need:
            reps = int(np.ceil(need / float(len(data))))
            data = np.tile(data, reps)
        data = data[:need]
        peak = float(np.max(np.abs(data))) or 1.0
        data = data / peak * 0.92
        fade = min(int(sample_rate * 0.08), max(1, need // 12))
        if fade > 0 and len(data) > 2 * fade:
            data[:fade] *= np.linspace(0.0, 1.0, fade)
            data[-fade:] *= np.linspace(1.0, 0.0, fade)
        pcm = np.int16(np.clip(data, -1.0, 1.0) * 32767)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(int(sample_rate))
            wf.writeframes(pcm.tobytes())
        out = buf.getvalue()
        _REAL_WAV_CACHE[key] = out
        return out
    except Exception:  # noqa
        return None



def _ambient_wav(preset, duration=12.0, sample_rate=22050):
    """DSP-based ambient synthesis using numpy. Fiecare apel sună ușor diferit (seed aleatoriu)."""
    # Prioritate: dacă avem o înregistrare REALĂ pentru acest preset, o folosim.
    _rb = _real_sound_wav(preset, duration, sample_rate)
    if _rb is not None:
        return _rb
    try:
        import numpy as np
    except ImportError:
        # Fallback minimal fără numpy
        output = io.BytesIO()
        with wave.open(output, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * int(sample_rate * duration))
        return output.getvalue()

    sr = int(sample_rate)
    dur = max(2.0, min(float(duration), 30.0))
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    rng = np.random.default_rng()   # seed aleatoriu → variație la fiecare apel

    # ── DSP helpers ──────────────────────────────────────────────────────────
    def wn(size=n):
        return rng.uniform(-1.0, 1.0, size)

    def fband(sig, lo=0, hi=None):
        """Bandpass prin FFT — fără artefacte de ringing."""
        S = np.fft.rfft(sig)
        f = np.fft.rfftfreq(len(sig), 1 / sr)
        if lo > 0:
            S[f < lo] = 0
        if hi:
            S[f > hi] = 0
        return np.fft.irfft(S, len(sig))

    def pink(lo=20, hi=8000, size=n):
        """Zgomot roz (1/f) band-limitat."""
        f = np.fft.rfftfreq(size, 1 / sr)
        with np.errstate(divide="ignore", invalid="ignore"):
            mag = np.where(f > 0, 1.0 / np.sqrt(np.maximum(f, 0.1)), 0)
        mag[f < lo] = 0
        if hi:
            mag[f > hi] = 0
        ph = rng.uniform(0, 2 * np.pi, len(f))
        return np.fft.irfft(mag * np.exp(1j * ph), size)

    def am(rate, depth=0.5, dc=1.0):
        """LFO pentru modulație de amplitudine."""
        ph = rng.uniform(0, 2 * np.pi)
        return dc + depth * np.sin(2 * np.pi * rate * t + ph)

    def sine(freq, amp=1.0):
        return amp * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))

    def norm(sig, pk=0.88):
        m = np.max(np.abs(sig))
        return sig * (pk / m) if m > 1e-9 else sig

    def footsteps(rate, lo=300, hi=4000, amp=0.6):
        """Impulsuri ritmice (pași, picături, ciocnituri)."""
        out = np.zeros(n)
        step = max(1, int(sr / rate))
        spread = max(1, step // 4)
        pos = int(rng.integers(0, step))
        while pos < n:
            blen = min(int(rng.uniform(0.04, 0.18) * sr), n - pos)
            if blen > 0:
                burst = fband(rng.uniform(-1, 1, blen), lo, hi)
                env = np.exp(-np.linspace(0, 6, blen))
                out[pos:pos + blen] += burst * env * float(rng.uniform(0.5, 1.0)) * amp
            delta = int(rng.integers(-spread, spread + 1))
            pos += step + delta
        return out

    def birds(nb=8, lo_f=1500, hi_f=5000):
        """Ciripit de păsări sintetizat."""
        out = np.zeros(n)
        for _ in range(int(rng.integers(nb // 2, nb + 1))):
            pos = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.05, 0.3) * sr), n - pos)
            if clen > 0:
                freq = float(rng.uniform(lo_f, hi_f))
                tl = np.linspace(0, clen / sr, clen)
                env = np.sin(np.pi * tl / (clen / sr))
                tone = np.sin(2 * np.pi * freq * tl + float(rng.uniform(0, 2 * np.pi)))
                tone *= 1 + 0.3 * np.sin(2 * np.pi * float(rng.uniform(5, 20)) * tl)
                out[pos:pos + clen] += tone * env * float(rng.uniform(0.15, 0.5))
        return out

    def clicks(count, lo=1000, hi=8000, min_len=0.01, max_len=0.05, amp=0.5, decay=18):
        """Clicuri/ciocniri scurte la poziții aleatorii (obiecte, taste, chei)."""
        out = np.zeros(n)
        for _ in range(count):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(min_len, max_len) * sr), n - p)
            if blen > 0:
                out[p:p + blen] += fband(rng.uniform(-1, 1, blen), lo, hi) * np.exp(-np.linspace(0, decay, blen)) * float(rng.uniform(0.4, 1.0)) * amp
        return out

    def metal_ring(freq, min_len=0.3, max_len=1.2, amp=0.3, count=3, partials=(1.0, 2.76, 5.4)):
        """Clic metalic cu parțiale — chei, bijuterii, clopoței."""
        out = np.zeros(n)
        for _ in range(count):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(min_len, max_len) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                tone = np.zeros(clen)
                for i, m in enumerate(partials):
                    tone += (0.7 ** i) * np.sin(2 * np.pi * freq * m * tl)
                env = np.exp(-np.linspace(0, 3.5, clen))
                out[p:p + clen] += tone * env * float(rng.uniform(0.5, 1.0)) * amp
        return out

    def creak_sound(freq_lo=120, freq_hi=500, count=3, min_len=0.3, max_len=1.0, amp=0.3):
        """Scârțâit de lemn/mobilier/ușă — glisare de frecvență."""
        out = np.zeros(n)
        for _ in range(count):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(min_len, max_len) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                f = float(rng.uniform(freq_lo, freq_hi))
                glide = f * (1 + float(rng.uniform(-0.5, 0.5)) * np.sin(2 * np.pi * float(rng.uniform(1, 4)) * tl))
                tone = np.sin(2 * np.pi * np.cumsum(glide) / sr)
                out[p:p + clen] += tone * np.sin(np.pi * np.linspace(0, 1, clen)) * float(rng.uniform(0.5, 1.0)) * amp
        return out

    def snap_sound(count=1, lo=2000, hi=8000, amp=0.5):
        """Trosnet/uscat — elastic, capace, nasturi."""
        out = np.zeros(n)
        for _ in range(count):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.01, 0.03) * sr), n - p)
            if blen > 0:
                out[p:p + blen] += fband(rng.uniform(-1, 1, blen), lo, hi) * np.exp(-np.linspace(0, 30, blen)) * float(rng.uniform(0.4, 1.0)) * amp
        return out

    def foley_rush(lo=300, hi=6000, rate=4.0, amp=0.3):
        """Foșnet/frecătură textilă sau de material — zgomot filtrat cu LFO."""
        return fband(pink(lo, hi), lo, hi) * am(rate, 0.45, 0.55) * amp

    def water_bubble(count=8, min_len=0.04, max_len=0.15, lo=600, hi=2500, amp=0.12):
        """Bule de apă — clicuri cu frecvență în creștere."""
        out = np.zeros(n)
        for _ in range(count):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(min_len, max_len) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                freq = float(rng.uniform(lo, hi)) * (1 + 0.8 * tl / (blen / sr))
                out[p:p + blen] += np.sin(2 * np.pi * np.cumsum(freq) / sr) * np.sin(np.pi * np.linspace(0, 1, blen)) * amp
        return out

    # ── Preseturi ────────────────────────────────────────────────────────────
    if preset == "rain":
        base = pink(100, 8000) * am(rng.uniform(0.05, 0.15), 0.1, 0.9) * 0.55
        drops = footsteps(float(rng.uniform(10, 18)), lo=1500, hi=6000, amp=0.22)
        sig = base + drops

    elif preset == "storm":
        rain = pink(100, 8000) * am(rng.uniform(0.1, 0.3), 0.25, 0.75) * 0.60
        rumble = pink(18, 160) * am(rng.uniform(0.04, 0.1), 0.45, 0.55) * 0.48
        thunder = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            pos = int(rng.integers(int(0.05 * n), n))
            tlen = min(int(rng.uniform(0.8, 2.8) * sr), n - pos)
            if tlen > 0:
                boom = pink(18, 450, tlen)
                env = np.concatenate([
                    np.linspace(0, 1, max(1, tlen // 8)),
                    np.exp(-np.linspace(0, 5, tlen - tlen // 8))
                ])[:tlen]
                thunder[pos:pos + tlen] += boom * env * float(rng.uniform(0.55, 1.0))
        sig = rain + rumble + thunder * 0.90

    elif preset == "ocean":
        base = pink(55, 4500)
        w1 = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.05, 0.10)) * t + float(rng.uniform(0, np.pi)))) ** 0.55
        w2 = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.07, 0.14)) * t + float(rng.uniform(0, np.pi)))) ** 0.55
        sig = base * (0.5 * w1 + 0.4 * w2) * 0.88

    elif preset == "fire":
        base = pink(55, 2800) * am(rng.uniform(1.5, 3.5), 0.3, 0.7) * 0.30
        crackle = footsteps(float(rng.uniform(14, 26)), lo=500, hi=5500, amp=0.45)
        pops = np.zeros(n)
        for _ in range(int(rng.integers(3, 10))):
            p = int(rng.integers(0, n))
            plen = min(int(0.055 * sr), n - p)
            if plen > 0:
                pops[p:p + plen] = rng.uniform(-1, 1, plen) * np.exp(-np.linspace(0, 8, plen))
        sig = base + crackle * 0.38 + fband(pops, 250, 6500) * 0.52

    elif preset == "wind":
        w1 = pink(140, 3800) * am(rng.uniform(0.07, 0.18), 0.55, 0.45) * 0.65
        w2 = pink(550, 5500) * am(rng.uniform(0.11, 0.28), 0.50, 0.50) * 0.28
        sig = w1 + w2

    elif preset == "blizzard":
        base = pink(180, 6500) * am(rng.uniform(0.15, 0.40), 0.65, 0.35) * 0.72
        whistle = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            fc = float(rng.uniform(900, 3200))
            chunk = fband(pink(fc - 180, fc + 180), fc - 180, fc + 180)
            whistle += chunk * am(rng.uniform(0.2, 0.5), 0.70, 0.30) * 0.30
        sig = base + whistle

    elif preset == "crickets":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            freq = float(rng.uniform(2100, 3100))
            rate = float(rng.uniform(3.5, 5.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 14
            sig += chirp * sine(freq, 0.28)

    elif preset == "river":
        base = pink(90, 5000) * am(rng.uniform(0.12, 0.28), 0.20, 0.80) * 0.55
        gurgle = footsteps(float(rng.uniform(12, 24)), lo=380, hi=3200, amp=0.18)
        sig = base + gurgle * 0.28

    elif preset == "train":
        rumble = pink(28, 320) * am(rng.uniform(0.7, 1.3), 0.15, 0.85) * 0.44
        joints = footsteps(float(rng.uniform(3.5, 5.5)), lo=55, hi=420, amp=0.72)
        hiss = pink(1100, 8500) * 0.14
        sig = rumble + joints * 0.50 + hiss

    elif preset == "forest":
        leaves = pink(650, 6500) * am(rng.uniform(0.08, 0.22), 0.40, 0.60) * 0.30
        wind_low = pink(70, 650) * am(rng.uniform(0.04, 0.10), 0.30, 0.70) * 0.14
        sig = leaves + wind_low + birds(nb=10) * 0.40

    elif preset == "forest_walk":
        leaves = pink(650, 6500) * am(rng.uniform(0.08, 0.22), 0.35, 0.65) * 0.24
        steps = footsteps(float(rng.uniform(1.2, 1.8)), lo=220, hi=5500, amp=0.58)
        twigs = np.zeros(n)
        for _ in range(int(rng.integers(2, 7))):
            p = int(rng.integers(0, n))
            tlen = min(int(0.07 * sr), n - p)
            if tlen > 0:
                snap = fband(rng.uniform(-1, 1, tlen), 700, 7500)
                twigs[p:p + tlen] += snap * np.exp(-np.linspace(0, 12, tlen)) * float(rng.uniform(0.4, 0.85))
        sig = leaves + birds(nb=8) * 0.38 + steps * 0.50 + twigs

    elif preset == "cafe":
        murmur = fband(pink(140, 3200), 170, 2600) * am(rng.uniform(0.04, 0.12), 0.15, 0.85) * 0.37
        machine = np.zeros(n)
        mpos = int(sr * float(rng.uniform(2, 6)))
        while mpos < n:
            mlen = min(int(rng.uniform(0.9, 2.6) * sr), n - mpos)
            if mlen > 0:
                hiss = fband(rng.uniform(-1, 1, mlen), 1800, 9000)
                menv = np.sin(np.pi * np.linspace(0, 1, mlen)) ** 0.5
                machine[mpos:mpos + mlen] += hiss * menv * float(rng.uniform(0.14, 0.28))
            mpos += int(sr * float(rng.uniform(8, 17)))
        clinks = footsteps(float(rng.uniform(0.14, 0.40)), lo=2200, hi=9500, amp=0.32)
        sig = murmur + machine * 0.38 + clinks * 0.22

    elif preset == "city":
        traffic = fband(pink(45, 1600), 55, 1300) * am(rng.uniform(0.04, 0.12), 0.20, 0.80) * 0.45
        hum = fband(pink(48, 130), 52, 120) * 0.17
        horns = np.zeros(n)
        for _ in range(int(rng.integers(1, 5))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(0.3, 2.0) * sr), n - p)
            if hlen > 0:
                freq = float(rng.uniform(300, 750))
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.28
                tl = np.linspace(0, hlen / sr, hlen)
                horns[p:p + hlen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.22, 0.55))
        sig = traffic + hum + horns * 0.38

    elif preset == "countryside":
        wind = pink(70, 2200) * am(rng.uniform(0.04, 0.10), 0.30, 0.70) * 0.18
        brd = birds(nb=14, lo_f=1100, hi_f=5800) * 0.52
        crk = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            freq = float(rng.uniform(2200, 2900))
            rate = float(rng.uniform(3.0, 5.2))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 16
            crk += chirp * sine(freq, 0.17)
        sig = wind + brd + crk * 0.38

    elif preset == "snow":
        faint = pink(70, 2000) * am(rng.uniform(0.03, 0.07), 0.40, 0.60) * 0.085
        sig = faint

    elif preset == "snow_walk":
        base = pink(55, 1300) * 0.07
        steps = fband(footsteps(float(rng.uniform(0.7, 1.1)), lo=100, hi=2800, amp=0.52), 80, 3200)
        sig = base + steps * 0.65

    elif preset == "station":
        crowd = fband(pink(180, 3200), 190, 2600) * am(rng.uniform(0.04, 0.10), 0.20, 0.80) * 0.31
        trains = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, int(0.7 * n)))
            tlen = min(int(rng.uniform(3, 9) * sr), n - p)
            if tlen > 0:
                rumble = pink(28, 550, tlen)
                third = tlen // 3
                env = np.concatenate([
                    np.linspace(0, 1, third),
                    np.ones(third),
                    np.linspace(1, 0, tlen - 2 * third)
                ])[:tlen]
                trains[p:p + tlen] += rumble * env * float(rng.uniform(0.24, 0.56))
        pa = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.75 * n)))
            alen = min(int(rng.uniform(3, 9) * sr), n - p)
            if alen > 0:
                pa_noise = fband(pink(280, 3500, alen), 280, 3500)
                syl_env = np.zeros(alen)
                sp = 0
                while sp < alen:
                    sdur = int(rng.uniform(0.05, 0.19) * sr)
                    se = min(sp + sdur, alen)
                    syl_env[sp:se] = float(rng.uniform(0.28, 1.0))
                    sp += sdur + int(rng.uniform(0.02, 0.11) * sr)
                frame = np.sin(np.pi * np.linspace(0, 1, alen)) ** 0.28
                pa[p:p + alen] += pa_noise * syl_env * frame * float(rng.uniform(0.17, 0.38))
        sig = crowd + trains * 0.50 + pa * 0.55

    elif preset == "heels_parquet":
        base = pink(90, 2200) * 0.048
        clicks = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(1.5, 2.2))))
        spread = max(1, step_n // 6)
        pos = int(rng.integers(0, step_n // 2))
        while pos < n:
            clen = min(int(rng.uniform(0.008, 0.032) * sr), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 1100, 9500)
                clicks[pos:pos + clen] += click * np.exp(-np.linspace(0, 18, clen)) * float(rng.uniform(0.5, 1.0))
                if float(rng.random()) < 0.62:
                    cp = pos + clen
                    crk_len = min(int(rng.uniform(0.06, 0.28) * sr), n - cp)
                    if crk_len > 0:
                        crk_f = float(rng.uniform(190, 620))
                        crk = fband(rng.uniform(-1, 1, crk_len), crk_f - 80, crk_f + 240)
                        clicks[cp:cp + crk_len] += crk * np.exp(-np.linspace(0, 9, crk_len)) * float(rng.uniform(0.24, 0.56))
            pos += step_n + int(rng.integers(-spread, spread + 1))
        sig = base + clicks * 0.75

    elif preset == "rain_window":
        base = pink(200, 6000) * am(rng.uniform(0.08, 0.18), 0.12, 0.88) * 0.48
        taps = footsteps(float(rng.uniform(20, 35)), lo=2000, hi=8000, amp=0.18)
        sig = base + taps * 0.30

    elif preset == "wind_strong":
        w1 = pink(100, 5000) * am(rng.uniform(0.12, 0.30), 0.65, 0.35) * 0.78
        w2 = pink(400, 7000) * am(rng.uniform(0.18, 0.40), 0.60, 0.40) * 0.42
        gust = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            glen = min(int(rng.uniform(0.3, 1.2) * sr), n - p)
            if glen > 0:
                gust[p:p+glen] += pink(200, 6000, glen) * np.sin(np.pi * np.linspace(0, 1, glen)) * 0.30
        sig = w1 + w2 + gust

    elif preset == "ocean_storm":
        base = pink(40, 6000) * am(rng.uniform(0.08, 0.20), 0.50, 0.50) * 0.72
        w1 = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.08, 0.16)) * t)) ** 0.45
        w2 = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.10, 0.20)) * t)) ** 0.45
        spray = pink(2000, 9000) * 0.22
        sig = base * (0.6 * w1 + 0.5 * w2) + spray

    elif preset == "birds_morning":
        wind = pink(70, 2000) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.12
        sig = wind + birds(nb=18, lo_f=1200, hi_f=6000) * 0.55

    elif preset == "birds":
        sig = birds(nb=14, lo_f=1500, hi_f=5500) * 0.50

    elif preset == "birds_lake":
        water = pink(80, 3000) * am(rng.uniform(0.05, 0.12), 0.15, 0.85) * 0.22
        sig = water + birds(nb=12, lo_f=1000, hi_f=5000) * 0.45

    elif preset == "crickets_night":
        crk = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            freq = float(rng.uniform(2000, 3200))
            rate = float(rng.uniform(3.0, 5.0))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 16
            crk += chirp * sine(freq, 0.22)
        distant = pink(40, 500) * 0.06
        sig = crk + distant

    elif preset == "night":
        crk = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            freq = float(rng.uniform(1800, 2800))
            rate = float(rng.uniform(2.5, 4.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 18
            crk += chirp * sine(freq, 0.16)
        owl = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.8 * n)))
            olen = min(int(0.4 * sr), n - p)
            if olen > 0:
                tl = np.linspace(0, olen / sr, olen)
                env = np.sin(np.pi * np.linspace(0, 1, olen))
                owl[p:p+olen] += np.sin(2 * np.pi * 220 * tl) * env * 0.12
        sig = crk + owl + pink(30, 300) * 0.04

    elif preset == "night_city":
        traffic = fband(pink(40, 1200), 50, 1000) * am(rng.uniform(0.03, 0.08), 0.20, 0.80) * 0.28
        hum = sine(50, 0.020) + sine(100, 0.012)
        distant_sirens = np.zeros(n)
        for _ in range(int(rng.integers(0, 2))):
            p = int(rng.integers(0, int(0.6 * n)))
            slen = min(int(rng.uniform(2, 5) * sr), n - p)
            if slen > 0:
                tl = np.linspace(0, slen / sr, slen)
                freq = float(rng.uniform(500, 900))
                siren = np.sin(2 * np.pi * (freq + 200 * np.sin(2 * np.pi * 0.5 * tl)) * tl)
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.3
                distant_sirens[p:p+slen] += siren * env * 0.10
        sig = traffic + hum + distant_sirens

    elif preset == "rainforest":
        rain = pink(100, 7000) * am(rng.uniform(0.06, 0.14), 0.10, 0.90) * 0.32
        brd = birds(nb=16, lo_f=800, hi_f=5000) * 0.38
        insects = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            freq = float(rng.uniform(3000, 7000))
            insects += sine(freq, 0.06) * am(float(rng.uniform(4, 8)), 0.50, 0.50)
        sig = rain + brd + insects * 0.15

    elif preset == "autumn":
        leaves = pink(500, 6000) * am(rng.uniform(0.06, 0.16), 0.35, 0.65) * 0.28
        wind = pink(70, 1500) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.12
        brd = birds(nb=6, lo_f=1000, hi_f=4500) * 0.25
        sig = leaves + wind + brd

    elif preset == "spring":
        brd = birds(nb=16, lo_f=1200, hi_f=5500) * 0.48
        water = pink(80, 2500) * am(rng.uniform(0.03, 0.08), 0.15, 0.85) * 0.10
        sig = brd + water

    elif preset == "summer":
        brd = birds(nb=12, lo_f=1500, hi_f=6000) * 0.40
        crk = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            freq = float(rng.uniform(2500, 3500))
            rate = float(rng.uniform(4, 6))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 20
            crk += chirp * sine(freq, 0.14)
        sig = brd + crk * 0.20

    elif preset == "winter":
        wind = pink(60, 2500) * am(rng.uniform(0.04, 0.10), 0.40, 0.60) * 0.14
        sig = wind + pink(30, 200) * 0.04

    elif preset == "countryside_morning":
        brd = birds(nb=18, lo_f=1000, hi_f=5500) * 0.50
        wind = pink(70, 2000) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.10
        rooster = np.zeros(n)
        p = int(rng.uniform(0.1, 0.3) * n)
        rlen = min(int(0.5 * sr), n - p)
        if rlen > 0:
            tl = np.linspace(0, rlen / sr, rlen)
            env = np.exp(-np.linspace(0, 3, rlen))
            rooster[p:p+rlen] += np.sin(2 * np.pi * 800 * tl) * env * 0.20
        sig = brd + wind + rooster

    elif preset == "countryside_night":
        crk = np.zeros(n)
        for _ in range(int(rng.integers(4, 8))):
            freq = float(rng.uniform(2000, 3000))
            rate = float(rng.uniform(3.0, 5.0))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 16
            crk += chirp * sine(freq, 0.18)
        frogs = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            flen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if flen > 0:
                tl = np.linspace(0, flen / sr, flen)
                freq = float(rng.uniform(150, 400))
                croak = np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 4, flen))
                frogs[p:p+flen] += croak * 0.12
        sig = crk + frogs + pink(30, 300) * 0.04

    elif preset == "farm":
        brd = birds(nb=10, lo_f=800, hi_f=4000) * 0.30
        moo = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.7 * n)))
            mlen = min(int(rng.uniform(0.5, 1.2) * sr), n - p)
            if mlen > 0:
                tl = np.linspace(0, mlen / sr, mlen)
                freq = float(rng.uniform(150, 250))
                env = np.sin(np.pi * np.linspace(0, 1, mlen))
                moo[p:p+mlen] += np.sin(2 * np.pi * freq * tl) * env * 0.18
        sig = brd + moo + pink(50, 500) * 0.06

    elif preset == "frogs":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            flen = min(int(rng.uniform(0.08, 0.25) * sr), n - p)
            if flen > 0:
                tl = np.linspace(0, flen / sr, flen)
                freq = float(rng.uniform(120, 350))
                croak = np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 5, flen))
                sig[p:p+flen] += croak * float(rng.uniform(0.10, 0.22))

    elif preset == "lake":
        water = pink(60, 2500) * am(rng.uniform(0.04, 0.10), 0.18, 0.82) * 0.20
        brd = birds(nb=8, lo_f=900, hi_f=4500) * 0.30
        sig = water + brd

    elif preset == "fountain":
        base = pink(2000, 8000) * am(rng.uniform(0.15, 0.30), 0.20, 0.80) * 0.30
        splash = footsteps(float(rng.uniform(15, 25)), lo=1500, hi=7000, amp=0.20)
        sig = base + splash * 0.25

    elif preset == "city_heavy":
        traffic = fband(pink(40, 1500), 45, 1200) * am(rng.uniform(0.05, 0.15), 0.25, 0.75) * 0.58
        hum = fband(pink(45, 120), 48, 110) * 0.20
        horns = np.zeros(n)
        for _ in range(int(rng.integers(2, 7))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(0.3, 1.5) * sr), n - p)
            if hlen > 0:
                freq = float(rng.uniform(300, 700))
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.25
                tl = np.linspace(0, hlen / sr, hlen)
                horns[p:p+hlen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.25, 0.50))
        sig = traffic + hum + horns * 0.42

    elif preset == "sirens":
        base = fband(pink(40, 1000), 45, 800) * 0.20
        siren = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, int(0.5 * n)))
            slen = min(int(rng.uniform(3, 8) * sr), n - p)
            if slen > 0:
                tl = np.linspace(0, slen / sr, slen)
                freq = float(rng.uniform(600, 1000))
                sweep = freq + 300 * np.sin(2 * np.pi * 0.7 * tl)
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.3
                siren[p:p+slen] += np.sin(2 * np.pi * sweep * tl) * env * 0.28
        sig = base + siren

    elif preset == "airport":
        crowd = fband(pink(150, 3000), 160, 2500) * am(rng.uniform(0.03, 0.08), 0.15, 0.85) * 0.28
        hum = sine(60, 0.015) + sine(120, 0.008)
        pa = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.7 * n)))
            alen = min(int(rng.uniform(2, 5) * sr), n - p)
            if alen > 0:
                pa_noise = fband(pink(300, 3500, alen), 300, 3500)
                syl_env = np.zeros(alen)
                sp = 0
                while sp < alen:
                    sdur = int(rng.uniform(0.05, 0.15) * sr)
                    se = min(sp + sdur, alen)
                    syl_env[sp:se] = float(rng.uniform(0.25, 0.8))
                    sp += sdur + int(rng.uniform(0.02, 0.08) * sr)
                frame = np.sin(np.pi * np.linspace(0, 1, alen)) ** 0.3
                pa[p:p+alen] += pa_noise * syl_env * frame * float(rng.uniform(0.15, 0.30))
        jet = pink(40, 500) * am(rng.uniform(0.5, 1.5), 0.10, 0.90) * 0.15
        sig = crowd + hum + pa + jet

    elif preset == "metro":
        rumble = pink(30, 400) * am(rng.uniform(0.8, 1.5), 0.12, 0.88) * 0.52
        screech = fband(pink(2000, 6000), 2500, 5500) * am(rng.uniform(0.3, 0.8), 0.40, 0.60) * 0.12
        hum = sine(80, 0.020) + sine(120, 0.012)
        sig = rumble + screech + hum

    elif preset == "bus":
        engine = pink(40, 300) * am(rng.uniform(1.0, 2.0), 0.15, 0.85) * 0.48
        rattle = pink(200, 2000) * am(rng.uniform(3, 6), 0.25, 0.75) * 0.12
        sig = engine + rattle

    elif preset == "cars":
        traffic = fband(pink(50, 2000), 55, 1500) * am(rng.uniform(0.04, 0.12), 0.20, 0.80) * 0.38
        passby = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            plen = min(int(rng.uniform(0.5, 2.0) * sr), n - p)
            if plen > 0:
                whoosh = fband(pink(100, 4000, plen), 200, 3500)
                env = np.sin(np.pi * np.linspace(0, 1, plen)) ** 0.4
                passby[p:p+plen] += whoosh * env * float(rng.uniform(0.20, 0.45))
        sig = traffic + passby

    elif preset == "cart":
        creak = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(0.8, 1.5))))
        pos = 0
        while pos < n:
            clen = min(int(rng.uniform(0.08, 0.20) * sr), n - pos)
            if clen > 0:
                freq = float(rng.uniform(80, 200))
                tl = np.linspace(0, clen / sr, clen)
                creak[pos:pos+clen] += np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 6, clen)) * 0.20
            pos += step_n + int(rng.integers(-2, 3))
        hoof = footsteps(float(rng.uniform(1.5, 2.5)), lo=200, hi=3000, amp=0.35)
        sig = creak + hoof * 0.40

    elif preset == "tractor":
        engine = pink(30, 250) * am(rng.uniform(0.5, 1.0), 0.20, 0.80) * 0.55
        rattle = pink(300, 3000) * am(rng.uniform(4, 8), 0.30, 0.70) * 0.15
        sig = engine + rattle

    elif preset == "boat":
        engine = pink(40, 350) * am(rng.uniform(0.6, 1.2), 0.15, 0.85) * 0.40
        water = pink(100, 4000) * am(rng.uniform(0.1, 0.25), 0.30, 0.70) * 0.22
        slap = footsteps(float(rng.uniform(2, 5)), lo=500, hi=4000, amp=0.20)
        sig = engine + water + slap * 0.25

    elif preset == "harbor":
        water = pink(80, 3000) * am(rng.uniform(0.05, 0.12), 0.20, 0.80) * 0.25
        horn = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.7 * n)))
            hlen = min(int(rng.uniform(1, 3) * sr), n - p)
            if hlen > 0:
                tl = np.linspace(0, hlen / sr, hlen)
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.3
                horn[p:p+hlen] += np.sin(2 * np.pi * 150 * tl) * env * 0.25
        gull = birds(nb=6, lo_f=800, hi_f=3000) * 0.20
        sig = water + horn + gull

    elif preset == "station_train_coming":
        crowd = fband(pink(150, 3000), 160, 2500) * am(rng.uniform(0.03, 0.08), 0.20, 0.80) * 0.22
        approach = np.zeros(n)
        tlen = min(int(rng.uniform(4, 10) * sr), n)
        rumble = pink(25, 500, tlen)
        env = np.linspace(0, 1, tlen) ** 0.5
        approach[:tlen] += rumble * env * 0.55
        screech = fband(pink(2000, 6000, tlen), 2500, 5500) * env * 0.10
        approach[:tlen] += screech
        sig = crowd + approach

    elif preset == "bakery":
        murmur = fband(pink(140, 2800), 170, 2200) * am(rng.uniform(0.04, 0.10), 0.15, 0.85) * 0.30
        oven = pink(50, 500) * 0.08
        clinks = footsteps(float(rng.uniform(0.2, 0.5)), lo=2000, hi=8000, amp=0.25)
        sig = murmur + oven + clinks * 0.20

    elif preset == "restaurant":
        murmur = fband(pink(130, 3000), 160, 2500) * am(rng.uniform(0.05, 0.12), 0.18, 0.82) * 0.38
        clinks = footsteps(float(rng.uniform(0.15, 0.40)), lo=2500, hi=9500, amp=0.28)
        music = sine(440, 0.015) + sine(554, 0.012) + sine(659, 0.010)
        sig = murmur + clinks * 0.22 + music

    elif preset == "store":
        murmur = fband(pink(140, 2800), 170, 2300) * am(rng.uniform(0.03, 0.08), 0.15, 0.85) * 0.25
        hum = sine(60, 0.012) + sine(120, 0.008)
        beep = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            blen = min(int(0.1 * sr), n - p)
            if blen > 0:
                beep[p:p+blen] += sine(2000, 0.15)[:blen] * np.exp(-np.linspace(0, 15, blen))
        sig = murmur + hum + beep * 0.12

    elif preset == "checkout":
        beep = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            blen = min(int(0.08 * sr), n - p)
            if blen > 0:
                beep[p:p+blen] += sine(2200, 0.18)[:blen] * np.exp(-np.linspace(0, 20, blen))
        murmur = fband(pink(140, 2500), 170, 2000) * 0.18
        sig = beep * 0.25 + murmur

    elif preset == "shopping_mall":
        murmur = fband(pink(130, 3000), 160, 2500) * am(rng.uniform(0.04, 0.10), 0.20, 0.80) * 0.32
        music = sine(523, 0.012) + sine(659, 0.010) + sine(784, 0.008)
        footsteps_mall = footsteps(float(rng.uniform(0.5, 1.5)), lo=300, hi=4000, amp=0.15)
        sig = murmur + music + footsteps_mall * 0.18

    elif preset == "shopping_bags":
        rustle = pink(2000, 8000) * am(rng.uniform(3, 8), 0.40, 0.60) * 0.25
        crinkle = footsteps(float(rng.uniform(10, 20)), lo=3000, hi=9000, amp=0.20)
        sig = rustle + crinkle * 0.30

    elif preset == "kitchen":
        hum = sine(60, 0.010) + sine(120, 0.006)
        fridge = pink(100, 800) * am(rng.uniform(0.3, 0.6), 0.10, 0.90) * 0.08
        clatter = footsteps(float(rng.uniform(0.3, 0.8)), lo=1500, hi=7000, amp=0.18)
        sig = hum + fridge + clatter * 0.15

    elif preset == "coffee_machine":
        hum = sine(60, 0.008)
        steam = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(int(0.1 * n), int(0.8 * n)))
            slen = min(int(rng.uniform(0.5, 2.0) * sr), n - p)
            if slen > 0:
                hiss = fband(rng.uniform(-1, 1, slen), 2000, 8000)
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.5
                steam[p:p+slen] += hiss * env * float(rng.uniform(0.15, 0.30))
        pour = pink(500, 4000) * am(rng.uniform(2, 5), 0.20, 0.80) * 0.12
        sig = hum + steam + pour

    elif preset == "tv":
        murmur = fband(pink(200, 4000), 250, 3500) * am(rng.uniform(0.05, 0.15), 0.30, 0.70) * 0.22
        sig = murmur + pink(50, 200) * 0.04

    elif preset == "radio":
        static = pink(1000, 8000) * am(rng.uniform(0.5, 2.0), 0.30, 0.70) * 0.15
        voice_band = fband(pink(300, 3000), 400, 2500) * am(rng.uniform(0.1, 0.3), 0.25, 0.75) * 0.20
        sig = static + voice_band

    elif preset == "typing":
        clicks = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(3, 8))))
        pos = 0
        while pos < n:
            clen = min(int(rng.uniform(0.005, 0.02) * sr), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 2000, 8000)
                clicks[pos:pos+clen] += click * np.exp(-np.linspace(0, 25, clen)) * float(rng.uniform(0.3, 0.7))
            pos += step_n + int(rng.integers(-3, 4))
        space = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            slen = min(int(0.03 * sr), n - p)
            if slen > 0:
                space[p:p+slen] += fband(rng.uniform(-1, 1, slen), 1000, 5000) * np.exp(-np.linspace(0, 12, slen)) * 0.5
        sig = clicks * 0.40 + space * 0.20

    elif preset == "vacuum":
        motor = pink(80, 4000) * am(rng.uniform(0.8, 1.5), 0.12, 0.88) * 0.42
        whine = sine(2000, 0.020) + sine(3000, 0.012)
        sig = motor + whine * 0.08

    elif preset == "washing":
        motor = pink(60, 1500) * am(rng.uniform(0.3, 0.8), 0.20, 0.80) * 0.32
        water = pink(200, 5000) * am(rng.uniform(1, 3), 0.25, 0.75) * 0.18
        thump = footsteps(float(rng.uniform(0.5, 1.5)), lo=50, hi=500, amp=0.25)
        sig = motor + water + thump * 0.15

    elif preset == "bathroom":
        fan = pink(200, 4000) * am(rng.uniform(0.5, 1.0), 0.10, 0.90) * 0.12
        drip = footsteps(float(rng.uniform(0.3, 0.8)), lo=2000, hi=6000, amp=0.15)
        echo = pink(100, 2000) * 0.04
        sig = fan + drip * 0.18 + echo

    elif preset == "water_faucet":
        flow = pink(1000, 8000) * am(rng.uniform(0.15, 0.30), 0.15, 0.85) * 0.35
        hiss = fband(pink(3000, 10000), 3500, 9000) * 0.12
        sig = flow + hiss

    elif preset == "makeup":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(10, 25))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.02, 0.08) * sr), n - p)
            if blen > 0:
                brush = fband(rng.uniform(-1, 1, blen), 2000, 7000)
                sig[p:p+blen] += brush * np.exp(-np.linspace(0, 15, blen)) * float(rng.uniform(0.08, 0.18))

    elif preset == "heels":
        base = pink(90, 2000) * 0.040
        clicks = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(1.5, 2.2))))
        spread = max(1, step_n // 6)
        pos = int(rng.integers(0, step_n // 2))
        while pos < n:
            clen = min(int(rng.uniform(0.008, 0.03) * sr), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 1200, 9000)
                clicks[pos:pos+clen] += click * np.exp(-np.linspace(0, 20, clen)) * float(rng.uniform(0.5, 1.0))
            pos += step_n + int(rng.integers(-spread, spread + 1))
        sig = base + clicks * 0.72

    elif preset == "heely":
        base = pink(80, 3000) * 0.030
        squeak = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(1.2, 2.0))))
        pos = 0
        while pos < n:
            slen = min(int(rng.uniform(0.05, 0.15) * sr), n - pos)
            if slen > 0:
                freq = float(rng.uniform(800, 2000))
                tl = np.linspace(0, slen / sr, slen)
                squeak[pos:pos+slen] += np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 10, slen)) * 0.15
            pos += step_n
        sig = base + squeak

    elif preset == "footsteps":
        sig = footsteps(float(rng.uniform(1.0, 1.8)), lo=200, hi=5000, amp=0.55)

    elif preset == "footsteps_wood":
        sig = footsteps(float(rng.uniform(1.0, 1.6)), lo=150, hi=3500, amp=0.50)

    elif preset == "footsteps_tile":
        sig = footsteps(float(rng.uniform(1.0, 1.6)), lo=400, hi=6000, amp=0.55)

    elif preset == "footsteps_outside":
        base = pink(100, 3000) * 0.06
        sig = footsteps(float(rng.uniform(1.0, 1.8)), lo=200, hi=4500, amp=0.50) + base

    elif preset == "stairs":
        sig = footsteps(float(rng.uniform(0.7, 1.2)), lo=200, hi=4000, amp=0.48)
        creak = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(100, 300))
                creak[p:p+clen] += np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 5, clen)) * 0.10
        sig += creak

    elif preset == "chips":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(15, 35))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.005, 0.02) * sr), n - p)
            if clen > 0:
                crunch = fband(rng.uniform(-1, 1, clen), 1500, 8000)
                sig[p:p+clen] += crunch * np.exp(-np.linspace(0, 30, clen)) * float(rng.uniform(0.15, 0.35))

    elif preset == "eating":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(10, 25))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.03, 0.10) * sr), n - p)
            if clen > 0:
                chew = fband(rng.uniform(-1, 1, clen), 300, 3000)
                sig[p:p+clen] += chew * np.exp(-np.linspace(0, 12, clen)) * float(rng.uniform(0.12, 0.25))

    elif preset == "drinking":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if slen > 0:
                gulp = sine(float(rng.uniform(200, 400)), 0.15)[:slen] * np.exp(-np.linspace(0, 8, slen))
                sig[p:p+slen] += gulp

    elif preset == "cooking":
        sizzle = pink(2000, 8000) * am(rng.uniform(0.5, 1.5), 0.20, 0.80) * 0.20
        clatter = footsteps(float(rng.uniform(0.3, 0.8)), lo=1500, hi=6000, amp=0.18)
        sig = sizzle + clatter * 0.15

    elif preset == "library":
        hum = sine(60, 0.006) + sine(120, 0.004)
        whisper = fband(pink(200, 2000), 250, 1500) * am(rng.uniform(0.05, 0.15), 0.15, 0.85) * 0.06
        pages = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            plen = min(int(0.08 * sr), n - p)
            if plen > 0:
                pages[p:p+plen] += fband(rng.uniform(-1, 1, plen), 2000, 7000) * np.exp(-np.linspace(0, 15, plen)) * 0.10
        sig = hum + whisper + pages

    elif preset == "office":
        hum = sine(60, 0.008) + sine(120, 0.005)
        ac = pink(100, 1500) * am(rng.uniform(0.3, 0.6), 0.10, 0.90) * 0.06
        keys = np.zeros(n)
        for _ in range(int(rng.integers(5, 15))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.005, 0.015) * sr), n - p)
            if clen > 0:
                keys[p:p+clen] += fband(rng.uniform(-1, 1, clen), 2000, 7000) * np.exp(-np.linspace(0, 25, clen)) * 0.15
        sig = hum + ac + keys

    elif preset == "hospital":
        hum = sine(60, 0.010) + sine(120, 0.006)
        beep = np.zeros(n)
        for _ in range(int(rng.integers(3, 10))):
            p = int(rng.integers(0, n))
            blen = min(int(0.05 * sr), n - p)
            if blen > 0:
                beep[p:p+blen] += sine(2000, 0.15)[:blen] * np.exp(-np.linspace(0, 25, blen))
        monitor = sine(1, 0.005) * am(1.2, 0.50, 0.50)
        sig = hum + beep * 0.10 + monitor * 0.04

    elif preset == "school":
        murmur = fband(pink(150, 3000), 180, 2500) * am(rng.uniform(0.05, 0.12), 0.20, 0.80) * 0.28
        bell = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.8 * n)))
            blen = min(int(rng.uniform(0.5, 1.5) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                env = np.exp(-np.linspace(0, 3, blen))
                bell[p:p+blen] += np.sin(2 * np.pi * 880 * tl) * env * 0.15
        sig = murmur + bell

    elif preset == "party":
        music = sine(120, 0.030) + sine(240, 0.020) + sine(480, 0.012)
        music *= am(rng.uniform(0.5, 1.5), 0.30, 0.70)
        crowd = fband(pink(150, 3000), 180, 2500) * am(rng.uniform(0.05, 0.15), 0.25, 0.75) * 0.22
        sig = music * 0.30 + crowd

    elif preset == "crowd":
        murmur = fband(pink(130, 3000), 160, 2500) * am(rng.uniform(0.04, 0.10), 0.20, 0.80) * 0.32
        clap = np.zeros(n)
        for _ in range(int(rng.integers(5, 15))):
            p = int(rng.integers(0, n))
            clen = min(int(0.03 * sr), n - p)
            if clen > 0:
                clap[p:p+clen] += fband(rng.uniform(-1, 1, clen), 1000, 5000) * np.exp(-np.linspace(0, 20, clen)) * 0.12
        sig = murmur + clap

    elif preset == "heartbeat":
        sig = np.zeros(n)
        bpm = float(rng.uniform(60, 80))
        beat_int = sr / (bpm / 60)
        pos = 0
        while pos < n:
            for freq, amp, decay in [(60, 0.40, 15), (40, 0.25, 10)]:
                blen = min(int(0.08 * sr), n - pos)
                if blen > 0:
                    tl = np.linspace(0, blen / sr, blen)
                    sig[pos:pos+blen] += np.sin(2 * np.pi * freq * tl) * np.exp(-decay * tl) * amp
            pos += int(beat_int)

    elif preset == "clock":
        sig = np.zeros(n)
        tick_int = sr // 2
        pos = 0
        while pos < n:
            clen = min(int(0.005 * sr), n - pos)
            if clen > 0:
                sig[pos:pos+clen] += fband(rng.uniform(-1, 1, clen), 3000, 8000) * np.exp(-np.linspace(0, 30, clen)) * 0.20
            pos += tick_int

    elif preset == "bell":
        sig = np.zeros(n)
        chime_int = int(rng.uniform(3.0, 4.0) * sr)
        pos = 0
        while pos < n:
            for freq, amp, decay in [(660, 0.35, 1.2), (880, 0.20, 1.0), (1320, 0.12, 0.8)]:
                blen = min(int(2.5 * sr), n - pos)
                if blen > 0:
                    tl = np.linspace(0, blen / sr, blen)
                    sig[pos:pos+blen] += np.sin(2 * np.pi * freq * tl) * np.exp(-decay * tl) * amp
            pos += chime_int

    elif preset == "breath":
        sig = np.zeros(n)
        breath_int = int(rng.uniform(3.5, 4.5) * sr)
        pos = 0
        while pos < n:
            blen = min(int(1.6 * sr), n - pos)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                env = np.sin(np.pi * np.clip(tl / 1.6, 0, 1)) ** 1.5
                breath = pink(400, 1500, size=blen) * env * 0.55
                air = fband(rng.uniform(-1, 1, blen), 400, 1500) * env * 0.20
                sig[pos:pos+blen] += breath + air
            pos += breath_int

    elif preset == "helicopter":
        rotor = sine(20, 0.50) * am(float(rng.uniform(8, 14)), 0.70, 0.30)
        engine = pink(50, 500) * 0.15
        sig = rotor * 0.40 + engine

    elif preset == "dogs":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.15, 0.5) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                freq = float(rng.uniform(300, 800))
                bark = np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 8, blen))
                sig[p:p+blen] += bark * float(rng.uniform(0.20, 0.40))

    elif preset == "cats":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            plen = min(int(rng.uniform(0.3, 0.8) * sr), n - p)
            if plen > 0:
                tl = np.linspace(0, plen / sr, plen)
                freq = float(rng.uniform(200, 500))
                purr = np.sin(2 * np.pi * freq * tl) * (0.5 + 0.5 * np.sin(2 * np.pi * 25 * tl))
                env = np.sin(np.pi * np.linspace(0, 1, plen))
                sig[p:p+plen] += purr * env * 0.15

    elif preset == "squirrels":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(6, 15))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.02, 0.08) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(2500, 5000))
                tl = np.linspace(0, clen / sr, clen)
                chirp = np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 20, clen))
                sig[p:p+clen] += chirp * float(rng.uniform(0.10, 0.22))

    elif preset == "bees":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            freq = float(rng.uniform(200, 400))
            buzz = sine(freq, 0.06) * am(float(rng.uniform(3, 8)), 0.50, 0.50)
            sig += buzz
        sig *= 0.30

    elif preset == "baby":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.5, 1.5) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(400, 900))
                cry = np.sin(2 * np.pi * freq * tl) * (1 + 0.3 * np.sin(2 * np.pi * 5 * tl))
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.5
                sig[p:p+clen] += cry * env * 0.18

    elif preset == "drilling":
        motor = pink(100, 5000) * am(float(rng.uniform(15, 25)), 0.60, 0.40) * 0.35
        whine = sine(float(rng.uniform(2000, 4000)), 0.020)
        sig = motor + whine * 0.10

    elif preset == "phone":
        sig = np.zeros(n)
        ring_freq = sine(440, 0.25) + sine(480, 0.25)
        ring_int = sr * 2
        pos = 0
        while pos < n:
            rlen = min(int(1.2 * sr), n - pos)
            if rlen > 0:
                sig[pos:pos+rlen] += ring_freq[:rlen]
            pos += ring_int

    elif preset == "pool":
        water = pink(100, 5000) * am(rng.uniform(0.1, 0.25), 0.30, 0.70) * 0.30
        splash = footsteps(float(rng.uniform(1, 3)), lo=500, hi=5000, amp=0.20)
        echo = pink(50, 2000) * 0.05
        sig = water + splash * 0.25 + echo

    elif preset == "gym":
        metal = np.zeros(n)
        for _ in range(int(rng.integers(3, 10))):
            p = int(rng.integers(0, n))
            clen = min(int(0.03 * sr), n - p)
            if clen > 0:
                metal[p:p+clen] += fband(rng.uniform(-1, 1, clen), 1000, 6000) * np.exp(-np.linspace(0, 25, clen)) * 0.25
        breath = fband(pink(200, 1500), 250, 1200) * am(rng.uniform(0.3, 0.8), 0.30, 0.70) * 0.10
        sig = metal + breath

    elif preset == "ice":
        creak = np.zeros(n)
        for _ in range(int(rng.integers(4, 12))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.1, 0.4) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(100, 400))
                creak[p:p+clen] += np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 4, clen)) * 0.15
        crack = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            clen = min(int(0.05 * sr), n - p)
            if clen > 0:
                crack[p:p+clen] += fband(rng.uniform(-1, 1, clen), 2000, 8000) * np.exp(-np.linspace(0, 20, clen)) * 0.20
        sig = creak + crack

    elif preset == "shooting":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            clen = min(int(0.03 * sr), n - p)
            if clen > 0:
                boom = pink(50, 3000, clen) * np.exp(-np.linspace(0, 30, clen)) * 0.45
                sig[p:p+clen] += boom

    elif preset == "explosion":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), n))
            clen = min(int(rng.uniform(0.5, 2.0) * sr), n - p)
            if clen > 0:
                boom = pink(20, 2000, clen) * np.exp(-np.linspace(0, 3, clen)) * 0.65
                rumble = pink(20, 100, clen) * np.exp(-np.linspace(0, 1.5, clen)) * 0.40
                sig[p:p+clen] += boom + rumble

    elif preset == "factory":
        machine = pink(80, 4000) * am(rng.uniform(1, 3), 0.20, 0.80) * 0.30
        metal = np.zeros(n)
        for _ in range(int(rng.integers(5, 15))):
            p = int(rng.integers(0, n))
            clen = min(int(0.02 * sr), n - p)
            if clen > 0:
                metal[p:p+clen] += fband(rng.uniform(-1, 1, clen), 1500, 7000) * np.exp(-np.linspace(0, 20, clen)) * 0.15
        hum = sine(100, 0.015) + sine(200, 0.010)
        sig = machine + metal + hum

    elif preset == "construction":
        hammer = np.zeros(n)
        for _ in range(int(rng.integers(5, 15))):
            p = int(rng.integers(0, n))
            clen = min(int(0.01 * sr), n - p)
            if clen > 0:
                hammer[p:p+clen] += fband(rng.uniform(-1, 1, clen), 500, 5000) * np.exp(-np.linspace(0, 25, clen)) * 0.35
        saw = pink(2000, 8000) * am(float(rng.uniform(2, 5)), 0.40, 0.60) * 0.12
        sig = hammer + saw

    elif preset == "arcade":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(10, 25))):
            p = int(rng.integers(0, n))
            clen = min(int(0.05 * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(800, 3000))
                tl = np.linspace(0, clen / sr, clen)
                env = np.exp(-np.linspace(0, 12, clen))
                sig[p:p+clen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.10, 0.25))

    elif preset == "dice":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            clen = min(int(0.03 * sr), n - p)
            if clen > 0:
                sig[p:p+clen] += fband(rng.uniform(-1, 1, clen), 1500, 6000) * np.exp(-np.linspace(0, 20, clen)) * 0.25

    elif preset == "lab":
        hum = sine(60, 0.010) + sine(120, 0.006)
        vent = pink(100, 2000) * am(rng.uniform(0.3, 0.6), 0.10, 0.90) * 0.08
        beep = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            blen = min(int(0.03 * sr), n - p)
            if blen > 0:
                beep[p:p+blen] += sine(1500, 0.12)[:blen] * np.exp(-np.linspace(0, 25, blen))
        sig = hum + vent + beep * 0.08

    elif preset == "desert":
        heat = pink(200, 4500) * am(rng.uniform(0.08, 0.18), 0.45, 0.55) * 0.22
        shimmer = fband(pink(3000, 9000), 3500, 8000) * am(rng.uniform(0.5, 1.2), 0.60, 0.40) * 0.08
        gust = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            glen = min(int(rng.uniform(0.4, 1.5) * sr), n - p)
            if glen > 0:
                gust[p:p+glen] += pink(150, 3500, glen) * np.sin(np.pi * np.linspace(0, 1, glen)) * 0.20
        sig = heat + shimmer + gust

    elif preset == "waterfall":
        roar = pink(40, 6000) * am(rng.uniform(0.5, 1.5), 0.15, 0.85) * 0.60
        mid = fband(pink(200, 4000), 300, 3500) * am(rng.uniform(2, 5), 0.20, 0.80) * 0.35
        spray = pink(2000, 9000) * 0.18
        sig = roar + mid + spray

    elif preset == "stream":
        base = pink(120, 5000) * am(rng.uniform(0.2, 0.5), 0.30, 0.70) * 0.40
        gurgle = footsteps(float(rng.uniform(18, 34)), lo=450, hi=3800, amp=0.20)
        sig = base + gurgle * 0.30

    elif preset == "rain_roof":
        base = pink(150, 6000) * am(rng.uniform(0.05, 0.12), 0.10, 0.90) * 0.52
        taps = footsteps(float(rng.uniform(28, 45)), lo=2500, hi=8500, amp=0.22)
        rumble = pink(30, 200) * 0.08
        sig = base + taps * 0.32 + rumble

    elif preset == "thunder_roll":
        low = pink(25, 180) * am(rng.uniform(0.06, 0.15), 0.40, 0.60) * 0.30
        roll = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(int(0.05 * n), n))
            tlen = min(int(rng.uniform(1.5, 4.0) * sr), n - p)
            if tlen > 0:
                boom = pink(20, 300, tlen)
                env = np.concatenate([
                    np.linspace(0, 1, max(1, tlen // 6)),
                    np.exp(-np.linspace(0, 2.5, tlen - tlen // 6))
                ])[:tlen]
                roll[p:p + tlen] += boom * env * float(rng.uniform(0.30, 0.55))
        sig = low + roll

    elif preset == "wind_chimes":
        base = pink(60, 2500) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.08
        chimes = np.zeros(n)
        notes = [660, 784, 880, 990, 1175, 1320]
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.4, 1.5) * sr), n - p)
            if clen > 0:
                freq = float(rng.choice(notes)) * float(rng.uniform(0.98, 1.02))
                tl = np.linspace(0, clen / sr, clen)
                tone = np.sin(2 * np.pi * freq * tl) + 0.4 * np.sin(2 * np.pi * freq * 2.76 * tl)
                env = np.exp(-np.linspace(0, 2.5, clen))
                chimes[p:p + clen] += tone * env * float(rng.uniform(0.12, 0.30))
        sig = base + chimes

    elif preset == "church_bells":
        base = pink(50, 1000) * 0.05
        bells = np.zeros(n)
        freqs = [(130, 2.1), (164, 2.7), (196, 3.1)]
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.7 * n)))
            blen = min(int(rng.uniform(2, 5) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                f, decay = rng.choice(freqs)
                tone = (np.sin(2 * np.pi * f * tl) + 0.3 * np.sin(2 * np.pi * f * 2.4 * tl))
                env = np.exp(-decay * tl)
                bells[p:p + blen] += tone * env * float(rng.uniform(0.10, 0.20))
        sig = base + bells

    elif preset == "temple_gong":
        base = pink(50, 900) * 0.04
        gongs = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.15 * n), int(0.8 * n)))
            glen = min(int(rng.uniform(3, 7) * sr), n - p)
            if glen > 0:
                tl = np.linspace(0, glen / sr, glen)
                freq = float(rng.uniform(110, 150))
                tone = (np.sin(2 * np.pi * freq * tl) + 0.5 * np.sin(2 * np.pi * freq * 2.76 * tl)
                        + 0.3 * np.sin(2 * np.pi * freq * 5.4 * tl))
                env = np.exp(-np.linspace(0, 0.9, glen))
                gongs[p:p + glen] += tone * env * float(rng.uniform(0.15, 0.28))
        drone = sine(55, 0.02) + sine(82, 0.012)
        sig = base + gongs + drone

    elif preset == "meadow":
        wind = pink(70, 2000) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.12
        brd = birds(nb=10, lo_f=1500, hi_f=5500) * 0.35
        insects = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            freq = float(rng.uniform(3000, 6500))
            insects += sine(freq, 0.04) * am(float(rng.uniform(4, 9)), 0.50, 0.50)
        sig = wind + brd + insects * 0.12

    elif preset == "night_forest":
        crk = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            freq = float(rng.uniform(2000, 3000))
            rate = float(rng.uniform(2.5, 4.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 16
            crk += chirp * sine(freq, 0.16)
        owl = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.8 * n)))
            olen = min(int(0.5 * sr), n - p)
            if olen > 0:
                tl = np.linspace(0, olen / sr, olen)
                env = np.sin(np.pi * np.linspace(0, 1, olen))
                owl[p:p+olen] += np.sin(2 * np.pi * 240 * tl) * env * 0.10
        leaves = pink(600, 5000) * am(rng.uniform(0.05, 0.12), 0.30, 0.70) * 0.10
        sig = crk + owl + leaves + pink(25, 250) * 0.05

    elif preset == "underwater":
        deep = pink(30, 1200) * am(rng.uniform(0.05, 0.12), 0.40, 0.60) * 0.28
        bubbles = np.zeros(n)
        for _ in range(int(rng.integers(6, 14))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.05, 0.15) * sr), n - p)
            if blen > 0:
                freq = float(rng.uniform(600, 2500))
                tl = np.linspace(0, blen / sr, blen)
                env = np.sin(np.pi * np.linspace(0, 1, blen))
                bubbles[p:p+blen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.06, 0.14))
        echo = fband(pink(100, 3000), 200, 2500) * 0.04
        sig = deep + bubbles + echo

    elif preset == "space":
        drone = sine(48, 0.03) + sine(72, 0.02) + sine(110, 0.012)
        swells = np.zeros(n)
        for _ in range(int(rng.integers(2, 4))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(3, 8) * sr), n - p)
            if slen > 0:
                tl = np.linspace(0, slen / sr, slen)
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.7
                swells[p:p+slen] += pink(200, 3000, slen) * env * float(rng.uniform(0.06, 0.14))
        twinkle = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            tlen = min(int(0.02 * sr), n - p)
            if tlen > 0:
                twinkle[p:p+tlen] += sine(float(rng.uniform(800, 2500)), 0.06)[:tlen] * np.exp(-np.linspace(0, 30, tlen))
        sig = drone + swells + twinkle * 0.5

    elif preset == "cyberpunk":
        base = fband(pink(45, 1500), 50, 1200) * am(rng.uniform(0.04, 0.10), 0.25, 0.75) * 0.35
        hum = sine(50, 0.02) + sine(100, 0.012) + sine(200, 0.008)
        bleeps = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.03, 0.12) * sr), n - p)
            if blen > 0:
                freq = float(rng.uniform(600, 2400))
                env = np.exp(-np.linspace(0, 18, blen))
                bleeps[p:p+blen] += np.sin(2 * np.pi * freq * np.linspace(0, blen / sr, blen)) * env * float(rng.uniform(0.10, 0.22))
        sig = base + hum + bleeps

    elif preset == "casino":
        murmur = fband(pink(150, 3000), 170, 2500) * am(rng.uniform(0.04, 0.10), 0.20, 0.80) * 0.22
        jingles = np.zeros(n)
        for _ in range(int(rng.integers(8, 18))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.02, 0.08) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                freq = float(rng.uniform(1800, 4000))
                jingles[p:p+blen] += (np.sin(2 * np.pi * freq * tl) + 0.5 * np.sin(2 * np.pi * freq * 1.5 * tl)) * np.exp(-np.linspace(0, 20, blen)) * 0.10
        sig = murmur + jingles

    elif preset == "market":
        murmur = fband(pink(150, 3200), 180, 2600) * am(rng.uniform(0.05, 0.14), 0.25, 0.75) * 0.32
        calls = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.4, 1.2) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(300, 900))
                tl = np.linspace(0, clen / sr, clen)
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.3
                calls[p:p+clen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.12, 0.25))
        rustle = footsteps(float(rng.uniform(3, 8)), lo=2000, hi=8000, amp=0.12)
        sig = murmur + calls + rustle

    elif preset == "typewriter":
        clicks = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(5, 11))))
        pos = int(rng.integers(0, step_n))
        while pos < n:
            clen = min(int(rng.uniform(0.01, 0.03) * sr), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 800, 4500)
                clicks[pos:pos+clen] += click * np.exp(-np.linspace(0, 22, clen)) * float(rng.uniform(0.35, 0.7))
            pos += step_n + int(rng.integers(-2, 3))
        ding = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.2 * n), int(0.8 * n)))
            dlen = min(int(0.6 * sr), n - p)
            if dlen > 0:
                tl = np.linspace(0, dlen / sr, dlen)
                ding[p:p+dlen] += (np.sin(2 * np.pi * 2000 * tl) + 0.4 * np.sin(2 * np.pi * 3000 * tl)) * np.exp(-np.linspace(0, 5, dlen)) * 0.12
        sig = clicks * 0.6 + ding

    elif preset == "printer":
        motor = pink(60, 2000) * am(rng.uniform(3, 7), 0.30, 0.70) * 0.25
        feed = footsteps(float(rng.uniform(6, 12)), lo=800, hi=4000, amp=0.15)
        hum = sine(60, 0.008) + sine(120, 0.005)
        sig = motor + feed + hum

    elif preset == "fan":
        hum = sine(60, 0.015) + sine(120, 0.010) + sine(180, 0.006)
        whir = pink(150, 4000) * am(float(rng.uniform(8, 14)), 0.20, 0.80) * 0.15
        sig = hum + whir

    elif preset == "air_conditioning":
        base = pink(90, 1800) * am(rng.uniform(0.2, 0.5), 0.10, 0.90) * 0.16
        hiss = fband(pink(2000, 7000), 2500, 6000) * 0.06
        hum = sine(60, 0.012) + sine(120, 0.008)
        sig = base + hiss + hum

    elif preset == "cash_register":
        base = murmur = fband(pink(150, 2500), 180, 2000) * 0.15
        dings = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            dlen = min(int(0.15 * sr), n - p)
            if dlen > 0:
                tl = np.linspace(0, dlen / sr, dlen)
                dings[p:p+dlen] += (np.sin(2 * np.pi * 2600 * tl) + 0.3 * np.sin(2 * np.pi * 3900 * tl)) * np.exp(-np.linspace(0, 12, dlen)) * 0.16
        sig = base + dings

    elif preset == "dishwasher":
        water = pink(300, 5000) * am(rng.uniform(1, 3), 0.20, 0.80) * 0.25
        spray = footsteps(float(rng.uniform(4, 8)), lo=1200, hi=6000, amp=0.12)
        motor = pink(50, 500) * am(rng.uniform(0.3, 0.7), 0.10, 0.90) * 0.12
        sig = water + spray + motor

    elif preset == "shower":
        water = pink(800, 8000) * am(rng.uniform(0.2, 0.5), 0.20, 0.80) * 0.42
        splash = footsteps(float(rng.uniform(5, 10)), lo=1500, hi=6000, amp=0.12)
        echo = pink(100, 2000) * 0.06
        sig = water + splash + echo

    elif preset == "snore":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(1.5, 3.5) * sr), n - p)
            if slen > 0:
                tl = np.linspace(0, slen / sr, slen)
                freq = float(rng.uniform(90, 180))
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.4
                snore = np.sin(2 * np.pi * freq * tl) * env * 0.22
                rattle = fband(rng.uniform(-1, 1, slen), 300, 1500) * env * 0.06
                sig[p:p+slen] += snore + rattle
        sig += pink(40, 400) * 0.05

    elif preset == "applause":
        claps = np.zeros(n)
        for _ in range(int(rng.integers(40, 80))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.01, 0.04) * sr), n - p)
            if clen > 0:
                claps[p:p+clen] += fband(rng.uniform(-1, 1, clen), 900, 6000) * np.exp(-np.linspace(0, 25, clen)) * float(rng.uniform(0.08, 0.18))
        murmur = fband(pink(140, 3000), 160, 2500) * am(rng.uniform(0.1, 0.3), 0.30, 0.70) * 0.12
        sig = claps + murmur

    elif preset == "cheering":
        murmur = fband(pink(150, 3000), 180, 2600) * am(rng.uniform(0.05, 0.15), 0.30, 0.70) * 0.25
        hoorays = np.zeros(n)
        for _ in range(int(rng.integers(4, 9))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(0.5, 1.5) * sr), n - p)
            if hlen > 0:
                freq = float(rng.uniform(300, 600))
                tl = np.linspace(0, hlen / sr, hlen)
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.4
                hoorays[p:p+hlen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.15, 0.30))
        claps = np.zeros(n)
        for _ in range(int(rng.integers(20, 50))):
            p = int(rng.integers(0, n))
            clen = min(int(0.03 * sr), n - p)
            if clen > 0:
                claps[p:p+clen] += fband(rng.uniform(-1, 1, clen), 900, 5500) * np.exp(-np.linspace(0, 20, clen)) * 0.08
        sig = murmur + hoorays + claps

    elif preset == "motorcycle":
        engine = pink(30, 500) * am(float(rng.uniform(8, 18)), 0.40, 0.60) * 0.50
        whine = sine(float(rng.uniform(1200, 2500)), 0.015)
        passby = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            plen = min(int(rng.uniform(1, 3) * sr), n - p)
            if plen > 0:
                whoosh = pink(60, 2000, plen)
                env = np.sin(np.pi * np.linspace(0, 1, plen)) ** 0.5
                passby[p:p+plen] += whoosh * env * float(rng.uniform(0.15, 0.35))
        sig = engine * 0.7 + whine + passby

    elif preset == "bicycle":
        base = pink(60, 1500) * 0.05
        bells = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            blen = min(int(0.3 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                env = np.exp(-np.linspace(0, 6, blen))
                bells[p:p+blen] += (np.sin(2 * np.pi * 2500 * tl) + 0.4 * np.sin(2 * np.pi * 3200 * tl)) * env * 0.20
        creak = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(300, 700))
                creak[p:p+clen] += np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 8, clen)) * 0.08
        sig = base + bells + creak

    elif preset == "organ":
        drone = sine(65, 0.06) + sine(98, 0.05) + sine(130, 0.04) + sine(196, 0.02)
        chord = np.zeros(n)
        notes_freq = [130.8, 164.8, 196.0, 261.6]
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(int(0.1 * n), int(0.7 * n)))
            clen = min(int(rng.uniform(3, 7) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                f = float(rng.choice(notes_freq))
                tone = (np.sin(2 * np.pi * f * tl) + 0.5 * np.sin(2 * np.pi * f * 2 * tl)
                        + 0.25 * np.sin(2 * np.pi * f * 3 * tl))
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.5
                chord[p:p+clen] += tone * env * 0.08
        reverb = fband(pink(50, 1500), 80, 1200) * 0.03
        sig = drone + chord + reverb

    elif preset == "gong":
        gongs = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            glen = min(int(rng.uniform(2, 5) * sr), n - p)
            if glen > 0:
                tl = np.linspace(0, glen / sr, glen)
                freq = float(rng.uniform(160, 260))
                tone = (np.sin(2 * np.pi * freq * tl) + 0.6 * np.sin(2 * np.pi * freq * 2.7 * tl)
                        + 0.4 * np.sin(2 * np.pi * freq * 5.3 * tl))
                env = np.exp(-np.linspace(0, 1.2, glen))
                gongs[p:p+glen] += tone * env * float(rng.uniform(0.12, 0.22))
        silence = pink(40, 400) * 0.02
        sig = gongs + silence

    # ══════════════ SUNETE NOI — FAMILII FIZICE ═══════════════════════════════
    # ── 🚪 Casă / obiecte ─────────────────────────────────────────────────────
    elif preset == "keys_jingle":
        base = foley_rush(2500, 9500, float(rng.uniform(3, 8)), 0.10)
        sig = metal_ring(float(rng.uniform(2500, 6500)), 0.03, 0.15, 0.28, int(rng.integers(4, 10))) + base

    elif preset == "keys_rummage":
        base = foley_rush(1500, 8000, float(rng.uniform(4, 9)), 0.16)
        sig = metal_ring(float(rng.uniform(2000, 6000)), 0.02, 0.12, 0.22, int(rng.integers(8, 16))) + base

    elif preset == "keys_drop":
        sig = clicks(int(rng.integers(4, 9)), 1800, 8500, 0.008, 0.035, 0.45)
        sig += metal_ring(float(rng.uniform(2200, 6500)), 0.03, 0.2, 0.3, int(rng.integers(3, 6)))
        sig += fband(pink(50, 800), 60, 700) * np.exp(-np.linspace(0, 9, n)) * 0.2

    elif preset == "keys_put":
        sig = clicks(int(rng.integers(3, 7)), 2000, 9000, 0.01, 0.04, 0.4) + metal_ring(float(rng.uniform(3000, 7000)), 0.05, 0.2, 0.18, 3)

    elif preset == "door_handle":
        sig = clicks(2, 800, 4000, 0.01, 0.04, 0.5) + fband(pink(200, 2500), 300, 2000) * np.exp(-np.linspace(0, 15, n)) * 0.2

    elif preset == "door_balcony":
        sig = foley_rush(300, 3500, float(rng.uniform(2.5, 5)), 0.3)
        sig += metal_ring(float(rng.uniform(1500, 3500)), 0.04, 0.25, 0.18, 3)
        sig += creak_sound(150, 600, 1, 0.3, 0.8, 0.2)

    elif preset == "door_room":
        sig = creak_sound(120, 550, int(rng.integers(1, 3)), 0.25, 0.8, 0.3)
        sig += fband(pink(70, 1400), 80, 1200) * np.exp(-np.linspace(0, 9, n)) * 0.4
        sig += clicks(1, 500, 3000, 0.01, 0.04, 0.35)

    elif preset == "door_creak_open":
        sig = creak_sound(120, 500, int(rng.integers(2, 5)), 0.4, 1.2, 0.35)
        sig += foley_rush(200, 3000, float(rng.uniform(2, 5)), 0.12)

    elif preset == "door_close":
        sig = fband(pink(60, 1200), 80, 1000) * np.exp(-np.linspace(0, 8, n)) * 0.5
        sig += clicks(2, 400, 3000, 0.01, 0.05, 0.45)

    elif preset == "door_slam":
        sig = fband(pink(40, 900), 50, 700) * np.exp(-np.linspace(0, 6, n)) * 0.75
        sig += clicks(2, 300, 2500, 0.01, 0.06, 0.5)

    elif preset == "door_fridge":
        sig = fband(pink(60, 1500), 70, 1200) * np.exp(-np.linspace(0, 9, n)) * 0.5
        sig += clicks(2, 600, 4000, 0.008, 0.03, 0.35)
        sig += metal_ring(float(rng.uniform(1500, 3000)), 0.05, 0.25, 0.15, 2)

    elif preset == "door_oven":
        sig = fband(pink(100, 2200), 150, 1800) * np.exp(-np.linspace(0, 8, n)) * 0.45
        sig += clicks(3, 900, 5000, 0.008, 0.04, 0.4) + creak_sound(300, 700, 1, 0.2, 0.5, 0.15)

    elif preset == "door_lift":
        sig = metal_ring(float(rng.uniform(1200, 2600)), 0.05, 0.3, 0.2, 3) + clicks(2, 1500, 6000, 0.01, 0.04, 0.35)
        sig += fband(pink(60, 800), 70, 700) * np.exp(-np.linspace(0, 10, n)) * 0.3

    elif preset == "door_train":
        sig = fband(pink(200, 4000), 300, 3500) * np.exp(-np.linspace(0, 6, n)) * 0.5
        sig += clicks(3, 800, 4000, 0.01, 0.05, 0.5) + creak_sound(200, 600, 1, 0.3, 0.8, 0.2)

    elif preset == "door_plane":
        sig = fband(pink(60, 1500), 80, 1200) * np.exp(-np.linspace(0, 8, n)) * 0.45
        sig += clicks(2, 500, 3500, 0.01, 0.05, 0.4) + foley_rush(300, 2500, 3, 0.12)

    elif preset == "door_cabinet":
        sig = fband(pink(150, 2500), 200, 2000) * np.exp(-np.linspace(0, 8, n)) * 0.4
        sig += clicks(2, 1000, 5000, 0.008, 0.03, 0.35) + creak_sound(400, 900, 1, 0.15, 0.4, 0.15)

    elif preset == "door_bathroom":
        sig = creak_sound(150, 600, int(rng.integers(1, 3)), 0.3, 0.9, 0.3)
        sig += fband(pink(80, 1500), 90, 1200) * np.exp(-np.linspace(0, 9, n)) * 0.4
        sig += clicks(1, 500, 3000, 0.01, 0.04, 0.35)

    elif preset == "floor_creak":
        sig = creak_sound(90, 350, int(rng.integers(2, 5)), 0.3, 1.1, 0.35)
        sig += pink(40, 300) * 0.03

    elif preset == "furniture_move":
        sig = foley_rush(100, 2000, float(rng.uniform(1.5, 4)), 0.35) + creak_sound(80, 300, int(rng.integers(2, 4)), 0.4, 1.2, 0.3)

    elif preset == "chair_pull":
        sig = foley_rush(150, 3000, float(rng.uniform(2, 5)), 0.3) + creak_sound(150, 500, int(rng.integers(1, 3)), 0.2, 0.6, 0.3)

    elif preset == "chair_push":
        sig = foley_rush(200, 3000, float(rng.uniform(2, 5)), 0.3)
        sig += clicks(2, 400, 2500, 0.01, 0.06, 0.4)

    elif preset == "table_touch":
        sig = clicks(1, 500, 3000, 0.01, 0.05, 0.4) + fband(pink(100, 2000), 150, 1500) * np.exp(-np.linspace(0, 14, n)) * 0.2

    elif preset == "object_fall":
        sig = fband(pink(50, 1500), 60, 1200) * np.exp(-np.linspace(0, 7, n)) * 0.55
        sig += clicks(2, 500, 3500, 0.01, 0.05, 0.4)

    elif preset == "object_break":
        sig = clicks(int(rng.integers(6, 14)), 2000, 9500, 0.01, 0.04, 0.5) + metal_ring(float(rng.uniform(3000, 8000)), 0.05, 0.3, 0.25, 5)
        sig += fband(pink(100, 3000), 150, 2500) * np.exp(-np.linspace(0, 5, n)) * 0.4

    elif preset == "glass_put":
        sig = clicks(2, 2000, 9000, 0.005, 0.03, 0.35) + metal_ring(float(rng.uniform(3000, 7000)), 0.05, 0.25, 0.2, 3)
        sig += fband(pink(80, 1000), 100, 800) * np.exp(-np.linspace(0, 12, n)) * 0.2

    elif preset == "box_open":
        sig = foley_rush(300, 3000, float(rng.uniform(2, 4)), 0.3) + clicks(2, 500, 3000, 0.01, 0.05, 0.4)
        sig += creak_sound(200, 700, 1, 0.2, 0.6, 0.2)

    elif preset == "box_close":
        sig = fband(pink(100, 2000), 150, 1500) * np.exp(-np.linspace(0, 8, n)) * 0.45
        sig += clicks(2, 600, 3500, 0.008, 0.04, 0.4)

    elif preset == "packaging":
        sig = foley_rush(800, 8000, float(rng.uniform(4, 8)), 0.3) + snap_sound(int(rng.integers(2, 5)), 2000, 7000, 0.35)

    elif preset == "tape_peel":
        sig = foley_rush(1200, 7000, float(rng.uniform(3, 6)), 0.28) + snap_sound(2, 1500, 6000, 0.3)

    elif preset == "pen_write":
        sig = clicks(int(rng.integers(6, 16)), 800, 3500, 0.01, 0.03, 0.35)
        sig += foley_rush(800, 4000, float(rng.uniform(6, 12)), 0.12)

    elif preset == "paper_rustle":
        sig = foley_rush(1500, 8000, float(rng.uniform(4, 9)), 0.3) + clicks(int(rng.integers(3, 8)), 2000, 8000, 0.01, 0.03, 0.2)

    elif preset == "paperclip":
        sig = metal_ring(float(rng.uniform(4000, 8000)), 0.03, 0.12, 0.25, int(rng.integers(2, 5)))

    elif preset == "rubber_band":
        sig = snap_sound(int(rng.integers(2, 5)), 1000, 5000, 0.4) + foley_rush(500, 3500, 4, 0.15)

    # ── 👕 Haine ──────────────────────────────────────────────────────────────
    elif preset == "fabric_rustle":
        sig = foley_rush(400, 6000, float(rng.uniform(3, 8)), 0.3)

    elif preset == "clothes_put":
        sig = foley_rush(300, 5000, float(rng.uniform(2, 5)), 0.35) + clicks(2, 800, 4000, 0.01, 0.05, 0.25)

    elif preset == "jacket_zip":
        sig = foley_rush(800, 7000, float(rng.uniform(2, 4)), 0.25) + clicks(int(rng.integers(2, 5)), 1500, 8000, 0.01, 0.03, 0.4)

    elif preset == "zipper":
        sig = foley_rush(1200, 8000, float(rng.uniform(3, 6)), 0.28)
        sig += clicks(int(rng.integers(2, 5)), 1500, 8000, 0.005, 0.03, 0.4)

    elif preset == "button":
        sig = snap_sound(int(rng.integers(1, 3)), 1500, 6000, 0.4) + clicks(1, 800, 4000, 0.01, 0.03, 0.3)

    elif preset == "belt_buckle":
        sig = metal_ring(float(rng.uniform(2500, 5000)), 0.05, 0.25, 0.25, int(rng.integers(2, 4))) + clicks(1, 1000, 4000, 0.01, 0.03, 0.35)

    elif preset == "socks":
        sig = foley_rush(400, 4500, float(rng.uniform(2, 5)), 0.25)

    elif preset == "gloves_put":
        sig = foley_rush(300, 5000, float(rng.uniform(2, 4)), 0.3) + clicks(1, 600, 3000, 0.01, 0.04, 0.25)

    elif preset == "scarf":
        sig = foley_rush(300, 4500, float(rng.uniform(2, 5)), 0.3)

    elif preset == "hanger":
        sig = metal_ring(float(rng.uniform(2000, 4500)), 0.05, 0.3, 0.22, int(rng.integers(2, 5)))
        sig += foley_rush(400, 4000, float(rng.uniform(2, 5)), 0.15)

    elif preset == "closet":
        sig = creak_sound(150, 600, int(rng.integers(2, 4)), 0.3, 1.0, 0.28)
        sig += foley_rush(400, 4500, float(rng.uniform(2, 4)), 0.2) + clicks(2, 600, 3500, 0.01, 0.05, 0.3)

    elif preset == "clothes_fold":
        sig = foley_rush(300, 4000, float(rng.uniform(2, 5)), 0.3) + clicks(2, 700, 3500, 0.01, 0.05, 0.25)

    elif preset == "clothes_bag":
        sig = foley_rush(500, 6000, float(rng.uniform(3, 6)), 0.3) + foley_rush(1500, 7000, 3, 0.2)

    # ── 👜 Genți / obiecte personale ──────────────────────────────────────────
    elif preset == "bag_open":
        sig = foley_rush(800, 7000, float(rng.uniform(3, 6)), 0.3) + clicks(int(rng.integers(2, 5)), 1200, 7000, 0.01, 0.03, 0.35)

    elif preset == "bag_zip":
        sig = foley_rush(1200, 8000, float(rng.uniform(2, 5)), 0.3) + clicks(int(rng.integers(2, 4)), 1500, 7000, 0.005, 0.03, 0.4)

    elif preset == "wallet":
        sig = clicks(2, 500, 3000, 0.01, 0.04, 0.4) + foley_rush(800, 5000, 4, 0.2) + metal_ring(float(rng.uniform(3000, 6000)), 0.03, 0.15, 0.15, 2)

    elif preset == "money":
        sig = foley_rush(1500, 7000, float(rng.uniform(3, 6)), 0.25) + clicks(int(rng.integers(2, 5)), 2000, 6000, 0.01, 0.03, 0.2)

    elif preset == "coins":
        sig = metal_ring(float(rng.uniform(4000, 9000)), 0.03, 0.12, 0.28, int(rng.integers(4, 9)))

    elif preset == "card_swipe":
        sig = foley_rush(800, 6000, float(rng.uniform(2, 4)), 0.25) + clicks(2, 1500, 6000, 0.005, 0.02, 0.35)

    elif preset == "keys_bag":
        sig = metal_ring(float(rng.uniform(2500, 7000)), 0.03, 0.15, 0.3, int(rng.integers(4, 9)))
        sig += foley_rush(500, 5000, 4, 0.15)

    elif preset == "bag_put":
        sig = fband(pink(50, 1500), 60, 1200) * np.exp(-np.linspace(0, 8, n)) * 0.5
        sig += clicks(2, 400, 2500, 0.01, 0.05, 0.4)

    elif preset == "suitcase_open":
        sig = clicks(2, 500, 3000, 0.01, 0.05, 0.45) + creak_sound(200, 700, 2, 0.3, 0.8, 0.25)
        sig += foley_rush(400, 4000, 3, 0.2)

    elif preset == "suitcase_zip":
        sig = foley_rush(1000, 8000, float(rng.uniform(2, 5)), 0.3) + clicks(int(rng.integers(2, 5)), 1500, 7000, 0.005, 0.03, 0.4)

    elif preset == "suitcase_wheels":
        sig = footsteps(float(rng.uniform(4, 8)), lo=200, hi=3500, amp=0.3)
        sig += foley_rush(300, 2500, float(rng.uniform(4, 8)), 0.12)

    elif preset == "luggage_lift":
        sig = foley_rush(200, 2500, float(rng.uniform(2, 4)), 0.3) + clicks(2, 400, 2500, 0.01, 0.05, 0.4)

    # ── 🚿 Baie ───────────────────────────────────────────────────────────────
    elif preset == "toilet_seat":
        sig = clicks(2, 300, 2500, 0.01, 0.06, 0.5) + fband(pink(100, 1500), 120, 1200) * np.exp(-np.linspace(0, 8, n)) * 0.35

    elif preset == "toilet_flush":
        rush = fband(pink(300, 5000), 400, 4000) * np.exp(-np.linspace(0, 3, n)) * 0.6
        gurgle = foley_rush(200, 2000, float(rng.uniform(3, 6)), 0.3)
        sig = rush + gurgle + water_bubble(int(rng.integers(3, 7)), 0.05, 0.2, 400, 1800, 0.15)

    elif preset == "sink_water":
        sig = fband(pink(500, 8000), 700, 7000) * am(rng.uniform(0.2, 0.4), 0.15, 0.85) * 0.4
        sig += foley_rush(2000, 9000, 4, 0.12)

    elif preset == "bath_fill":
        sig = fband(pink(300, 6000), 400, 5000) * am(rng.uniform(0.15, 0.3), 0.2, 0.8) * 0.4
        sig += water_bubble(int(rng.integers(4, 10)), 0.04, 0.12, 500, 2500, 0.1)

    elif preset == "drain":
        sig = fband(pink(200, 4000), 250, 3500) * np.exp(-np.linspace(0, 3.5, n)) * 0.5
        sig += water_bubble(int(rng.integers(5, 12)), 0.04, 0.18, 300, 2000, 0.2)

    elif preset == "cosmetic_pump":
        sig = clicks(2, 500, 2500, 0.01, 0.05, 0.4) + foley_rush(800, 4000, 3, 0.2)

    elif preset == "tube_squeeze":
        sig = foley_rush(800, 4000, float(rng.uniform(3, 6)), 0.25) + clicks(1, 1000, 3500, 0.01, 0.04, 0.3)

    elif preset == "toothbrush":
        sig = clicks(2, 1000, 4500, 0.01, 0.04, 0.3) + foley_rush(800, 4500, 4, 0.15)

    elif preset == "rinse_cup":
        sig = metal_ring(float(rng.uniform(2500, 5500)), 0.05, 0.3, 0.2, 3)
        sig += water_bubble(int(rng.integers(3, 6)), 0.04, 0.12, 600, 2500, 0.1)

    elif preset == "mirror_steam":
        sig = foley_rush(300, 3500, float(rng.uniform(2, 4)), 0.25)

    elif preset == "razor":
        sig = foley_rush(1500, 8000, float(rng.uniform(4, 8)), 0.2)
        sig += clicks(int(rng.integers(3, 8)), 2000, 8000, 0.01, 0.03, 0.3)

    elif preset == "electric_razor":
        sig = fband(pink(300, 5000), 400, 4000) * am(float(rng.uniform(50, 90)), 0.4, 0.6) * 0.35
        sig += sine(float(rng.uniform(1500, 3500)), 0.02)

    elif preset == "epilator":
        sig = fband(pink(500, 7000), 600, 6000) * am(float(rng.uniform(60, 100)), 0.45, 0.55) * 0.3
        sig += clicks(int(rng.integers(4, 10)), 2000, 8000, 0.005, 0.02, 0.2)

    elif preset == "tweezers":
        sig = clicks(int(rng.integers(1, 3)), 2500, 8000, 0.005, 0.02, 0.35)

    # ── 💇 Salon / îngrijire ──────────────────────────────────────────────────
    elif preset == "scissors":
        sig = clicks(int(rng.integers(2, 5)), 2000, 8000, 0.005, 0.03, 0.4)
        sig += metal_ring(float(rng.uniform(4000, 8000)), 0.02, 0.1, 0.18, 2)

    elif preset == "clippers":
        sig = fband(pink(150, 3000), 200, 2500) * am(float(rng.uniform(45, 80)), 0.5, 0.5) * 0.4
        sig += sine(float(rng.uniform(120, 300)), 0.03)

    elif preset == "rotating_brush":
        sig = foley_rush(300, 4000, float(rng.uniform(6, 12)), 0.3)
        sig += fband(pink(100, 1500), 120, 1200) * am(float(rng.uniform(8, 16)), 0.4, 0.6) * 0.15

    elif preset == "flat_iron":
        sig = foley_rush(500, 5000, float(rng.uniform(2, 4)), 0.28)
        sig += clicks(1, 1000, 4000, 0.01, 0.03, 0.2)

    elif preset == "hair_spray":
        sig = foley_rush(2000, 9500, float(rng.uniform(3, 6)), 0.3) + clicks(int(rng.integers(2, 4)), 3000, 9000, 0.02, 0.06, 0.25)

    elif preset == "hair_cut":
        sig = clicks(int(rng.integers(4, 9)), 2000, 8000, 0.005, 0.03, 0.3)
        sig += foley_rush(500, 6000, 4, 0.12)

    elif preset == "comb_put":
        sig = clicks(1, 800, 3500, 0.01, 0.04, 0.35) + fband(pink(200, 2000), 300, 1500) * np.exp(-np.linspace(0, 12, n)) * 0.18

    elif preset == "salon_chair":
        sig = creak_sound(200, 700, int(rng.integers(1, 3)), 0.3, 0.9, 0.3)
        sig += foley_rush(300, 3000, float(rng.uniform(2, 4)), 0.2)

    # ── 💄 Cosmetice ──────────────────────────────────────────────────────────
    elif preset == "makeup_open":
        sig = clicks(2, 800, 4500, 0.01, 0.04, 0.4) + foley_rush(500, 5000, 3, 0.2)

    elif preset == "makeup_close":
        sig = clicks(1, 800, 4500, 0.01, 0.05, 0.45) + fband(pink(200, 3000), 300, 2500) * np.exp(-np.linspace(0, 10, n)) * 0.25

    elif preset == "brush_tap":
        sig = clicks(int(rng.integers(2, 6)), 2000, 7000, 0.01, 0.03, 0.35)

    elif preset == "foundation_pump":
        sig = clicks(2, 500, 2500, 0.01, 0.05, 0.4) + foley_rush(800, 4000, 3, 0.15)

    elif preset == "concealer":
        sig = clicks(1, 800, 3500, 0.01, 0.04, 0.35) + foley_rush(500, 3500, 3, 0.2)

    elif preset == "bronzer":
        sig = foley_rush(1000, 5000, float(rng.uniform(3, 6)), 0.3) + clicks(1, 1500, 5000, 0.01, 0.03, 0.25)

    elif preset == "brow_pencil":
        sig = clicks(int(rng.integers(3, 8)), 800, 4000, 0.01, 0.03, 0.3)
        sig += foley_rush(500, 3500, 5, 0.12)

    elif preset == "mascara":
        sig = clicks(int(rng.integers(2, 5)), 1500, 6000, 0.01, 0.04, 0.35) + foley_rush(800, 4500, 4, 0.15)

    elif preset == "lash_glue":
        sig = foley_rush(800, 5000, float(rng.uniform(2, 4)), 0.25) + clicks(1, 1500, 6000, 0.01, 0.03, 0.3)

    elif preset == "sponge":
        sig = foley_rush(500, 4000, float(rng.uniform(3, 6)), 0.25) + water_bubble(int(rng.integers(2, 5)), 0.05, 0.15, 500, 2000, 0.12)

    elif preset == "spray_mist":
        sig = foley_rush(2000, 9500, float(rng.uniform(3, 6)), 0.3) + clicks(int(rng.integers(2, 4)), 3000, 9000, 0.02, 0.06, 0.25)

    elif preset == "cotton_pad":
        sig = foley_rush(1500, 6000, float(rng.uniform(3, 6)), 0.25)

    elif preset == "cream_apply":
        sig = foley_rush(300, 4000, float(rng.uniform(2, 5)), 0.3)

    elif preset == "serum_drop":
        sig = water_bubble(int(rng.integers(1, 3)), 0.05, 0.15, 800, 3000, 0.25)
        sig += foley_rush(800, 4000, 2, 0.1)

    elif preset == "face_mask":
        sig = foley_rush(600, 6000, float(rng.uniform(3, 6)), 0.3) + clicks(1, 1000, 4000, 0.01, 0.04, 0.2)

    # ── 🌸 Parfumuri ──────────────────────────────────────────────────────────
    elif preset == "perfume_spray":
        sig = foley_rush(2000, 9500, float(rng.uniform(3, 6)), 0.32) + clicks(int(rng.integers(2, 5)), 3000, 9000, 0.02, 0.07, 0.3)

    elif preset == "bottle_cap":
        sig = clicks(2, 1000, 5000, 0.01, 0.04, 0.4) + metal_ring(float(rng.uniform(2500, 5000)), 0.03, 0.15, 0.2, 2)

    elif preset == "deodorant_spray":
        sig = foley_rush(1800, 9000, float(rng.uniform(3, 6)), 0.3) + clicks(int(rng.integers(2, 4)), 2500, 8500, 0.02, 0.06, 0.28)

    elif preset == "roll_on":
        sig = foley_rush(500, 4500, float(rng.uniform(3, 6)), 0.28) + water_bubble(int(rng.integers(1, 3)), 0.05, 0.12, 600, 2000, 0.12)

    elif preset == "stick_deo":
        sig = foley_rush(300, 3500, float(rng.uniform(2, 5)), 0.25) + clicks(1, 600, 3000, 0.01, 0.04, 0.2)

    elif preset == "body_spray":
        sig = foley_rush(2000, 9500, float(rng.uniform(3, 6)), 0.3) + clicks(int(rng.integers(2, 4)), 3000, 9000, 0.02, 0.06, 0.28)

    elif preset == "hand_cream":
        sig = foley_rush(300, 4000, float(rng.uniform(2, 5)), 0.28)

    elif preset == "perfume_wrist":
        sig = clicks(1, 1500, 6000, 0.01, 0.04, 0.2) + foley_rush(2000, 9000, 4, 0.12)

    # ── 💅 Unghii ─────────────────────────────────────────────────────────────
    elif preset == "polish_shake":
        sig = clicks(int(rng.integers(3, 7)), 800, 4000, 0.01, 0.05, 0.35) + foley_rush(300, 2500, 6, 0.12)

    elif preset == "polish_open":
        sig = clicks(2, 800, 4500, 0.01, 0.05, 0.4) + foley_rush(500, 4000, 3, 0.15)

    elif preset == "polish_brush":
        sig = foley_rush(800, 4500, float(rng.uniform(3, 6)), 0.2) + clicks(1, 1000, 4000, 0.01, 0.03, 0.2)

    elif preset == "nail_file":
        sig = foley_rush(1200, 7000, float(rng.uniform(5, 10)), 0.3)

    elif preset == "nail_clipper":
        sig = clicks(int(rng.integers(2, 5)), 1500, 7000, 0.005, 0.03, 0.4)

    elif preset == "cuticle":
        sig = clicks(int(rng.integers(2, 6)), 1500, 6000, 0.01, 0.04, 0.3) + foley_rush(800, 4000, 4, 0.12)

    # ── 💎 Bijuterii ──────────────────────────────────────────────────────────
    elif preset == "jewelry_box_open":
        sig = clicks(2, 800, 4000, 0.01, 0.05, 0.4) + creak_sound(300, 800, 1, 0.2, 0.6, 0.2)

    elif preset == "jewelry_box_close":
        sig = clicks(2, 800, 4000, 0.01, 0.05, 0.45) + fband(pink(200, 2500), 300, 2000) * np.exp(-np.linspace(0, 9, n)) * 0.3

    elif preset == "earrings":
        sig = metal_ring(float(rng.uniform(4000, 8000)), 0.03, 0.15, 0.25, int(rng.integers(2, 5))) + clicks(1, 2000, 6000, 0.005, 0.02, 0.2)

    elif preset == "bracelet":
        sig = metal_ring(float(rng.uniform(3000, 7000)), 0.05, 0.25, 0.25, int(rng.integers(3, 6))) + clicks(1, 2000, 6000, 0.005, 0.03, 0.25)

    elif preset == "necklace":
        sig = metal_ring(float(rng.uniform(2500, 6000)), 0.05, 0.3, 0.22, int(rng.integers(3, 7))) + clicks(1, 1500, 5000, 0.005, 0.03, 0.2)

    elif preset == "ring_put":
        sig = clicks(1, 2000, 6000, 0.005, 0.03, 0.25) + metal_ring(float(rng.uniform(4000, 8000)), 0.03, 0.12, 0.2, 2)

    elif preset == "jewelry_clink":
        sig = metal_ring(float(rng.uniform(3000, 7000)), 0.03, 0.15, 0.22, int(rng.integers(3, 7)))

    # ── 👠 Îmbrăcare / încălțare ─────────────────────────────────────────────
    elif preset == "shoe_box":
        sig = foley_rush(400, 4000, float(rng.uniform(2, 4)), 0.3) + clicks(2, 500, 3000, 0.01, 0.05, 0.35)

    elif preset == "shoe_put":
        sig = foley_rush(300, 3500, float(rng.uniform(2, 4)), 0.3) + clicks(2, 400, 2500, 0.01, 0.06, 0.4)

    elif preset == "shoe_takeoff":
        sig = foley_rush(300, 3500, float(rng.uniform(2, 5)), 0.3) + clicks(2, 300, 2000, 0.02, 0.08, 0.4)

    elif preset == "shoelace":
        sig = foley_rush(800, 4500, float(rng.uniform(3, 6)), 0.25) + clicks(int(rng.integers(2, 5)), 1000, 4000, 0.01, 0.03, 0.3)

    elif preset == "boot_zip":
        sig = foley_rush(1000, 7000, float(rng.uniform(2, 4)), 0.28) + clicks(int(rng.integers(2, 5)), 1500, 7000, 0.005, 0.03, 0.4)

    elif preset == "sandals":
        sig = clicks(int(rng.integers(2, 5)), 400, 2500, 0.01, 0.05, 0.35) + foley_rush(300, 3000, 3, 0.15)

    elif preset == "footsteps_stone":
        sig = footsteps(float(rng.uniform(1.0, 1.8)), lo=300, hi=5000, amp=0.55)
        sig += clicks(int(rng.integers(3, 8)), 2000, 7000, 0.01, 0.03, 0.15)

    elif preset == "footsteps_floor":
        sig = footsteps(float(rng.uniform(1.0, 1.8)), lo=200, hi=4500, amp=0.5)

    # ── 🏢 Spații interioare ──────────────────────────────────────────────────
    elif preset == "hall":
        sig = fband(pink(80, 2200), 100, 1800) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.22
        sig += footsteps(float(rng.uniform(0.8, 1.6)), lo=250, hi=3500, amp=0.3)

    elif preset == "staircase":
        sig = footsteps(float(rng.uniform(0.6, 1.2)), lo=150, hi=3500, amp=0.45)
        sig += creak_sound(90, 350, int(rng.integers(1, 4)), 0.3, 0.9, 0.2)

    elif preset == "empty_room":
        sig = fband(pink(70, 1800), 90, 1500) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.14
        sig += pink(30, 200) * 0.03

    elif preset == "crowded_room":
        murmur = fband(pink(140, 3200), 160, 2600) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.35
        sig = murmur + clicks(int(rng.integers(4, 10)), 1000, 5000, 0.01, 0.04, 0.15)

    elif preset == "bedroom":
        sig = fband(pink(80, 2000), 100, 1500) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.12
        sig += sine(50, 0.01) + creak_sound(100, 300, int(rng.integers(0, 2)), 0.3, 0.8, 0.08)

    elif preset == "dressing":
        sig = fband(pink(150, 3000), 200, 2500) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.14
        sig += foley_rush(300, 3500, 3, 0.1)

    elif preset == "balcony":
        sig = fband(pink(150, 4000), 200, 3500) * am(rng.uniform(0.05, 0.12), 0.3, 0.7) * 0.2
        sig += pink(40, 600) * 0.05

    elif preset == "garage":
        sig = fband(pink(60, 1500), 70, 1200) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.25
        sig += sine(50, 0.012) + sine(100, 0.008)

    elif preset == "basement":
        sig = fband(pink(50, 1200), 60, 1000) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.25
        sig += pink(25, 200) * 0.04

    elif preset == "attic":
        sig = fband(pink(60, 1500), 70, 1200) * am(rng.uniform(0.03, 0.08), 0.3, 0.7) * 0.14
        sig += creak_sound(90, 300, int(rng.integers(0, 2)), 0.3, 0.8, 0.1)

    elif preset == "office_space":
        sig = fband(pink(100, 2500), 120, 2000) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.16
        sig += clicks(int(rng.integers(3, 8)), 1500, 6000, 0.005, 0.02, 0.18)

    elif preset == "mall_space":
        murmur = fband(pink(130, 3000), 160, 2500) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.3
        sig = murmur + sine(523, 0.01) + sine(659, 0.008)

    elif preset == "salon_space":
        sig = fband(pink(100, 2500), 120, 2000) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.16
        sig += foley_rush(400, 3500, 3, 0.1) + clicks(2, 1000, 4500, 0.01, 0.04, 0.12)

    elif preset == "hotel":
        sig = fband(pink(100, 2500), 120, 2000) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.18
        sig += footsteps(float(rng.uniform(0.6, 1.2)), lo=300, hi=3500, amp=0.18)

    elif preset == "reception":
        murmur = fband(pink(140, 3000), 160, 2500) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.25
        sig = murmur + clicks(int(rng.integers(2, 6)), 1500, 6000, 0.005, 0.02, 0.15)

    elif preset == "hotel_corridor":
        sig = fband(pink(80, 2000), 100, 1500) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.16
        sig += footsteps(float(rng.uniform(0.6, 1.2)), lo=300, hi=3000, amp=0.2)

    elif preset == "hotel_lift":
        sig = fband(pink(60, 1200), 70, 1000) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.22
        sig += sine(50, 0.014) + clicks(2, 1500, 5000, 0.01, 0.04, 0.18)

    elif preset == "parking":
        sig = fband(pink(60, 1500), 70, 1200) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.22
        sig += sine(50, 0.012) + sine(100, 0.008)

    # ── 🌳 Exterior ───────────────────────────────────────────────────────────
    elif preset == "gate_open":
        sig = creak_sound(150, 600, int(rng.integers(2, 5)), 0.4, 1.2, 0.3)
        sig += metal_ring(float(rng.uniform(1500, 3500)), 0.05, 0.25, 0.18, 2)

    elif preset == "gate_close":
        sig = metal_ring(float(rng.uniform(1500, 3500)), 0.05, 0.3, 0.25, 3)
        sig += fband(pink(60, 1200), 70, 1000) * np.exp(-np.linspace(0, 8, n)) * 0.4

    elif preset == "gravel":
        sig = footsteps(float(rng.uniform(1.0, 1.8)), lo=400, hi=6000, amp=0.5)
        sig += clicks(int(rng.integers(5, 12)), 2500, 9000, 0.01, 0.04, 0.18)

    elif preset == "grass":
        sig = foley_rush(300, 4000, float(rng.uniform(2, 5)), 0.25)
        sig += footsteps(float(rng.uniform(1.0, 1.8)), lo=150, hi=3000, amp=0.3)

    elif preset == "leaves":
        sig = foley_rush(1500, 7000, float(rng.uniform(3, 7)), 0.3)
        sig += clicks(int(rng.integers(3, 8)), 2000, 8000, 0.01, 0.04, 0.25)

    elif preset == "branches":
        sig = snap_sound(int(rng.integers(2, 6)), 1000, 6000, 0.3) + creak_sound(200, 800, 2, 0.2, 0.6, 0.25)

    elif preset == "garage_door":
        sig = creak_sound(80, 400, int(rng.integers(2, 5)), 0.5, 1.5, 0.35)
        sig += foley_rush(150, 2000, float(rng.uniform(2, 4)), 0.25) + metal_ring(float(rng.uniform(1000, 2500)), 0.05, 0.3, 0.15, 2)

    elif preset == "car_door":
        sig = fband(pink(50, 1500), 60, 1200) * np.exp(-np.linspace(0, 8, n)) * 0.55
        sig += clicks(2, 500, 3000, 0.01, 0.06, 0.4)

    elif preset == "car_trunk":
        sig = fband(pink(50, 1200), 60, 1000) * np.exp(-np.linspace(0, 8, n)) * 0.6
        sig += clicks(2, 400, 2500, 0.01, 0.06, 0.4)

    elif preset == "wipers":
        sig = foley_rush(300, 3500, float(rng.uniform(1, 2)), 0.25) + clicks(2, 1500, 5000, 0.01, 0.04, 0.25)

    elif preset == "car_window":
        sig = foley_rush(400, 4000, float(rng.uniform(2, 4)), 0.25) + clicks(2, 1000, 4500, 0.01, 0.04, 0.2)

    elif preset == "engine_electric":
        sig = fband(pink(100, 2000), 120, 1500) * am(float(rng.uniform(10, 20)), 0.4, 0.6) * 0.35
        sig += sine(float(rng.uniform(200, 400)), 0.02)

    elif preset == "engine_diesel":
        sig = fband(pink(30, 500), 40, 400) * am(float(rng.uniform(8, 14)), 0.35, 0.65) * 0.5
        sig += foley_rush(200, 2500, 6, 0.12)

    # ── 🚆 Transport suplimentar ──────────────────────────────────────────────
    elif preset == "train_doors":
        sig = fband(pink(200, 4000), 300, 3500) * np.exp(-np.linspace(0, 6, n)) * 0.5
        sig += clicks(3, 800, 4000, 0.01, 0.05, 0.5) + creak_sound(200, 600, 1, 0.3, 0.8, 0.2)

    elif preset == "train_brake":
        sig = fband(pink(1500, 6000), 2000, 5000) * am(float(rng.uniform(3, 6)), 0.4, 0.6) * 0.35
        sig += fband(pink(100, 2000), 150, 1500) * am(float(rng.uniform(6, 12)), 0.3, 0.7) * 0.3

    elif preset == "train_accel":
        ramp = np.linspace(0, 1, n) ** 0.6
        sig = fband(pink(40, 800), 50, 600) * am(float(rng.uniform(4, 8)), 0.3, 0.7) * 0.5 * ramp
        sig += foley_rush(300, 3000, 8, 0.15) * ramp

    elif preset == "train_depart":
        rumble = fband(pink(40, 700), 50, 600) * am(float(rng.uniform(4, 8)), 0.3, 0.7) * 0.5
        joints = footsteps(float(rng.uniform(3, 6)), lo=50, hi=400, amp=0.5)
        ramp = np.linspace(0, 1, n) ** 0.5
        sig = (rumble + joints * 0.6) * ramp

    elif preset == "train_pass":
        env = np.concatenate([np.linspace(0, 1, n // 2), np.linspace(1, 0, n - n // 2)]) ** 0.7
        sig = fband(pink(40, 900), 50, 700) * am(float(rng.uniform(6, 10)), 0.3, 0.7) * 0.6 * env
        sig += foley_rush(500, 4000, 10, 0.15) * env

    elif preset == "train_station":
        crowd = fband(pink(150, 3000), 160, 2500) * am(rng.uniform(0.04, 0.1), 0.2, 0.8) * 0.3
        hum = fband(pink(50, 400), 60, 350) * 0.12
        sig = crowd + hum + clicks(int(rng.integers(3, 8)), 1500, 6000, 0.01, 0.04, 0.15)

    elif preset == "train_interior":
        rumble = fband(pink(30, 400), 35, 350) * am(rng.uniform(0.6, 1.2), 0.15, 0.85) * 0.4
        joints = footsteps(float(rng.uniform(3, 5)), lo=50, hi=400, amp=0.35)
        sig = rumble + joints * 0.5 + foley_rush(300, 2500, 3, 0.08)

    elif preset == "station_announce":
        sig = np.zeros(n)
        p = int(rng.integers(int(0.1 * n), int(0.4 * n)))
        alen = min(int(rng.uniform(2, 6) * sr), n - p)
        if alen > 0:
            ann = fband(pink(250, 3200, alen), 300, 2800)
            syl = np.zeros(alen)
            sp = 0
            while sp < alen:
                sdur = int(rng.uniform(0.06, 0.16) * sr)
                se = min(sp + sdur, alen)
                syl[sp:se] = float(rng.uniform(0.3, 0.9))
                sp += sdur + int(rng.uniform(0.03, 0.1) * sr)
            frame = np.sin(np.pi * np.linspace(0, 1, alen)) ** 0.3
            sig[p:p + alen] += ann * syl * frame * 0.3
        sig += fband(pink(150, 3000), 160, 2500) * 0.2

    elif preset == "crowd_station":
        sig = fband(pink(150, 3200), 160, 2600) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.4
        sig += footsteps(float(rng.uniform(1, 3)), lo=250, hi=3500, amp=0.25)

    elif preset == "metro_station":
        rumble = fband(pink(30, 500), 40, 400) * am(rng.uniform(0.8, 1.5), 0.15, 0.85) * 0.5
        screech = fband(pink(2000, 6000), 2500, 5500) * am(rng.uniform(0.3, 0.6), 0.4, 0.6) * 0.12
        sig = rumble + screech + clicks(3, 1500, 5000, 0.01, 0.04, 0.2)

    elif preset == "bus_doors":
        sig = fband(pink(200, 4000), 300, 3500) * np.exp(-np.linspace(0, 6, n)) * 0.5
        sig += clicks(3, 800, 4000, 0.01, 0.05, 0.45)

    elif preset == "transport_announce":
        sig = fband(pink(250, 3200), 300, 2800) * am(float(rng.uniform(0.3, 0.6)), 0.3, 0.7) * 0.22
        sig += foley_rush(300, 3000, 4, 0.1)

    elif preset == "airport_extra":
        sig = fband(pink(100, 3000), 120, 2500) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.3
        sig += sine(60, 0.012) + sine(120, 0.008)

    elif preset == "baggage_belt":
        sig = fband(pink(80, 1500), 100, 1200) * am(rng.uniform(0.3, 0.6), 0.2, 0.8) * 0.3
        sig += clicks(int(rng.integers(3, 8)), 800, 4000, 0.01, 0.05, 0.25)

    elif preset == "luggage_cart":
        sig = footsteps(float(rng.uniform(3, 6)), lo=150, hi=3000, amp=0.3)
        sig += foley_rush(200, 2000, float(rng.uniform(3, 6)), 0.12) + clicks(2, 500, 2500, 0.01, 0.04, 0.2)

    elif preset == "plane_cabin":
        sig = fband(pink(40, 900), 50, 800) * am(rng.uniform(0.3, 0.7), 0.15, 0.85) * 0.45
        sig += foley_rush(500, 4000, 3, 0.08) + sine(60, 0.012)

    elif preset == "seatbelt":
        sig = clicks(2, 1500, 6000, 0.005, 0.03, 0.4) + metal_ring(float(rng.uniform(3000, 6000)), 0.03, 0.12, 0.2, 2)

    # ── 🏙️ Oraș ──────────────────────────────────────────────────────────────
    elif preset == "crowd_moving":
        murmur = fband(pink(140, 3200), 160, 2600) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.32
        sig = murmur + footsteps(float(rng.uniform(1, 3)), lo=250, hi=3500, amp=0.25)

    elif preset == "people_walk":
        sig = footsteps(float(rng.uniform(1, 2)), lo=250, hi=4000, amp=0.4)

    elif preset == "people_run":
        sig = footsteps(float(rng.uniform(2, 3.5)), lo=200, hi=4500, amp=0.45)

    elif preset == "trotinette":
        sig = fband(pink(40, 800), 50, 600) * am(float(rng.uniform(6, 12)), 0.3, 0.7) * 0.35
        sig += sine(float(rng.uniform(300, 700)), 0.015)

    elif preset == "tram":
        rumble = fband(pink(30, 500), 40, 400) * am(rng.uniform(0.6, 1.2), 0.15, 0.85) * 0.5
        bell = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.2 * n), n))
            blen = min(int(0.3 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                bell[p:p + blen] += (np.sin(2 * np.pi * 2200 * tl) + 0.4 * np.sin(2 * np.pi * 2900 * tl)) * np.exp(-np.linspace(0, 5, blen)) * 0.14
        sig = rumble + bell

    elif preset == "distant_horn":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(0.3, 1.2) * sr), n - p)
            if hlen > 0:
                tl = np.linspace(0, hlen / sr, hlen)
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.3
                sig[p:p + hlen] += np.sin(2 * np.pi * float(rng.uniform(300, 600)) * tl) * env * 0.12
        sig += fband(pink(50, 1500), 60, 1200) * 0.1

    elif preset == "distant_siren":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, int(0.6 * n)))
            slen = min(int(rng.uniform(2, 5) * sr), n - p)
            if slen > 0:
                tl = np.linspace(0, slen / sr, slen)
                freq = float(rng.uniform(500, 900))
                sweep = freq + 150 * np.sin(2 * np.pi * 0.6 * tl)
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.3
                sig[p:p + slen] += np.sin(2 * np.pi * sweep * tl) * env * 0.1
        sig += fband(pink(50, 1500), 60, 1200) * 0.1

    elif preset == "traffic_light":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            blen = min(int(0.1 * sr), n - p)
            if blen > 0:
                sig[p:p + blen] += sine(2200, 0.18)[:blen] * np.exp(-np.linspace(0, 20, blen))
        sig += fband(pink(50, 1500), 60, 1200) * 0.12

    elif preset == "crosswalk":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            blen = min(int(0.3 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                sig[p:p + blen] += (np.sin(2 * np.pi * 2000 * tl) + 0.5 * np.sin(2 * np.pi * 2600 * tl)) * np.sin(np.pi * np.linspace(0, 1, blen)) * 0.14
        sig += fband(pink(50, 1500), 60, 1200) * 0.12

    elif preset == "roadwork":
        sig = clicks(int(rng.integers(8, 18)), 300, 3000, 0.01, 0.05, 0.5)
        sig += fband(pink(100, 2000), 150, 1500) * am(float(rng.uniform(4, 8)), 0.3, 0.7) * 0.25

    elif preset == "jackhammer":
        sig = fband(pink(100, 2500), 150, 2000) * am(float(rng.uniform(8, 14)), 0.5, 0.5) * 0.4
        sig += clicks(int(rng.integers(8, 16)), 500, 3000, 0.01, 0.04, 0.4)

    elif preset == "construction_extra":
        sig = clicks(int(rng.integers(6, 14)), 400, 3500, 0.01, 0.06, 0.45)
        sig += foley_rush(300, 3000, float(rng.uniform(3, 6)), 0.2)

    elif preset == "distant_cars":
        sig = fband(pink(50, 1500), 60, 1200) * am(rng.uniform(0.04, 0.1), 0.25, 0.75) * 0.25
        sig += foley_rush(200, 2500, 3, 0.1)

    elif preset == "night_traffic":
        sig = fband(pink(40, 1500), 50, 1200) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.22
        sig += sine(50, 0.01) + foley_rush(200, 2000, 2, 0.08)

    elif preset == "market_square":
        murmur = fband(pink(150, 3200), 180, 2600) * am(rng.uniform(0.05, 0.12), 0.25, 0.75) * 0.35
        calls = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.4, 1.2) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(300, 800))
                calls[p:p + clen] += np.sin(2 * np.pi * freq * tl) * np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.3 * 0.12
        sig = murmur + calls

    elif preset == "park_space":
        sig = birds(nb=10, lo_f=1200, hi_f=5500) * 0.4
        sig += fband(pink(150, 4000), 200, 3500) * am(rng.uniform(0.04, 0.1), 0.3, 0.7) * 0.15
        sig += footsteps(float(rng.uniform(0.6, 1.4)), lo=250, hi=3500, amp=0.2)

    elif preset == "fountain_water":
        sig = fband(pink(2000, 8000), 2500, 7000) * am(rng.uniform(0.15, 0.3), 0.2, 0.8) * 0.3
        sig += water_bubble(int(rng.integers(4, 10)), 0.04, 0.12, 800, 3500, 0.12)

    # ── 🏫 Școală / birou ─────────────────────────────────────────────────────
    elif preset == "classroom":
        sig = fband(pink(150, 3000), 180, 2500) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.3
        sig += clicks(int(rng.integers(2, 6)), 800, 4000, 0.01, 0.04, 0.15)

    elif preset == "chairs_move":
        sig = foley_rush(200, 3000, float(rng.uniform(2, 5)), 0.3) + creak_sound(150, 500, int(rng.integers(1, 4)), 0.2, 0.6, 0.3)

    elif preset == "chalk_board":
        sig = foley_rush(1000, 6000, float(rng.uniform(3, 7)), 0.3) + clicks(int(rng.integers(3, 8)), 2000, 8000, 0.005, 0.03, 0.25)

    elif preset == "board_wipe":
        sig = foley_rush(500, 4000, float(rng.uniform(2, 4)), 0.35) + foley_rush(1500, 6000, 3, 0.15)

    elif preset == "notebook":
        sig = foley_rush(1000, 5000, float(rng.uniform(3, 6)), 0.28) + clicks(1, 800, 3500, 0.01, 0.04, 0.2)

    elif preset == "page_turn":
        sig = foley_rush(1500, 7000, float(rng.uniform(3, 6)), 0.25) + clicks(1, 1500, 5000, 0.01, 0.03, 0.15)

    elif preset == "page_rip":
        sig = foley_rush(1200, 8000, float(rng.uniform(2, 4)), 0.3) + clicks(int(rng.integers(2, 5)), 1500, 7000, 0.01, 0.04, 0.25)

    elif preset == "backpack":
        sig = foley_rush(500, 6000, float(rng.uniform(3, 6)), 0.3) + clicks(2, 1000, 4500, 0.01, 0.04, 0.3)

    elif preset == "students":
        sig = fband(pink(150, 3200), 180, 2600) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.35
        sig += footsteps(float(rng.uniform(1, 2.5)), lo=250, hi=3500, amp=0.3)

    elif preset == "hall_steps":
        sig = footsteps(float(rng.uniform(1, 2)), lo=250, hi=4000, amp=0.4)
        sig += fband(pink(100, 2500), 120, 2000) * 0.15

    elif preset == "school_bell":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.2 * n), int(0.7 * n)))
            blen = min(int(rng.uniform(0.5, 1.2) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                env = np.exp(-np.linspace(0, 3, blen))
                sig[p:p + blen] += (np.sin(2 * np.pi * 880 * tl) + 0.3 * np.sin(2 * np.pi * 1760 * tl)) * env * 0.16
        sig += fband(pink(150, 3000), 180, 2500) * 0.12

    elif preset == "computer_typing":
        sig = clicks(int(rng.integers(10, 25)), 1500, 7000, 0.005, 0.02, 0.35)
        sig += foley_rush(300, 2500, 3, 0.08)

    elif preset == "printer_extra":
        sig = fband(pink(150, 3000), 200, 2500) * am(float(rng.uniform(4, 8)), 0.3, 0.7) * 0.3
        sig += clicks(int(rng.integers(3, 7)), 1000, 5000, 0.01, 0.03, 0.25)

    elif preset == "desk_phone":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, n))
            rlen = min(int(rng.uniform(0.3, 0.8) * sr), n - p)
            if rlen > 0:
                tl = np.linspace(0, rlen / sr, rlen)
                sig[p:p + rlen] += (np.sin(2 * np.pi * 440 * tl) + 0.5 * np.sin(2 * np.pi * 480 * tl)) * np.sin(np.pi * np.linspace(0, 1, rlen)) ** 0.4 * 0.14
        sig += fband(pink(100, 2500), 120, 2000) * 0.12

    # ── 🏥 Medical ────────────────────────────────────────────────────────────
    elif preset == "clinic_door":
        sig = creak_sound(150, 600, int(rng.integers(1, 3)), 0.3, 0.9, 0.3)
        sig += fband(pink(80, 1500), 90, 1200) * np.exp(-np.linspace(0, 9, n)) * 0.4

    elif preset == "wheelchair":
        sig = footsteps(float(rng.uniform(3, 6)), lo=200, hi=3000, amp=0.3)
        sig += foley_rush(200, 2000, float(rng.uniform(4, 8)), 0.12) + clicks(2, 500, 2500, 0.01, 0.04, 0.2)

    elif preset == "stretcher":
        sig = foley_rush(200, 2000, float(rng.uniform(2, 4)), 0.3) + clicks(2, 400, 2500, 0.01, 0.06, 0.35)

    elif preset == "stethoscope":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 8))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.05, 0.15) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                sig[p:p + blen] += np.sin(2 * np.pi * float(rng.uniform(120, 250)) * tl) * np.exp(-np.linspace(0, 12, blen)) * 0.2

    elif preset == "blood_pressure":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.3, 0.7) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                env = np.sin(np.pi * np.linspace(0, 1, blen)) ** 0.4
                sig[p:p + blen] += np.sin(2 * np.pi * float(rng.uniform(60, 120)) * tl) * env * 0.25
        sig += foley_rush(300, 2500, 3, 0.08)

    elif preset == "monitor_beep":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            blen = min(int(0.08 * sr), n - p)
            if blen > 0:
                sig[p:p + blen] += sine(1800, 0.18)[:blen] * np.exp(-np.linspace(0, 20, blen))
        sig += fband(pink(60, 1500), 70, 1200) * 0.08

    elif preset == "monitor_alarm":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            blen = min(int(0.4 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                sig[p:p + blen] += (np.sin(2 * np.pi * 900 * tl) + 0.5 * np.sin(2 * np.pi * 1350 * tl)) * np.sin(np.pi * np.linspace(0, 1, blen)) ** 0.3 * 0.18
        sig += fband(pink(60, 1500), 70, 1200) * 0.1

    elif preset == "medical_gloves":
        sig = foley_rush(500, 6000, float(rng.uniform(3, 6)), 0.3) + clicks(int(rng.integers(2, 5)), 1500, 6000, 0.005, 0.03, 0.25)

    elif preset == "syringe":
        sig = foley_rush(1000, 5000, float(rng.uniform(2, 4)), 0.25) + clicks(2, 1500, 5000, 0.005, 0.02, 0.3)

    elif preset == "medical_pack":
        sig = foley_rush(1500, 8000, float(rng.uniform(3, 6)), 0.3) + snap_sound(2, 1500, 6000, 0.3)

    elif preset == "sanitizer":
        sig = clicks(2, 500, 2500, 0.01, 0.05, 0.4) + foley_rush(800, 4000, 3, 0.2)

    elif preset == "curtain_draw":
        sig = foley_rush(300, 4000, float(rng.uniform(2, 5)), 0.3)
        sig += metal_ring(float(rng.uniform(2000, 4500)), 0.03, 0.15, 0.12, int(rng.integers(2, 5)))

    # ── 🎵 Divertisment ───────────────────────────────────────────────────────
    elif preset == "applause_soft":
        claps = np.zeros(n)
        for _ in range(int(rng.integers(15, 35))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.01, 0.03) * sr), n - p)
            if clen > 0:
                claps[p:p + clen] += fband(rng.uniform(-1, 1, clen), 900, 5500) * np.exp(-np.linspace(0, 22, clen)) * float(rng.uniform(0.05, 0.12))
        sig = claps + fband(pink(140, 3000), 160, 2500) * 0.08

    elif preset == "laugh":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.3, 1.0) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(350, 700))
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.4
                mod = 1 + 0.3 * np.sin(2 * np.pi * float(rng.uniform(4, 8)) * tl)
                sig[p:p + clen] += np.sin(2 * np.pi * freq * tl) * mod * env * 0.14

    elif preset == "giggle":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 9))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.2, 0.6) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(500, 1000))
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.5
                mod = 1 + 0.4 * np.sin(2 * np.pi * float(rng.uniform(6, 11)) * tl)
                sig[p:p + clen] += np.sin(2 * np.pi * freq * tl) * mod * env * 0.1

    elif preset == "whisper":
        sig = fband(pink(250, 2500), 300, 2000) * am(rng.uniform(0.1, 0.3), 0.4, 0.6) * 0.12

    elif preset == "whistle":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), n))
            wlen = min(int(rng.uniform(0.3, 0.9) * sr), n - p)
            if wlen > 0:
                tl = np.linspace(0, wlen / sr, wlen)
                env = np.sin(np.pi * np.linspace(0, 1, wlen)) ** 0.5
                sig[p:p + wlen] += np.sin(2 * np.pi * float(rng.uniform(1800, 3200)) * tl) * env * 0.14

    elif preset == "boo":
        sig = fband(pink(150, 1500), 200, 1200) * am(float(rng.uniform(2, 4)), 0.4, 0.6) * 0.22
        sig += np.zeros(n)

    elif preset == "stage":
        murmur = fband(pink(150, 3200), 180, 2600) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.28
        sig = murmur + clicks(int(rng.integers(3, 8)), 1000, 5000, 0.01, 0.04, 0.15)

    elif preset == "microphone":
        sig = clicks(2, 400, 2500, 0.01, 0.05, 0.35) + foley_rush(300, 2500, 3, 0.12)

    elif preset == "speaker_feedback":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.4, 1.5) * sr), n - p)
            if slen > 0:
                tl = np.linspace(0, slen / sr, slen)
                freq = float(rng.uniform(600, 2400))
                sig[p:p + slen] += np.sin(2 * np.pi * freq * tl) * np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.5 * 0.12
        sig += fband(pink(150, 3000), 180, 2500) * 0.12

    elif preset == "dance":
        beat = float(rng.uniform(2, 3))
        sig = np.zeros(n)
        pos = 0
        while pos < n:
            blen = min(int(0.06 * sr), n - pos)
            if blen > 0:
                sig[pos:pos + blen] += fband(rng.uniform(-1, 1, blen), 50, 400) * np.exp(-np.linspace(0, 15, blen)) * 0.5
            pos += int(sr / beat)
        sig += fband(pink(100, 2000), 150, 1500) * am(beat, 0.4, 0.6) * 0.15

    elif preset == "club":
        beat = float(rng.uniform(2, 3))
        sig = np.zeros(n)
        pos = 0
        while pos < n:
            blen = min(int(0.08 * sr), n - pos)
            if blen > 0:
                sig[pos:pos + blen] += fband(rng.uniform(-1, 1, blen), 50, 500) * np.exp(-np.linspace(0, 12, blen)) * 0.6
            pos += int(sr / beat)
        sig += fband(pink(100, 3000), 150, 2500) * am(beat * 2, 0.4, 0.6) * 0.2

    elif preset == "concert":
        murmur = fband(pink(150, 3000), 180, 2500) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.3
        beat = float(rng.uniform(1.5, 2.5))
        sig = murmur + fband(pink(100, 2000), 150, 1500) * am(beat, 0.4, 0.6) * 0.18

    elif preset == "theater":
        sig = fband(pink(120, 2800), 150, 2200) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.25
        sig += clicks(int(rng.integers(2, 6)), 1000, 4500, 0.01, 0.04, 0.12)

    elif preset == "cinema":
        sig = fband(pink(100, 2500), 120, 2000) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.2
        sig += foley_rush(300, 2500, 2, 0.1) + sine(60, 0.008)

    elif preset == "projector":
        sig = fband(pink(150, 3000), 200, 2500) * am(float(rng.uniform(10, 20)), 0.35, 0.65) * 0.18

    # ── ❤️ Emoții ambientale ──────────────────────────────────────────────────
    elif preset == "breath_calm":
        sig = fband(pink(200, 1500), 250, 1200) * am(float(rng.uniform(0.15, 0.3)), 0.6, 0.4) * 0.12

    elif preset == "breath_agitated":
        sig = fband(pink(200, 1800), 250, 1500) * am(float(rng.uniform(0.4, 0.8)), 0.6, 0.4) * 0.18

    elif preset == "sigh":
        sig = fband(pink(200, 1800), 250, 1500) * np.exp(-np.linspace(0, 6, n)) * 0.18

    elif preset == "cry_soft":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.3, 1.2) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(300, 700))
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.4
                mod = 1 + 0.3 * np.sin(2 * np.pi * float(rng.uniform(3, 6)) * tl)
                sig[p:p + clen] += np.sin(2 * np.pi * freq * tl) * mod * env * 0.1

    elif preset == "cry_loud":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.5, 1.5) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(250, 600))
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.4
                mod = 1 + 0.35 * np.sin(2 * np.pi * float(rng.uniform(3, 6)) * tl)
                sig[p:p + clen] += np.sin(2 * np.pi * freq * tl) * mod * env * 0.18

    elif preset == "heartbeat_fast":
        sig = np.zeros(n)
        bpm = float(rng.uniform(100, 150))
        beat_int = sr / (bpm / 60)
        pos = 0
        while pos < n:
            for freq, amp, decay in [(70, 0.4, 15), (45, 0.25, 10)]:
                blen = min(int(0.06 * sr), n - pos)
                if blen > 0:
                    tl = np.linspace(0, blen / sr, blen)
                    sig[pos:pos + blen] += np.sin(2 * np.pi * freq * tl) * np.exp(-decay * tl) * amp
            pos += int(beat_int)

    elif preset == "tremble":
        sig = fband(pink(100, 3000), 120, 2500) * am(float(rng.uniform(6, 12)), 0.5, 0.5) * 0.12

    elif preset == "silence_tension":
        sig = pink(40, 400) * 0.03 + sine(50, 0.006) + fband(pink(300, 2000), 400, 1500) * am(0.5, 0.5, 0.5) * 0.04

    elif preset == "romance":
        sig = sine(196, 0.012) + sine(247, 0.010) + sine(294, 0.008)
        sig += fband(pink(100, 2000), 120, 1500) * am(rng.uniform(0.04, 0.1), 0.3, 0.7) * 0.05

    elif preset == "relaxation":
        sig = sine(130, 0.014) + sine(164, 0.011) + sine(196, 0.009)
        sig += fband(pink(100, 1800), 120, 1500) * am(rng.uniform(0.03, 0.08), 0.3, 0.7) * 0.05

    elif preset == "warm_atmos":
        sig = fband(pink(80, 2000), 100, 1500) * am(rng.uniform(0.03, 0.08), 0.3, 0.7) * 0.12
        sig += sine(60, 0.008)

    elif preset == "cold_atmos":
        sig = fband(pink(200, 4000), 250, 3500) * am(rng.uniform(0.05, 0.12), 0.4, 0.6) * 0.1
        sig += pink(40, 400) * 0.03

    elif preset == "night_atmos":
        sig = fband(pink(50, 800), 60, 600) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.14
        sig += sine(50, 0.008)

    elif preset == "morning_atmos":
        sig = birds(nb=12, lo_f=1400, hi_f=5800) * 0.4
        sig += fband(pink(100, 2500), 120, 2000) * am(rng.uniform(0.04, 0.1), 0.25, 0.75) * 0.08

    # ── 🌙 Ambianțe temporale ────────────────────────────────────────────────
    elif preset == "morning":
        sig = birds(nb=14, lo_f=1200, hi_f=5800) * 0.45
        sig += fband(pink(100, 2500), 120, 2000) * am(rng.uniform(0.04, 0.1), 0.25, 0.75) * 0.08

    elif preset == "afternoon":
        sig = fband(pink(150, 3500), 180, 3000) * am(rng.uniform(0.04, 0.1), 0.25, 0.75) * 0.12
        sig += birds(nb=6, lo_f=1500, hi_f=5000) * 0.15

    elif preset == "evening":
        crk = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            freq = float(rng.uniform(2000, 3000))
            rate = float(rng.uniform(3, 5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 16
            crk += chirp * sine(freq, 0.12)
        sig = crk + fband(pink(60, 1200), 70, 1000) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.08

    elif preset == "quiet_house":
        sig = fband(pink(60, 1800), 70, 1400) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.1
        sig += sine(50, 0.008) + creak_sound(100, 300, int(rng.integers(0, 2)), 0.3, 0.8, 0.06)

    elif preset == "busy_house":
        murmur = fband(pink(140, 3200), 160, 2600) * am(rng.uniform(0.05, 0.12), 0.2, 0.8) * 0.3
        sig = murmur + clicks(int(rng.integers(3, 8)), 1000, 5000, 0.01, 0.04, 0.15)

    elif preset == "quiet_city":
        sig = fband(pink(40, 1500), 50, 1200) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.18
        sig += sine(50, 0.008)

    elif preset == "busy_city":
        sig = fband(pink(40, 1500), 50, 1200) * am(rng.uniform(0.05, 0.15), 0.25, 0.75) * 0.45
        sig += foley_rush(200, 3000, 4, 0.12)

    elif preset == "distant_rain":
        sig = fband(pink(150, 6000), 200, 5000) * am(rng.uniform(0.05, 0.12), 0.1, 0.9) * 0.18

    elif preset == "distant_storm":
        sig = fband(pink(25, 200), 30, 150) * am(rng.uniform(0.06, 0.15), 0.4, 0.6) * 0.2
        sig += fband(pink(150, 6000), 200, 5000) * 0.08

    elif preset == "distant_people":
        sig = fband(pink(200, 3000), 250, 2500) * am(rng.uniform(0.05, 0.15), 0.3, 0.7) * 0.14

    elif preset == "big_echo":
        sig = fband(pink(60, 2000), 80, 1500) * am(rng.uniform(0.03, 0.08), 0.3, 0.7) * 0.12
        sig += pink(30, 200) * 0.03

    elif preset == "small_echo":
        sig = fband(pink(200, 3000), 250, 2500) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.08

    elif preset == "big_room":
        sig = fband(pink(60, 1800), 80, 1400) * am(rng.uniform(0.03, 0.08), 0.3, 0.7) * 0.12

    elif preset == "small_room":
        sig = fband(pink(150, 2800), 200, 2200) * am(rng.uniform(0.03, 0.08), 0.2, 0.8) * 0.09

    elif preset == "long_corridor":
        sig = fband(pink(80, 2200), 100, 1800) * am(rng.uniform(0.03, 0.08), 0.25, 0.75) * 0.12
        sig += footsteps(float(rng.uniform(0.6, 1.4)), lo=250, hi=3000, amp=0.2)

    elif preset == "empty_space":
        sig = fband(pink(50, 1500), 60, 1200) * am(rng.uniform(0.03, 0.08), 0.3, 0.7) * 0.08
        sig += pink(25, 150) * 0.03

    # ── 🔊 Efecte diverse ─────────────────────────────────────────────────────
    elif preset == "object_lift":
        sig = foley_rush(300, 3000, float(rng.uniform(2, 4)), 0.2)

    elif preset == "object_put":
        sig = fband(pink(50, 1500), 60, 1200) * np.exp(-np.linspace(0, 8, n)) * 0.45
        sig += clicks(1, 400, 2500, 0.01, 0.05, 0.35)

    elif preset == "hit":
        sig = fband(pink(50, 2000), 60, 1500) * np.exp(-np.linspace(0, 5, n)) * 0.6
        sig += clicks(2, 300, 3000, 0.01, 0.06, 0.45)

    elif preset == "knock":
        sig = clicks(int(rng.integers(2, 5)), 400, 3000, 0.02, 0.07, 0.5)
        sig += fband(pink(80, 1500), 90, 1200) * np.exp(-np.linspace(0, 10, n)) * 0.25

    elif preset == "scratch":
        sig = foley_rush(1000, 6000, float(rng.uniform(4, 8)), 0.3)
        sig += clicks(int(rng.integers(2, 5)), 2000, 7000, 0.01, 0.04, 0.2)

    elif preset == "rub":
        sig = foley_rush(500, 4000, float(rng.uniform(3, 6)), 0.25)

    elif preset == "tear":
        sig = foley_rush(1500, 8000, float(rng.uniform(3, 6)), 0.3)
        sig += clicks(int(rng.integers(2, 5)), 2000, 8000, 0.01, 0.04, 0.25)

    elif preset == "unwrap":
        sig = foley_rush(800, 7000, float(rng.uniform(4, 8)), 0.3) + snap_sound(int(rng.integers(2, 5)), 2000, 7000, 0.35)

    elif preset == "close":
        sig = fband(pink(100, 2500), 150, 2000) * np.exp(-np.linspace(0, 8, n)) * 0.4
        sig += clicks(2, 800, 4000, 0.01, 0.04, 0.35)

    elif preset == "button_press":
        sig = clicks(1, 2000, 7000, 0.005, 0.02, 0.4) + clicks(1, 1500, 5000, 0.005, 0.02, 0.3)

    elif preset == "switch_flip":
        sig = clicks(2, 1500, 6000, 0.005, 0.03, 0.45)

    elif preset == "beep_electronic":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            blen = min(int(0.1 * sr), n - p)
            if blen > 0:
                sig[p:p + blen] += sine(2000, 0.15)[:blen] * np.exp(-np.linspace(0, 20, blen))
        sig += fband(pink(60, 1200), 70, 1000) * 0.06

    elif preset == "alarm_effect":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 8))):
            p = int(rng.integers(0, n))
            blen = min(int(0.3 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                sig[p:p + blen] += (np.sin(2 * np.pi * 800 * tl) + 0.5 * np.sin(2 * np.pi * 1200 * tl)) * np.sin(np.pi * np.linspace(0, 1, blen)) ** 0.3 * 0.16
        sig += fband(pink(60, 1500), 70, 1200) * 0.08

    elif preset == "vibration":
        sig = fband(pink(100, 2000), 120, 1500) * am(float(rng.uniform(20, 40)), 0.6, 0.4) * 0.3

    elif preset == "buzz":
        sig = fband(pink(200, 2500), 250, 2000) * am(float(rng.uniform(30, 60)), 0.5, 0.5) * 0.22
        sig += sine(float(rng.uniform(100, 200)), 0.02)

    elif preset == "mechanism":
        sig = clicks(int(rng.integers(3, 7)), 500, 3000, 0.01, 0.05, 0.35)
        sig += creak_sound(100, 400, int(rng.integers(1, 3)), 0.2, 0.7, 0.2)

    elif preset == "metal_hit":
        sig = metal_ring(float(rng.uniform(1500, 4000)), 0.1, 0.4, 0.3, 3) + clicks(2, 1000, 5000, 0.01, 0.04, 0.35)

    elif preset == "glass_touch":
        sig = metal_ring(float(rng.uniform(3000, 7000)), 0.03, 0.15, 0.2, int(rng.integers(2, 5))) + clicks(1, 2500, 7000, 0.005, 0.02, 0.2)

    elif preset == "ceramic_touch":
        sig = metal_ring(float(rng.uniform(2000, 5000)), 0.03, 0.15, 0.22, int(rng.integers(2, 5))) + clicks(1, 1500, 5000, 0.005, 0.03, 0.2)

    elif preset == "wood_hit":
        sig = clicks(2, 300, 2500, 0.01, 0.06, 0.5) + fband(pink(100, 1500), 150, 1200) * np.exp(-np.linspace(0, 8, n)) * 0.3

    elif preset == "paper_rip":
        sig = foley_rush(1500, 8000, float(rng.uniform(3, 6)), 0.3) + clicks(int(rng.integers(2, 5)), 2000, 8000, 0.01, 0.04, 0.25)

    elif preset == "brush_sweep":
        sig = foley_rush(1000, 5000, float(rng.uniform(4, 8)), 0.25)

    elif preset == "footsteps_dressing":
        sig = footsteps(float(rng.uniform(1.0, 1.8)), lo=200, hi=4000, amp=0.45)
        sig += foley_rush(300, 3500, 3, 0.1)

    # ── Natură și mediu: mare, pădure, grădină, parc, lac, munte, plajă, vreme, atmosfere ──
    elif preset == "splash":
        base = pink(100, 5000) * am(rng.uniform(0.08, 0.18), 0.25, 0.75) * 0.25
        spl = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.1, 0.35) * sr), n - p)
            if slen > 0:
                body = fband(rng.uniform(-1, 1, slen), 300, 4000)
                env = np.exp(-np.linspace(0, 6, slen)) * np.sin(np.pi * np.linspace(0, 1, slen))
                spl[p:p + slen] += body * env * float(rng.uniform(0.25, 0.5))
        sig = base + spl + water_bubble(int(rng.integers(2, 6)), 0.05, 0.12, 500, 2200, 0.10)

    elif preset == "breeze":
        sig = pink(120, 3500) * am(rng.uniform(0.06, 0.14), 0.35, 0.65) * 0.30

    elif preset == "shells":
        sig = clicks(int(rng.integers(8, 20)), 1200, 7000, 0.01, 0.05, 0.22)
        sig += foley_rush(800, 5000, float(rng.uniform(2, 4)), 0.12)

    elif preset == "waves_rocks":
        base = pink(50, 4500) * am(rng.uniform(0.08, 0.16), 0.45, 0.55) * 0.55
        w1 = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.06, 0.12)) * t)) ** 0.5
        crash = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.15, 0.5) * sr), n - p)
            if clen > 0:
                body = fband(rng.uniform(-1, 1, clen), 200, 3500)
                env = np.exp(-np.linspace(0, 4, clen))
                crash[p:p + clen] += body * env * float(rng.uniform(0.3, 0.55))
        sig = base * (0.6 + 0.5 * w1) + crash

    elif preset == "sea_cave":
        water = pink(40, 2500) * am(rng.uniform(0.05, 0.10), 0.30, 0.70) * 0.30
        echo = fband(pink(60, 1500), 80, 1400) * am(rng.uniform(0.08, 0.2), 0.4, 0.6) * 0.12
        drip = clicks(int(rng.integers(2, 6)), 800, 4000, 0.04, 0.12, 0.10)
        sig = water + echo + drip

    elif preset == "lighthouse":
        water = pink(55, 2500) * am(rng.uniform(0.05, 0.10), 0.25, 0.75) * 0.22
        horn = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.2 * n), int(0.7 * n)))
            hlen = min(int(rng.uniform(1.5, 3.5) * sr), n - p)
            if hlen > 0:
                tl = np.linspace(0, hlen / sr, hlen)
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.4
                horn[p:p + hlen] += np.sin(2 * np.pi * 130 * tl) * env * 0.18
        sig = water + horn

    elif preset == "boat_idle":
        engine = pink(30, 200) * am(rng.uniform(0.5, 0.9), 0.25, 0.75) * 0.42
        sputter = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.05, 0.15) * sr), n - p)
            if slen > 0:
                sputter[p:p + slen] += fband(rng.uniform(-1, 1, slen), 100, 900) * np.exp(-np.linspace(0, 10, slen)) * 0.2
        sig = engine + sputter

    elif preset == "boat_accel":
        tl = np.linspace(0, dur, n, endpoint=False)
        freq = 40 + 220 * np.clip(tl / dur, 0, 1)
        engine = np.sin(2 * np.pi * np.cumsum(freq) / sr) * 0.35
        rumble = pink(30, 300) * 0.30
        sig = engine * (0.5 + 0.5 * np.clip(tl / dur, 0, 1)) + rumble

    elif preset == "oars":
        spl = footsteps(float(rng.uniform(0.7, 1.2)), lo=400, hi=3000, amp=0.22)
        creak = creak_sound(250, 700, 4, 0.25, 0.7, 0.16)
        sig = spl * 0.6 + creak

    elif preset == "anchor_chain":
        sig = metal_ring(float(rng.uniform(900, 2000)), 0.08, 0.3, 0.22, 6)
        sig += clicks(int(rng.integers(4, 10)), 1500, 7000, 0.01, 0.04, 0.15)

    elif preset == "pontoon":
        water = pink(70, 2500) * am(rng.uniform(0.05, 0.12), 0.25, 0.75) * 0.22
        creak = creak_sound(150, 500, 3, 0.3, 1.0, 0.18)
        lap = footsteps(float(rng.uniform(0.8, 1.6)), lo=300, hi=2000, amp=0.14)
        sig = water + creak + lap * 0.5

    elif preset == "water_step":
        base = pink(80, 3000) * am(rng.uniform(0.06, 0.14), 0.2, 0.8) * 0.16
        steps = footsteps(float(rng.uniform(0.9, 1.5)), lo=300, hi=3000, amp=0.30)
        sig = base + steps * 0.7

    elif preset == "water_lap":
        base = pink(60, 2200) * am(rng.uniform(0.05, 0.12), 0.20, 0.80) * 0.24
        lap = footsteps(float(rng.uniform(0.6, 1.2)), lo=250, hi=1800, amp=0.18)
        sig = base + lap * 0.55

    elif preset == "insects":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            freq = float(rng.uniform(1200, 4500))
            sig += sine(freq, 0.05) * am(float(rng.uniform(3, 8)), 0.5, 0.5)
        sig += pink(60, 1500) * 0.05

    elif preset == "owl":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.7 * n)))
            hlen = min(int(rng.uniform(0.6, 1.2) * sr), n - p)
            if hlen > 0:
                tl = np.linspace(0, hlen / sr, hlen)
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.5
                f = 240 + 90 * tl / (hlen / sr)
                sig[p:p + hlen] += np.sin(2 * np.pi * f * tl) * env * 0.22
        sig += pink(40, 600) * 0.06

    elif preset == "ravens":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.1, 0.25) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                f = float(rng.uniform(500, 1100)) * (1 + 0.3 * np.sin(2 * np.pi * float(rng.uniform(8, 16)) * tl))
                sig[p:p + clen] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-np.linspace(0, 6, clen)) * float(rng.uniform(0.12, 0.22))

    elif preset == "fox":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            ylen = min(int(rng.uniform(0.08, 0.2) * sr), n - p)
            if ylen > 0:
                tl = np.linspace(0, ylen / sr, ylen)
                f = float(rng.uniform(600, 1400)) * (1 + 0.5 * np.sin(2 * np.pi * float(rng.uniform(15, 25)) * tl))
                sig[p:p + ylen] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-np.linspace(0, 10, ylen)) * float(rng.uniform(0.08, 0.16))

    elif preset == "deer":
        steps = footsteps(float(rng.uniform(1.0, 1.6)), lo=120, hi=2500, amp=0.16)
        snort = np.zeros(n)
        p = int(rng.integers(0, int(0.5 * n)))
        snl = min(int(rng.uniform(0.1, 0.2) * sr), n - p)
        if snl > 0:
            snort[p:p + snl] += fband(rng.uniform(-1, 1, snl), 300, 2500) * np.exp(-np.linspace(0, 8, snl)) * 0.14
        sig = steps + snort

    elif preset == "animals_far":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.15, 0.4) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                f = float(rng.uniform(300, 1500)) * (1 + 0.2 * np.sin(2 * np.pi * float(rng.uniform(3, 8)) * tl))
                sig[p:p + clen] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-np.linspace(0, 4, clen)) * float(rng.uniform(0.04, 0.09))
        sig += pink(50, 800) * 0.04

    elif preset == "fog":
        sig = pink(50, 1500) * am(rng.uniform(0.03, 0.08), 0.30, 0.70) * 0.10
        sig += fband(pink(200, 3000), 200, 2800) * 0.03

    elif preset == "gate_wood":
        sig = creak_sound(150, 600, int(rng.integers(2, 5)), 0.3, 1.0, 0.28)
        sig += fband(pink(70, 1200), 80, 1000) * np.exp(-np.linspace(0, 6, n)) * 0.2

    elif preset == "flies":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 8))):
            freq = float(rng.uniform(250, 900))
            on = np.abs(np.sin(2 * np.pi * float(rng.uniform(8, 20)) * t + rng.uniform(0, np.pi))) ** 8
            sig += on * sine(freq, 0.08)

    elif preset == "mosquitoes":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 6))):
            freq = float(rng.uniform(1500, 4000))
            on = np.abs(np.sin(2 * np.pi * float(rng.uniform(5, 12)) * t + rng.uniform(0, np.pi))) ** 12
            sig += on * sine(freq, 0.07)

    elif preset == "sprinkler":
        rot = np.maximum(0.0, np.sin(2 * np.pi * float(rng.uniform(0.4, 0.7)) * t)) ** 4
        sig = fband(pink(1200, 7000), 1300, 6800) * (0.3 + 0.7 * rot) * 0.30

    elif preset == "watering":
        sig = fband(pink(800, 6000), 900, 5800) * am(float(rng.uniform(1.5, 3)), 0.3, 0.7) * 0.16

    elif preset == "lawnmower":
        engine = pink(40, 350) * am(rng.uniform(0.8, 1.4), 0.15, 0.85) * 0.4
        blade = pink(900, 4000) * am(rng.uniform(3, 6), 0.3, 0.7) * 0.16
        sig = engine + blade

    elif preset == "rake":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 7))):
            p = int(rng.integers(0, n))
            rlen = min(int(rng.uniform(0.3, 0.9) * sr), n - p)
            if rlen > 0:
                drag = foley_rush(400, 4000, float(rng.uniform(3, 7)), 0.28)
                env = np.sin(np.pi * np.linspace(0, 1, rlen)) ** 0.4
                sig[p:p + rlen] += drag[:rlen] * env

    elif preset == "shovel":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 6))):
            p = int(rng.integers(0, n))
            dlen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if dlen > 0:
                thud = fband(rng.uniform(-1, 1, dlen), 150, 2000) * np.exp(-np.linspace(0, 8, dlen)) * float(rng.uniform(0.2, 0.4))
                sig[p:p + dlen] += thud
                if p + dlen < n:
                    slen = min(int(0.15 * sr), n - p - dlen)
                    if slen > 0:
                        sig[p + dlen:p + dlen + slen] += foley_rush(500, 3000, 3, 0.12)[:slen]

    elif preset == "wheelbarrow":
        rattle = footsteps(float(rng.uniform(1.2, 2)), lo=250, hi=2500, amp=0.26)
        rumble = pink(60, 500) * am(rng.uniform(0.5, 0.9), 0.2, 0.8) * 0.16
        sig = rattle + rumble

    elif preset == "swing":
        creak = creak_sound(180, 600, 3, 0.2, 0.6, 0.18)
        whoosh = foley_rush(300, 2500, float(rng.uniform(1, 2)), 0.12) * am(float(rng.uniform(0.3, 0.5)), 0.8, 0.2)
        sig = creak + whoosh

    elif preset == "rocking_chair":
        rock = float(rng.uniform(0.4, 0.7))
        env = np.maximum(0.0, np.sin(2 * np.pi * rock * t + rng.uniform(0, np.pi))) ** 6
        sig = creak_sound(150, 500, 3, 0.15, 0.4, 0.2) * 0.6 + foley_rush(200, 2000, 2, 0.08) * env

    elif preset == "slide":
        whoosh = foley_rush(500, 4000, float(rng.uniform(2, 4)), 0.22)
        thud = np.zeros(n)
        p = int(rng.integers(int(0.5 * n), n))
        tlen = min(int(0.12 * sr), n - p)
        if tlen > 0:
            thud[p:p + tlen] += fband(rng.uniform(-1, 1, tlen), 120, 1800) * np.exp(-np.linspace(0, 10, tlen)) * 0.35
        sig = whoosh + thud

    elif preset == "barbecue":
        sizzle = np.zeros(n)
        for _ in range(int(rng.integers(10, 20))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.03, 0.12) * sr), n - p)
            if slen > 0:
                sizzle[p:p + slen] += fband(rng.uniform(-1, 1, slen), 2500, 9000) * np.exp(-np.linspace(0, 12, slen)) * float(rng.uniform(0.08, 0.2))
        crackle = footsteps(float(rng.uniform(5, 10)), lo=400, hi=4000, amp=0.2)
        sig = sizzle * 0.5 + crackle * 0.3

    elif preset == "stand_up":
        sig = foley_rush(300, 3500, float(rng.uniform(2, 4)), 0.16)
        sig += creak_sound(150, 450, 2, 0.2, 0.5, 0.12)
        sig += fband(pink(60, 800), 70, 700) * np.exp(-np.linspace(0, 10, n)) * 0.14

    elif preset == "children":
        murmur = fband(pink(300, 4500), 350, 4200) * am(rng.uniform(0.2, 0.5), 0.5, 0.5) * 0.10
        gl = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            glen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if glen > 0:
                tl = np.linspace(0, glen / sr, glen)
                f = float(rng.uniform(600, 2000)) * (1 + 0.6 * np.sin(2 * np.pi * float(rng.uniform(10, 20)) * tl))
                gl[p:p + glen] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-np.linspace(0, 5, glen)) * float(rng.uniform(0.05, 0.12))
        sig = murmur + gl

    elif preset == "ball_bounce":
        sig = footsteps(float(rng.uniform(1.5, 3)), lo=300, hi=3000, amp=0.30)
        sig += fband(pink(60, 1200), 70, 1100) * np.exp(-np.linspace(0, 8, n)) * 0.2

    elif preset == "pigeons":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.3, 0.7) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                f = 300 + 150 * np.sin(2 * np.pi * float(rng.uniform(2, 4)) * tl)
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.5
                sig[p:p + clen] += np.sin(2 * np.pi * np.cumsum(f) / sr) * env * float(rng.uniform(0.08, 0.16))
        sig += pink(50, 1000) * 0.04

    elif preset == "ducks":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            qlen = min(int(rng.uniform(0.08, 0.2) * sr), n - p)
            if qlen > 0:
                tl = np.linspace(0, qlen / sr, qlen)
                f = float(rng.uniform(300, 700)) * (1 + 0.8 * np.sin(2 * np.pi * float(rng.uniform(20, 40)) * tl))
                sig[p:p + qlen] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-np.linspace(0, 8, qlen)) * float(rng.uniform(0.10, 0.2))
        sig += pink(60, 1500) * 0.05

    elif preset == "fishing":
        whir = fband(pink(800, 3000), 900, 2800) * am(rng.uniform(2, 4), 0.3, 0.7) * 0.12
        splash = footsteps(float(rng.uniform(0.8, 1.4)), lo=400, hi=3000, amp=0.16)
        sig = whir + splash * 0.4

    elif preset == "rock_roll":
        rumble = pink(25, 400) * am(rng.uniform(0.3, 0.6), 0.3, 0.7) * 0.42
        rolls = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            rlen = min(int(rng.uniform(0.2, 0.6) * sr), n - p)
            if rlen > 0:
                tl = np.linspace(0, rlen / sr, rlen)
                f = float(rng.uniform(150, 700)) * (1 + 0.4 * np.sin(2 * np.pi * float(rng.uniform(5, 12)) * tl))
                rolls[p:p + rlen] += np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-np.linspace(0, 5, rlen)) * float(rng.uniform(0.15, 0.3))
        sig = rumble + rolls

    elif preset == "cabin":
        fire = pink(60, 2800) * am(rng.uniform(1.5, 3), 0.3, 0.7) * 0.22
        crackle = footsteps(float(rng.uniform(12, 20)), lo=500, hi=5000, amp=0.3)
        creak = creak_sound(140, 450, 2, 0.3, 0.8, 0.12)
        wind = pink(80, 1500) * am(rng.uniform(0.03, 0.07), 0.3, 0.7) * 0.08
        sig = fire + crackle * 0.3 + creak + wind

    elif preset == "foam":
        hiss = pink(1500, 9000) * am(rng.uniform(0.08, 0.16), 0.4, 0.6) * 0.16
        swell = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.07, 0.13)) * t)) ** 0.6
        sig = hiss * (0.3 + 0.7 * swell) + pink(50, 2000) * 0.08 * swell

    elif preset == "umbrella":
        flap = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            flen = min(int(rng.uniform(0.05, 0.15) * sr), n - p)
            if flen > 0:
                flap[p:p + flen] += foley_rush(300, 3500, float(rng.uniform(4, 8)), 0.2)[:flen]
        sig = flap + pink(120, 2500) * am(rng.uniform(0.05, 0.1), 0.3, 0.7) * 0.06

    elif preset == "lounger":
        sig = creak_sound(200, 700, 3, 0.15, 0.5, 0.18)
        sig += clicks(int(rng.integers(2, 5)), 1500, 6000, 0.01, 0.04, 0.12)
        sig += fband(pink(100, 2000), 120, 1800) * np.exp(-np.linspace(0, 8, n)) * 0.15

    elif preset == "jet_ski":
        two_stroke = np.zeros(n)
        for _ in range(int(rng.integers(3, 6))):
            freq = float(rng.uniform(300, 700))
            on = np.abs(np.sin(2 * np.pi * float(rng.uniform(10, 20)) * t + rng.uniform(0, np.pi))) ** 3
            two_stroke += on * sine(freq, 0.12)
        whine = fband(pink(1500, 5000), 1600, 4800) * am(rng.uniform(3, 5), 0.3, 0.7) * 0.12
        water = pink(80, 3000) * am(rng.uniform(0.08, 0.16), 0.3, 0.7) * 0.2
        sig = two_stroke + whine + water

    elif preset == "drizzle":
        base = pink(400, 7000) * am(rng.uniform(0.05, 0.12), 0.15, 0.85) * 0.30
        drops = footsteps(float(rng.uniform(6, 10)), lo=2000, hi=7000, amp=0.12)
        sig = base + drops * 0.5

    elif preset == "sleet":
        rain = pink(200, 6000) * am(rng.uniform(0.06, 0.14), 0.2, 0.8) * 0.3
        pellet = clicks(int(rng.integers(6, 12)), 3000, 8000, 0.01, 0.03, 0.12)
        sig = rain + pellet

    elif preset == "hail":
        rain = pink(150, 6000) * am(rng.uniform(0.1, 0.2), 0.25, 0.75) * 0.42
        pellets = footsteps(float(rng.uniform(20, 32)), lo=2500, hi=8000, amp=0.3)
        sig = rain + pellets * 0.6

    elif preset == "mysterious":
        drone = pink(40, 400) * am(rng.uniform(0.05, 0.1), 0.3, 0.7) * 0.16
        tone = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            tlen = min(int(rng.uniform(0.5, 1.5) * sr), n - p)
            if tlen > 0:
                tl = np.linspace(0, tlen / sr, tlen)
                f = float(rng.uniform(80, 300))
                tone[p:p + tlen] += np.sin(2 * np.pi * f * tl) * np.sin(np.pi * np.linspace(0, 1, tlen)) * float(rng.uniform(0.05, 0.12))
        sig = drone + tone

    else:  # "room" și orice preset necunoscut
        sig = pink(70, 3200) * 0.052 + sine(50, 0.016) + sine(100, 0.009)

    # Înmuiere globală: reduce tonurile pure/ascuțite ca să nu sune „electronic"/bip
    sig = _naturalize(sig, sr)
    # Normalizare + fade in/out pentru a evita clicuri la început/sfârșit
    sig = norm(sig)
    fade = int(0.04 * sr)
    if n > 2 * fade:
        sig[:fade] *= np.linspace(0, 1, fade)
        sig[-fade:] *= np.linspace(1, 0, fade)

    pcm = (np.clip(sig, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return output.getvalue()



# Toate numele de preseturi definite în _ambient_wav (extrase din codul acestui fișier),
# ca să putem expune orice preset direct din bibliotecă prin numele lui exact.
try:
    import re as _re_presets
    with open(__file__, encoding="utf-8") as _pf:
        AMBIENT_PRESET_NAMES = frozenset(
            _re_presets.findall(r'preset == "([a-z0-9_]+)"', _pf.read())
        )
except Exception:  # noqa
    AMBIENT_PRESET_NAMES = frozenset()


def sound_effect(prompt, duration=6.0, prompt_influence=0.45):
    """Returnează un sunet ambient sintetizat local; nu apelează niciun API extern."""
    text = str(prompt or "").lower()
    # Cale rapidă: dacă primim EXACT numele unui preset, îl folosim direct
    # (permite expunerea oricărui preset în bibliotecă fără să depindem de potrivirea pe cuvinte-cheie).
    _exact = text.strip()
    if _exact in AMBIENT_PRESET_NAMES:
        return _ambient_wav(_exact, duration=duration)
    presets = (
        # ── Forme exacte Casă/obiecte: câștigă înaintea legacy-ului (stairs/train/heels/balcony) ──
        ("keys_rummage",     ("chei răscolite", "răscolește cheile", "caută cheile", "scoate cheile din geantă",
                              "răscolește prin geantă după chei", "caută cheile în geantă",
                              "rummage keys", "keys rummaged", "fumble keys")),
        ("keys_drop",        ("chei scăpate", "scapă cheile", "cheile cad", "chei cad pe jos", "chei căzute",
                              "drop the keys", "keys dropped")),
        ("keys_put",         ("chei puse pe masă", "pune cheile pe masă", "așază cheile pe masă",
                              "keys put on the table", "keys on the table")),
        ("keys_bag",         ("pune cheile în geantă", "pune cheile în buzunar")),
        ("door_handle",      ("clanță apăsată", "apasă clanța", "apasă pe clanță")),
        ("door_balcony",     ("ușă de balcon", "ușa de balcon", "balcony door", "ușă glisantă")),
        ("door_cabinet",     ("ușă de dulap", "ușa de dulap", "cabinet door opens")),
        ("door_fridge",      ("ușă de frigider", "ușa frigiderului", "deschide frigiderul",
                              "fridge door opens", "fridge door")),
        ("door_oven",        ("ușă de cuptor", "ușa cuptorului", "deschide cuptorul")),
        ("door_lift",        ("ușă de lift", "ușa liftului", "elevator door")),
        ("door_train",       ("ușă de tren", "ușa trenului", "train door")),
        ("door_plane",       ("ușă de avion", "ușa avionului", "airplane door")),
        ("door_room",        ("ușă de cameră", "ușa camerei", "room door", "ușă de la cameră")),
        ("floor_creak",      ("podea care scârțâie", "podeaua scârțâie", "floor creaking")),
        ("furniture_move",   ("mobilier mutat", "muta mobilierul", "mută mobilierul", "moving furniture")),
        ("chair_pull",       ("scaun tras", "scaunul tras", "chair pulled")),
        ("chair_push",       ("scaun împins", "scaunul împins", "chair pushed")),
        ("table_touch",      ("masă atinsă", "atinge masa", "bate ușor în masă", "tapping the table")),
        ("object_fall",      ("obiect căzut", "obiectul a căzut", "obiect căzut pe jos", "ceva a căzut",
                              "something fell", "something dropped")),
        # ── Sunete contextuale suplimentare: casă și obiecte ──────────────────
        ("door_slam",          ("ușă trântit", "door slam", "trântește ușa", "ușa se trântește")),
        ("door_train",         ("ușa trenului", "train door opens")),
        ("train_doors",        ("uși tren", "train doors", "închidere uși tren")),
        ("door_creak_open",    ("ușa se deschide", "door opens", "ușă scârțâie", "door creak", "scârțâit ușă")),
        ("bus_doors",          ("uși autobuz", "bus doors")),
        ("door_plane",         ("ușă avion", "plane door", "avion ușă")),
        ("door_lift",          ("uși lift", "elevator doors", "lift doors")),
        ("hotel_lift",         ("lift hotel", "hotel elevator", "ascensor hotel")),
        ("door_cabinet",       ("ușă dulap", "cabinet door", "dulap ușă")),
        ("door_fridge",        ("ușă frigider", "fridge door", "frigider")),
        ("door_oven",          ("ușă cuptor", "oven door", "cuptor")),
        ("door_bathroom",      ("ușă baie", "bathroom door")),
        ("car_door",           ("ușă mașină", "car door", "portieră")),
        ("garage_door",        ("ușă garaj", "garage door")),
        ("clinic_door",        ("ușă clinică", "clinic door", "cabinete medicale")),
        ("door_handle",        ("clanță", "door handle", "mâner ușă")),
        ("door_close",         ("ușa se închide", "door close", "door shuts", "închide ușa")),
        ("keys_put",           ("chei pe masă", "keys on table", "pune cheile")),
        ("keys_bag",           ("chei în geantă", "keys bag")),
        ("keys_jingle",        ("cheile", "keys jingle", "keyring", "chei zăngănesc", "zăngănit de chei")),
        ("floor_creak",        ("parchet scârțâie", "floor creak", "podea scârțâie")),
        ("furniture_move",     ("mobilă", "furniture move", "mută mobila")),
        ("chair_pull",         ("scaun trage", "chair pull", "trage scaunul")),
        ("chair_push",         ("scaun împinge", "chair push", "împinge scaunul")),
        ("table_touch",        ("masă ating", "table touch")),
        ("object_fall",        ("obiect cade", "object fall", "lucru cade", "cade pe jos")),
        ("object_break",       ("obiect sparge", "object break", "ceva se sparge", "sparge")),
        ("glass_put",          ("pahar pe masă", "glass put", "pahar așezat")),
        ("box_open",           ("cutie deschide", "deschide cutia", "desface cutia")),
        ("box_close",          ("cutie închide", "închide cutia")),
        ("packaging",          ("ambalaj", "packaging", "pachet desface")),
        ("tape_peel",          ("bandă adezivă", "tape peel", "scoci")),
        ("pen_write",          ("pix scrie", "pen write", "scrie cu pixul")),
        ("paper_rustle",       ("hârtie foșnesc", "paper rustle", "hârtie foșnet")),
        ("paperclip",          ("aglutină", "paperclip", "clamă hârtie")),
        ("rubber_band",        ("elastic", "rubber band")),
        # ── Haine ─────────────────────────────────────────────────────────────
        ("fabric_rustle",      ("țesătură", "fabric rustle", "haine foșnesc", "material foșnet")),
        ("clothes_put",        ("haine pune", "clothes put", "îmbracă")),
        ("jacket_zip",         ("geacă fermoar", "jacket zip")),
        ("bag_zip",            ("geantă fermoar", "bag zip")),
        ("boot_zip",           ("cizmă fermoar", "boot zip", "ghete")),
        ("button",             ("nasture", "nasturi")),
        ("belt_buckle",        ("curea", "belt buckle", "cataramă")),
        ("socks",              ("șosete", "socks")),
        ("medical_gloves",     ("mănuși medicale", "medical gloves", "mănuși latex")),
        ("gloves_put",         ("mănuși", "gloves")),
        ("scarf",              ("eșarfă", "scarf", "fular")),
        ("hanger",             ("umeraș", "hanger")),
        ("closet",             ("dulap haine", "closet", "garderob")),
        ("clothes_fold",       ("haine împăturit", "clothes fold", "împăturește haine")),
        ("clothes_bag",        ("haine în geantă", "clothes bag")),
        # ── Genți și bagaje ───────────────────────────────────────────────────
        ("suitcase_zip",       ("valiză fermoar", "suitcase zip")),
        ("suitcase_wheels",    ("valiză roți", "suitcase wheels", "valiză pe roți")),
        ("suitcase_open",      ("valiză", "suitcase open", "deschide valiza")),
        ("bag_open",           ("geantă deschide", "bag open", "deschide geanta")),
        ("wallet",             ("portofel", "wallet")),
        ("money",              ("bani", "money", "bancnote")),
        ("coins",              ("monede", "coins")),
        ("card_swipe",         ("card bancar", "card swipe", "card de credit", "cardul")),
        ("bag_put",            ("geantă pe masă", "bag put", "pune geanta")),
        ("luggage_lift",       ("bagaj ridică", "luggage lift", "ridică bagajul")),
        ("zipper",             ("fermoar", "zipper", "zip")),
        # ── Baie și igienă ────────────────────────────────────────────────────
        ("toilet_seat",        ("capac toaletă", "toilet seat")),
        ("toilet_flush",       ("toaletă", "toilet flush", "spălare toaletă")),
        ("sink_water",         ("chiuvetă", "sink water", "apă chiuvetă")),
        ("bath_fill",          ("cadă umple", "bath fill", "umple cada")),
        ("drain",              ("scurgere", "drain", "apă se scurge")),
        ("cosmetic_pump",      ("pompiță", "cosmetic pump", "pompa cremă")),
        ("tube_squeeze",       ("tub", "tube squeeze", "stoarce tub")),
        ("toothbrush",         ("periuță", "toothbrush", "spălat dinți")),
        ("rinse_cup",          ("pahar clătire", "rinse cup", "clătește")),
        ("mirror_steam",       ("oglindă aburit", "mirror steam")),
        ("electric_razor",     ("brici electric", "electric razor")),
        ("razor",              ("aparat ras", "razor")),
        ("epilator",           ("epilator", "epilation")),
        ("tweezers",           ("pensetă", "tweezers", "pensat")),
        # ── Salon de înfrumusețare ────────────────────────────────────────────
        ("scissors",           ("foarfece", "scissors")),
        ("clippers",           ("mașină tuns", "clippers", "tuns mașină")),
        ("hair_cut",           ("tuns", "haircut", "tuns părul")),
        ("rotating_brush",     ("perie rotativ", "rotating brush")),
        ("flat_iron",          ("placa par", "flat iron", "netezi parul")),
        ("hair_spray",         ("spray păr", "hair spray", "spray de păr")),
        ("comb_put",           ("pieptene", "comb")),
        ("salon_chair",        ("scaun salon", "salon chair")),
        # ── Cosmetice și machiaj ──────────────────────────────────────────────
        ("makeup_open",        ("ruj deschide", "makeup open", "deschide rujul")),
        ("makeup_close",       ("ruj închide", "makeup close", "închide rujul")),
        ("brush_tap",          ("perie machiaj", "brush tap", "pensulă")),
        ("foundation_pump",    ("fond de ten", "foundation")),
        ("concealer",          ("corector", "concealer")),
        ("bronzer",            ("bronzer", "bronzant")),
        ("brow_pencil",        ("creion sprâncene", "brow pencil")),
        ("mascara",            ("mascara", "rimel")),
        ("lash_glue",          ("lipici gene", "lash glue")),
        ("sponge",             ("burete", "sponge")),
        ("cotton_pad",         ("disc demachiant", "cotton pad", "vată")),
        ("cream_apply",        ("aplică crema", "cream rub", "cream apply")),
        ("serum_drop",         ("serum", "serum drop", "picături serum")),
        ("face_mask",          ("mască facială", "face mask")),
        # ── Parfum și îngrijire ───────────────────────────────────────────────
        ("perfume_wrist",      ("parfum la încheietură", "perfume wrist")),
        ("perfume_spray",      ("spray parfum", "parfum", "perfume")),
        ("body_spray",         ("spray corp", "body spray")),
        ("spray_mist",         ("spray fin", "mist", "ceață spray")),
        ("bottle_cap",         ("capac sticlă", "bottle cap", "desfac capac")),
        ("roll_on",            ("roll on", "deodorant roll")),
        ("stick_deo",          ("stick deodorant", "deodorant stick")),
        ("deodorant_spray",    ("deodorant", "deodorant spray")),
        ("hand_cream",         ("cremă mâini", "hand cream")),
        ("perfume_wrist",      ("parfum la încheietură", "perfume wrist")),
        # ── Unghii ────────────────────────────────────────────────────────────
        ("polish_open",        ("ojă deschide", "polish open")),
        ("polish_brush",       ("aplică ojă", "polish brush")),
        ("polish_shake",       ("ojă", "nail polish shake", "agită ojă")),
        ("nail_file",          ("pilă unghii", "nail file", "pile unghii")),
        ("nail_clipper",       ("clește unghii", "nail clipper", "taie unghii")),
        ("cuticle",            ("cuticula", "cuticle")),
        # ── Bijuterii ─────────────────────────────────────────────────────────
        ("jewelry_box_close",  ("cutie bijuterii închide", "jewelry box close")),
        ("jewelry_box_open",   ("cutie bijuterii", "jewelry box open")),
        ("earrings",           ("cercei", "earrings")),
        ("bracelet",           ("brățară", "bracelet")),
        ("necklace",           ("colier", "necklace", "lanț gât")),
        ("ring_put",           ("inel", "ring put", "pune inelul")),
        ("jewelry_clink",      ("bijuterii zăngănesc", "jewelry clink")),
        # ── Îmbrăcăminte încălțăminte ─────────────────────────────────────────
        ("shoe_takeoff",       ("scoate pantofii", "shoe takeoff", "descălțat")),
        ("shoe_box",           ("cutie pantofi", "shoe box")),
        ("shoe_put",           ("pantofi", "shoe put", "încălțat")),
        ("shoelace",           ("șireturi", "shoelace", "leagă șireturile")),
        ("sandals",            ("sandale", "sandals")),
        ("footsteps_stone",    ("pași piatră", "footsteps stone", "pași pe piatră")),
        ("footsteps_floor",    ("pași interior", "footsteps floor", "pași pe jos")),
        ("footsteps_dressing", ("pași dressing", "footsteps dressing", "pași vestiar")),
        # ── Spații interioare ─────────────────────────────────────────────────
        ("hall_steps",         ("pași hol școală", "hall steps", "coridor pași")),
        ("long_corridor",      ("coridor lung", "long corridor")),
        ("hotel_corridor",     ("coridor hotel", "hotel corridor")),
        ("hall",               ("hol", "coridor", "lobby clădire")),
        ("staircase",          ("scara interioară", "staircase", "trepte interioare")),
        ("empty_room",         ("cameră goală", "empty room", "odaie goală")),
        ("crowded_room",       ("cameră aglomerat", "crowded room", "cameră plină de oameni")),
        ("bedroom",            ("dormitor", "bedroom", "cameră de dormit")),
        ("dressing",           ("dressing", "vestiar")),
        ("balcony",            ("balcon", "balcony")),
        ("garage",             ("garaj", "garage")),
        ("basement",           ("subsol", "basement", "beci")),
        ("attic",              ("mansardă", "attic", "pod casă")),
        ("office_space",       ("open space", "office space", "spațiu birou")),
        ("mall_space",         ("spațiu mall", "mall space", "zgomot mall")),
        ("salon_space",        ("salon coafură", "hair salon", "salon înfrumusețare")),
        ("hotel",              ("hotel", "lobby hotel")),
        ("reception",          ("recepție", "reception")),
        ("parking",            ("parcare", "parking", "parking lot")),
        # ── Exterior și casă ──────────────────────────────────────────────────
        ("gate_open",          ("poarta se deschide", "gate open", "porțile se deschid")),
        ("gate_close",         ("poarta se închide", "gate close", "închide poarta")),
        ("gravel",             ("pietriș", "gravel", "pași pietriș")),
        ("grass",              ("iarbă", "grass", "pași iarbă")),
        ("leaves",             ("foșnet frunze", "frunze foșnesc", "leaves rustle")),
        ("branches",           ("crengi", "branches", "crengi scrâșnesc")),
        ("car_trunk",          ("portbagaj", "car trunk")),
        ("wipers",             ("ștergătoare", "wipers")),
        ("car_window",         ("geam mașină", "car window", "fereastră mașină")),
        ("engine_electric",    ("motor electric", "electric engine", "motor lin")),
        ("engine_diesel",      ("motor diesel", "diesel engine", "motor pornit", "motorul pornește")),
        # ── Transport ─────────────────────────────────────────────────────────
        ("train_brake",        ("frânare tren", "train brake", "frana trenului")),
        ("train_accel",        ("accelerare tren", "train accelerate", "tren pornește")),
        ("train_depart",       ("tren pleacă", "train depart")),
        ("train_pass",         ("tren trece", "train passing")),
        ("train_station",      ("tren stație", "train station")),
        ("train_interior",     ("interior tren", "train interior", "vagon interior")),
        ("station_announce",   ("anunț gară", "station announcement", "anunț peron")),
        ("crowd_station",      ("peron aglomerat", "station crowd", "mulțime peron")),
        ("metro_station",      ("stație metrou", "metro station")),
        ("transport_announce", ("anunț transport", "transport announcement")),
        ("airport_extra",      ("aeroport zgomot", "airport hall", "terminal zgomot")),
        ("baggage_belt",       ("bandă bagaje", "baggage belt", "bagaje carusel")),
        ("luggage_cart",       ("cărucior bagaje", "luggage cart", "cărucior")),
        ("plane_cabin",        ("cabina avion", "plane cabin")),
        ("seatbelt",           ("centura de siguranță", "seatbelt", "se ataseaza centura")),
        # ── Oraș ──────────────────────────────────────────────────────────────
        ("crowd_moving",       ("oameni în mișcare", "crowd moving")),
        ("people_walk",        ("oameni care merg", "people walking", "transeunte")),
        ("people_run",         ("oameni care aleargă", "people running")),
        ("trotinette",         ("trotinetă", "scooter", "trotineta")),
        ("tram",               ("tramvai", "tram")),
        ("distant_horn",       ("claxon depărtat", "distant horn", "claxoane")),
        ("distant_siren",      ("sirenă depărtată", "distant siren", "sirene depărtate")),
        ("traffic_light",      ("semafor", "traffic light", "bip semafor")),
        ("crosswalk",          ("trecere de pietoni", "crosswalk", "zebra")),
        ("roadwork",           ("lucrări drum", "roadwork", "drum în lucru")),
        ("jackhammer",         ("ciocan pneumatic", "jackhammer", "pneumatic")),
        ("construction_extra", ("șantier zgomot", "construction noise")),
        ("distant_cars",       ("mașini depărtate", "distant cars", "trafic depărtat")),
        ("night_traffic",      ("trafic nocturn", "night traffic", "trafic noaptea")),
        ("market_square",      ("piața centrală", "market square", "piața oraș")),
        ("park_space",         ("parc public", "city park", "parc oraș")),
        ("fountain_water",     ("fântână arteziană", "fountain water", "fântână oraș")),
        # ── Școală și birou ───────────────────────────────────────────────────
        ("classroom",          ("sală de clasă", "classroom", "clasa elevi")),
        ("chairs_move",        ("scaune mișcate", "chairs moving", "scaune mută")),
        ("chalk_board",        ("cretă tablă", "chalk board", "scrie pe tablă")),
        ("board_wipe",         ("șterge tabla", "board wipe", "tabla sterge")),
        ("notebook",           ("caiet", "notebook")),
        ("page_turn",          ("pagina întoarce", "page turn", "răsfoiește")),
        ("page_rip",           ("pagina rupe", "page rip", "rupe pagina")),
        ("backpack",           ("ghiozdan", "backpack", "rucsac")),
        ("students",           ("elevii", "students", "școlari", "elevi la școală")),
        ("school_bell",        ("clopoțel școală", "school bell", "suna clopoțelul")),
        ("computer_typing",    ("tastare calculator", "computer typing", "taste calculator")),
        ("printer_extra",      ("alimentare hârtie", "printer noise", "imprimanta scoate pagini")),
        ("desk_phone",         ("telefon birou", "desk phone", "telefon fix")),
        # ── Medical ───────────────────────────────────────────────────────────
        ("wheelchair",         ("scaun cu rotile", "wheelchair")),
        ("stretcher",          ("targă", "stretcher", "pat de targă")),
        ("stethoscope",        ("stetoscop", "stethoscope")),
        ("blood_pressure",     ("tensiune arterială", "blood pressure", "măsoară tensiunea")),
        ("monitor_alarm",      ("alarmă monitor", "monitor alarm", "alarma aparaturii")),
        ("monitor_beep",       ("bip monitor", "monitor beep", "monitor pacienți")),
        ("syringe",            ("seringă", "syringe", "injecție")),
        ("medical_pack",       ("trusa medicală", "medical pack", "trusă de prim ajutor")),
        ("sanitizer",          ("dezinfectant", "sanitizer", "gel de mâini")),
        ("curtain_draw",       ("perdea trasă", "curtain draw", "draperii", "trage draperia")),
        # ── Divertisment ──────────────────────────────────────────────────────
        ("applause_soft",      ("aplauze discrete", "soft applause", "aplauze lin")),
        ("laugh",              ("râs", "laugh", "râsete")),
        ("giggle",             ("chicot", "giggle", "chicoteli")),
        ("whisper",            ("șoaptă", "whisper", "șoptit")),
        ("whistle",            ("fluier", "whistle", "fluierat")),
        ("boo",                ("huo", "boo crowd")),
        ("stage",              ("scenă", "stage")),
        ("speaker_feedback",   ("feedback microfon", "speaker feedback", "zgomot microfon")),
        ("microphone",         ("microfon", "microphone")),
        ("dance",              ("dans", "dance", "dansatori")),
        ("club",               ("club de noapte", "club", "nightclub")),
        ("concert",            ("concert", "concerte")),
        ("theater",            ("teatru", "theater", "spectacol teatru")),
        ("cinema",             ("sală film", "cinematograf", "la cinema", "proiecție de film")),
        ("projector",          ("proiector", "projector")),
        # ── Emoții și stări ───────────────────────────────────────────────────
        ("breath_calm",        ("respirație calmă", "calm breathing", "respirație lină")),
        ("breath_agitated",    ("respirație agitată", "agitated breathing", "respirație grea")),
        ("sigh",               ("oftat", "sigh", "suspin")),
        ("cry_soft",           ("plâns lin", "soft crying", "plânge încet")),
        ("cry_loud",           ("plâns", "crying", "plânge tare", "bocește")),
        ("heartbeat_fast",     ("bătăi inimă rapide", "fast heartbeat", "inima bate repede")),
        ("tremble",            ("tremurat", "tremble", "frisoane")),
        ("silence_tension",    ("tensiune liniște", "tense silence", "liniște tensionată")),
        ("romance",            ("romantism", "romance", "atmosferă romantică")),
        ("relaxation",         ("relaxare", "relaxation", "liniște relaxant")),
        ("warm_atmos",         ("atmosferă caldă", "warm atmosphere", "ambient cald")),
        ("cold_atmos",         ("atmosferă rece", "cold atmosphere", "ambient rece")),
        ("night_atmos",        ("atmosferă nocturnă", "night atmosphere", "noapte ambient")),
        ("morning_atmos",      ("atmosferă de dimineață", "morning atmosphere", "ambient dimineață")),
        # ── Ambiante temporale ────────────────────────────────────────────────
        ("morning",            ("dimineață liniștit", "quiet morning", "dimineață calmă")),
        ("afternoon",          ("după-amiază", "afternoon", "amiază")),
        ("evening",            ("seară liniștit", "evening calm", "seară calmă")),
        ("quiet_house",        ("casă liniștit", "quiet house", "casă calmă")),
        ("busy_house",         ("casă aglomerat", "busy house", "casă cu oameni")),
        ("quiet_city",         ("oraș liniștit", "quiet city", "liniște urbană")),
        ("busy_city",          ("oraș aglomerat", "busy city", "aglomerație urbană")),
        ("distant_rain",       ("ploaie depărtată", "distant rain", "ploaie în depărtare")),
        ("distant_storm",      ("furtună depărtată", "distant storm", "furtună în depărtare")),
        ("distant_people",     ("voci depărtate", "distant voices", "oameni în depărtare")),
        ("small_echo",         ("ecou mic", "small echo", "ecou încăpere mică")),
        ("big_echo",           ("ecou", "big echo", "sala ecou")),
        ("big_room",           ("sală mare", "big room", "încăpere mare")),
        ("small_room",         ("încăpere mică", "small room", "cameră mică")),
        ("empty_space",        ("spațiu gol", "empty space", "încăpere goală")),
        # ── Efecte ────────────────────────────────────────────────────────────
        ("object_lift",        ("obiect ridică", "object lift", "ridică obiectul")),
        ("object_put",         ("obiect pune", "object put", "pune obiectul")),
        ("hit",                ("lovitură", "lovește", "impact puternic")),
        ("knock",              ("bătut în ușă", "knock", "ciocăni")),
        ("scratch",            ("zgârietură", "scratch", "zgârie")),
        ("rub",                ("frecare", "rub", "frecat")),
        ("tear",               ("rupere", "tear", "sfâșiere")),
        ("unwrap",             ("despachetează", "unwrap", "desface cadou")),
        ("close",              ("închidere", "close sound", "se închide")),
        ("button_press",       ("buton apăsare", "button press", "apasă butonul")),
        ("switch_flip",        ("întrerupător", "switch flip", "comutator")),
        ("monitor_alarm",      ("alarmă monitor", "monitor alarm", "alarma aparaturii")),
        ("alarm_effect",       ("alarmă efect", "alarm beep", "bip de alarmă")),
        ("beep_electronic",    ("bip", "bip electronic", "electronic beep", "bip aparat")),
        ("vibration",          ("vibrație", "vibration", "vibrează")),
        ("buzz",               ("bâzâit", "buzz", "zâzâit")),
        ("mechanism",          ("mecanism", "mechanism", "angrenaj")),
        ("metal_hit",          ("metal lovit", "metal hit", "atingere metal")),
        ("glass_touch",        ("sticlă atingere", "glass touch", "atinge sticla")),
        ("ceramic_touch",      ("ceramică atingere", "ceramic touch")),
        ("wood_hit",           ("lemn lovit", "wood hit", "atinge lemnul")),
        ("paper_rip",          ("hârtie ruptă", "paper rip", "hârtie rupe")),
        ("brush_sweep",        ("mătura", "brush sweep", "măturat")),
        ("storm",              ("tunet", "furtun", "thunder", "storm", "lightning", "fulger", "grindină")),
        ("blizzard",           ("crivăț", "viscol", "blizzard", "howling wind", "vânt puternic", "uragan", "tornado", "vulcan")),
        ("rain_window",         ("ploaie geam", "rain window", "rain on window", "picături geam")),
        ("rain",                ("ploaie", "rain", "drizzle", "shower", "picături")),
        ("rainforest",          ("pădure tropical", "rainforest", "jungle rain", "tropical")),
        ("ocean_storm",         ("mare agitat", "ocean storm", "rough sea", "tsunami", "valuri cu rechini", "furtună mare")),
        ("ocean",               ("mare", "val", "ocean", "wave", "beach", "litoral", "coastă", "plajă", "insulă", "croazier")),
        ("fire",                ("foc", "campfire", "fire", "șemineu", "flacăr", "lumânare", "jar", "topitor", "hanuka")),
        ("wind_strong",         ("vânt puternic", "strong wind", "gale", "crivăț", "cosmic", "fundal cosmic", "munte", "vânt munte")),
        ("wind",                ("vânt", "wind", "breeze", "adiere", "suflare", "velier", "floarea soarelui")),
        ("forest_walk",         ("pași pădure", "walking forest", "footsteps leaves", "leaves underfoot",
                                 "crunch leaves", "rustling underfoot", "mers pădure", "foșnet pași",
                                 "drumeție", "munte pădure")),
        ("forest",              ("pădure", "forest", "frunze", "copac", "woods", "livadă", "veverițe")),
        ("autumn",               ("toamnă", "autumn", "frunze căzute", "fall leaves")),
        ("spring",               ("primăvară", "spring", "înflorire")),
        ("summer",               ("vară", "summer", "caniculă")),
        ("winter",               ("iarnă", "winter", "ger")),
        ("birds_morning",        ("dimineață păsări", "birds morning", "morning birds", "răsărit", "curcubeu", "dimineață țară", "grădină flori")),
        ("birds",                ("păsări", "birds", "fluturi", "păun", "gâște", "vulturi")),
        ("birds_lake",           ("lac rațe", "ducks", "birds lake", "lac păsări", "rațuște")),
        ("crickets_night",       ("noapte liniștit", "quiet night", "crickets night", "crepuscul")),
        ("crickets",             ("greier", "cricket", "seară câmp")),
        ("night",                ("noapte", "night", "pădure noapte", "halloween", "liliac")),
        ("night_city",           ("noapte oraș", "night city", "city night", "noapte în oraș")),
        ("lake",                 ("lac", "lake", "lebede", "stuf", "trestie")),
        ("fountain",             ("fântână", "fountain", "gheară", "izvor")),
        ("river",                ("râu", "river", "pârâu", "brook", "stream", "cascadă", "waterfall", "barca pescuit")),
        ("countryside_morning",  ("dimineață țară", "countryside morning", "morning countryside")),
        ("countryside_night",    ("noapte țară", "countryside night", "night countryside")),
        ("countryside",          ("țară", "sat", "countryside", "câmp", "rural", "livadă")),
        ("farm",                 ("fermă", "farm", "vaci", "cai", "porci", "oi", "găini", "curte")),
        ("frogs",                ("broaște", "frogs", "bălți", "pajiște broaște")),
        ("bees",                 ("albine", "bees", "stupi", "flori albine")),
        ("baby",                 ("bebeluș", "baby", "copil plânge", "hrănire bebeluș")),
        ("city_heavy",           ("trafic intens", "heavy traffic", "ambuteiaj", "intersecție", "semnalizare")),
        ("city",                 ("oraș", "city", "trafic", "traffic", "stradă", "street", "urban", "bulevard")),
        ("sirens",               ("siren", "sirens", "pompieri", "ambulanță", "poliție")),
        ("airport",              ("aeroport", "airport", "terminal aviatic")),
        ("metro",                ("metrou", "metro", "subway", "tramvai", "tram")),
        ("bus",                  ("autobuz", "bus", "camion")),
        ("cars",                 ("mașini", "cars", "taxi", "trecere")),
        ("cart",                 ("căruțe", "cart", "cai căruțe")),
        ("tractor",              ("tractor", "mecanic auto")),
        ("train",                ("tren", "train", "railroad", "railway", "șine", "vagon")),
        ("station_train_coming", ("tren gară", "train coming", "train arriving")),
        ("station",              ("gară", "station", "peron", "autogară")),
        ("boat",                 ("barcă", "boat", "vapor", "navă", "port", "harbor")),
        ("harbor",               ("port", "harbor", "dock")),
        ("cafe",                 ("cafenea", "cafe", "coffee shop", "bistro", "ceainărie", "berărie", "bar", "jazz", "degustare vin")),
        ("bakery",               ("brutărie", "bakery", "pâine")),
        ("restaurant",           ("restaurant", "dining")),
        ("store",                ("supermarket", "store", "magazin", "farmacie")),
        ("checkout",             ("casă marcat", "checkout", "scanare")),
        ("shopping_mall",        ("centru comercial", "shopping mall", "mall")),
        ("shopping_bags",       ("pungi cumpărături", "shopping bags", "sacoșe")),
        ("kitchen",              ("bucătărie", "kitchen")),
        ("coffee_machine",       ("preparare cafea", "coffee machine", "espresso")),
        ("tv",                   ("televizor", "tv", "film", "cinematic")),
        ("radio",                ("radio", "antenă radio")),
        ("typing",               ("tastatură", "typing", "calculator", "programare", "fax", "studio înregistrări")),
        ("vacuum",               ("aspirator", "vacuum")),
        ("washing",              ("mașină spălat", "washing machine", "spălare")),
        ("bathroom",             ("baie", "bathroom", "duș")),
        ("water_faucet",         ("robinet", "faucet", "apă curgă", "înot")),
        ("makeup",               ("machiaj", "makeup")),
        ("heels",                ("tocuri", "heels", "parchet", "parquet", "podea", "floor click",
                                   "toc pantof", "pantof cu toc", "lemn podea")),
        ("heely",                ("adidași", "sneakers", "heely")),
        ("footsteps",            ("pași", "footsteps", "mers", "alergare", "atletism")),
        ("footsteps_wood",       ("pași parchet", "footsteps wood", "pași lemn")),
        ("footsteps_tile",       ("pași gresie", "footsteps tile", "pași faianță")),
        ("footsteps_outside",    ("pași afară", "footsteps outside", "pași stradă")),
        ("stairs",               ("scări", "stairs", "lift")),
        ("chips",                ("chips", "ronțăit", "alune", "snacks")),
        ("eating",               ("mâncare", "eating", "ronțăit", "mastica")),
        ("drinking",             ("băut", "drinking", "sorbit")),
        ("cooking",              ("gătit", "cooking", "prăjit")),
        ("library",              ("bibliotecă", "library", "templu", "moschee", "parastas", "biserică")),
        ("office",               ("birou", "office", "server room", "centru date", "primărie", "bancă")),
        ("hospital",             ("spital", "hospital", "medical")),
        ("school",               ("școală", "school", "curs", "sală curs", "examen", "promoție")),
        ("party",                ("petrecere", "party", "concert", "orchestră", "karaoke", "fanfară", "nuntă", "anul nou", "crăciun", "moș crăciun", "darts", "circ")),
        ("crowd",                ("mulțime", "crowd", "stadion", "meci", "fotbal", "baschet", "tenis", "volei", "box", "teatru", "vernisaj", "copii joacă", "carusel")),
        ("heartbeat",            ("bătăi inimă", "heartbeat", "valentine", "injecție", "monitor cardiac", "stetoscop")),
        ("clock",                ("ceas", "clock", "clopote", "alarmă", "bec veghe", "clopote crăciun")),
        ("helicopter",           ("elicopter", "helicopter", "navă spațial", "ufo", "extraterestră")),
        ("dogs",                 ("câini", "dogs", "lătrat")),
        ("cats",                 ("pisici", "cats", "tors")),
        ("squirrels",            ("veverițe", "squirrels")),
        ("drilling",             ("dentist", "drilling", "freza", "bormașină")),
        ("phone",                ("notificări telefon", "phone", "telefon sună")),
        ("pool",                 ("piscină", "pool", "waterpolo")),
        ("gym",                  ("sală sport", "gym", "fitness", "greutăți")),
        ("ice",                  ("gheață", "ice", "patinaj", "patinoar")),
        ("shooting",             ("tir", "shooting", "airsoft", "arc săgeată", "foc armă")),
        ("explosion",            ("explozie", "explosion", "explozii", "bomă")),
        ("factory",              ("fabrică", "factory", "uzină", "oțelărie", "forjă")),
        ("construction",         ("construcții", "construction", "șantier", "minerit", "carieră")),
        ("arcade",               ("sala jocuri", "arcade", "jocuri retro")),
        ("dice",                 ("zaruri", "dice", "zar")),
        ("lab",                  ("laborator", "lab", "experimente", "chimie")),
        ("snow_walk",            ("pași zăpadă", "walking snow", "snow crunch", "footsteps snow",
                                  "snow underfoot", "zăpadă pași", "schi", "snowboard", "sanie")),
        ("snow",                 ("ninso", "zăpad", "snow", "iarnă liniș", "fulgi")),
        ("desert",               ("deșert", "desert", "nisi", "sand dune", "dună")),
        ("waterfall",            ("cascadă mare", "waterfall", "cădere apă", "niagară")),
        ("stream",               ("pârâu", "stream", "râu mic", "brooks", "apă lină")),
        ("rain_roof",            ("ploaie acoperiș", "rain roof", "ploaie tinichea", "ploaie tablă")),
        ("thunder_roll",         ("tunet depărtat", "thunder roll", "distant thunder", "bubuitură")),
        ("wind_chimes",          ("clopoței vânt", "wind chimes", "clopoței")),
        ("church_bells",         ("clopote biserică", "church bells", "clopot bisericesc")),
        ("temple_gong",          ("templu gong", "temple gong", "gong templu", "rugăciune")),
        ("meadow",               ("pajiște", "meadow", "câmp verde", "livadă flori", "prerie")),
        ("night_forest",         ("pădure noapte", "night forest", "forest night", "pădure întunecat")),
        ("underwater",           ("subacvatic", "underwater", "sub apă", "scufundare", "acvariu")),
        ("space",                ("spațiu cosmic", "deep space", "cosmos", "stele", "orbita")),
        ("cyberpunk",            ("cyberpunk", "futuristic city", "oraș futurist")),
        ("casino",               ("cazino", "casino", "slot machine", "păcănele")),
        ("market",               ("târg", "market", "piață", "bazar", "piața legume")),
        ("typewriter",           ("mașină scris", "typewriter", "tastare mecanic")),
        ("printer",              ("imprimantă", "printer", "printare")),
        ("fan",                  ("ventilator", "fan", "ventilație")),
        ("air_conditioning",     ("aer condiționat", "air conditioning", "ac unit", "climatizare")),
        ("cash_register",        ("casă ban", "cash register", "bancnot", "monede")),
        ("dishwasher",           ("mașină vase", "dishwasher", "spălat vase")),
        ("shower",               ("duș", "shower", "apă duș")),
        ("snore",                ("sforăit", "snore", "sforăi")),
        ("applause",             ("aplauze", "applause", "ovation")),
        ("cheering",             ("ovații", "cheering", "urare", "hooray", "urale")),
        ("motorcycle",           ("motociclet", "motorcycle", "motoreta")),
        ("bicycle",              ("biciclet", "bicycle", "clopoțel bicicletă", "velo")),
        ("organ",                ("orgă", "organ", "orgă biserică")),
        ("gong",                 ("gong", "tam-tam", "gong meditație")),
        # ── Aliasuri pentru forme flexionate / articulate în română ──────────
        ("door_cabinet",         ("ușa de la dulap", "ușa dulapului")),
        ("door_plane",           ("ușa avionului", "ușa de la avion")),
        ("door_handle",          ("clanța", "clanței")),
        ("clinic_door",          ("ușa de la clinică", "ușa clinicii")),
        ("furniture_move",       ("muta mobila", "mută mobila", "mobila")),
        ("glass_put",            ("paharul pe masă", "pune paharul")),
        ("tape_peel",            ("scoate banda", "banda adezivă")),
        ("paper_rustle",         ("hârtia foșnește", "hârtie foșnește", "foșnet de hârtie", "foșnet hârtie")),
        ("fabric_rustle",        ("materialul foșnește", "material foșnește", "foșnet de material", "foșnet material")),
        ("scarf",                ("eșarfa", "își înfășoară eșarfa")),
        ("toilet_seat",          ("capacul toaletei", "ridică capacul")),
        ("drain",                ("apa se scurge", "se scurge apa")),
        ("cosmetic_pump",        ("pompează crema", "pompează cremă")),
        ("toothbrush",           ("se spală pe dinți", "spală dinții")),
        ("mirror_steam",         ("oglinda se aburește", "oglinda aburită")),
        ("razor",                ("aparatul de ras",)),
        ("electric_razor",       ("briciul electric",)),
        ("tweezers",             ("penseta", "pensat sprâncene")),
        ("rotating_brush",       ("peria rotativă", "perie rotativă")),
        ("salon_chair",          ("scaunul de salon",)),
        ("brow_pencil",          ("creion de sprâncene",)),
        ("lash_glue",            ("lipici pentru gene",)),
        ("cotton_pad",           ("discul demachiant",)),
        ("face_mask",            ("masca facială",)),
        ("bottle_cap",           ("desface capacul sticlei", "capacul sticlei")),
        ("body_spray",           ("spray de corp",)),
        ("hand_cream",           ("cremă de mâini",)),
        ("polish_shake",         ("agită oja",)),
        ("polish_brush",         ("aplică oja",)),
        ("nail_file",            ("pila de unghii", "pila unghii")),
        ("nail_clipper",         ("cleștele de unghii", "cleștele unghii")),
        ("bracelet",             ("brățara",)),
        ("jewelry_clink",        ("bijuteriile zăngănesc",)),
        ("empty_room",           ("camera goală",)),
        ("crowded_room",         ("camera aglomerată", "camera plină de oameni")),
        ("attic",                ("mansarda",)),
        ("salon_space",          ("salonul de coafură",)),
        ("reception",            ("recepția",)),
        ("grass",                ("iarba", "pași pe iarbă")),
        ("engine_electric",      ("motorul electric",)),
        ("engine_diesel",        ("motorul diesel",)),
        ("baggage_belt",         ("banda de bagaje", "banda bagaje")),
        ("roadwork",             ("lucrări la drum",)),
        ("park_space",           ("parcul public", "parcul central", "grădina publică")),
        ("fountain_water",       ("fântâna arteziană",)),
        ("classroom",            ("sala de clasă",)),
        ("chairs_move",          ("scaunele mișcate", "scaunele se mișcă")),
        ("page_turn",            ("întoarce pagina", "întoarce o pagină")),
        ("school_bell",          ("clopoțelul școlii",)),
        ("wheelchair",           ("scaunul cu rotile",)),
        ("stretcher",            ("targa", "targa cu roți")),
        ("monitor_alarm",        ("alarma monitorului", "alarma aparatului")),
        ("syringe",              ("seringa", "seringa cu medicament")),
        ("whisper",              ("șoapte", "în șoaptă")),
        ("stage",                ("scena",)),
        ("tremble",              ("tremură", "îi tremură mâinile")),
        ("silence_tension",      ("tensiune în liniște",)),
        ("hit",                  ("lovitura", "o lovitură")),
        ("knock",                ("bate în ușă", "bate la ușă")),
        ("vibration",            ("vibrația", "vibrează telefonul")),
        ("paper_rip",            ("hârtia ruptă", "rupe hârtia")),
        ("brush_sweep",          ("mătură", "cu mătura")),
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)
