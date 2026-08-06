"""Generare vocală prin Chatterbox TTS (Hugging Face Space — gratuit, cloud).

Chatterbox clonează vocea direct din mostra audio — nu necesită transcrierea textului.
Folosim Space-ul oficial ResembleAI/Chatterbox de pe Hugging Face, apelat prin gradio_client.
Biblioteca de sunete ambientale (DSP cu numpy) rămâne neschimbată.
"""

import base64
import hashlib
import io
import math
import os
import random
import re
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

_voice_samples: dict = {}   # voice_id → (sample_bytes, suffix)
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
    _voice_samples[voice_id] = (sample, suffix)


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
    sample_bytes, suffix = info
    return _call_chatterbox_space(text, sample_bytes, suffix, exaggeration, cfg_weight)


def _generate_preview(text, sample_bytes, sample_name, exaggeration=0.5, cfg_weight=0.5):
    """Generează un preview direct din bytes (înainte de salvarea personajului)."""
    suffix = Path(str(sample_name or "reference.wav")).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"
    return _call_chatterbox_space(text, sample_bytes, suffix, exaggeration, cfg_weight)


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
    `reference_text` este ignorat — Chatterbox nu necesită transcriere.
    """
    return _generate_preview(expressify(text), sample_bytes, sample_name)


# ── Sinteza ambientală DSP (neschimbată) ─────────────────────────────────────

def _ambient_wav(preset, duration=12.0, sample_rate=22050):
    """DSP-based ambient synthesis using numpy. Fiecare apel sună ușor diferit (seed aleatoriu)."""
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

    else:  # "room" și orice preset necunoscut
        sig = pink(70, 3200) * 0.052 + sine(50, 0.016) + sine(100, 0.009)

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


def sound_effect(prompt, duration=6.0, prompt_influence=0.45):
    """Returnează un sunet ambient sintetizat local; nu apelează niciun API extern."""
    text = str(prompt or "").lower()
    presets = (
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
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)
