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

_voice_samples: dict = {}   # voice_id → (sample_bytes, suffix)
_client = None


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
    """
    from gradio_client import handle_file

    client = _get_client()

    # Salvează mostra într-un fișier temporar pentru upload
    tmp_path = _save_temp_sample(sample_bytes, suffix)
    try:
        result = client.predict(
            text[:300],
            handle_file(tmp_path),
            float(exaggeration),
            0.8,                # temperature (default recomandat)
            0,                  # seed = 0 → aleatoriu
            float(cfg_weight),
            False,              # vad_trim
            api_name="/generate_tts_audio",
        )
    except Exception as exc:
        msg = str(exc).lower()
        if "zerogpu" in msg or "quota" in msg:
            raise VoiceGenerationError(
                "Serviciul de voce a epuizat timpul de procesare gratuit pentru acum. "
                "Așteaptă 1-2 minute și încearcă din nou. Pentru utilizare intensă, "
                "configurează un token Hugging Face gratuit în setările aplicației."
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
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return _result_to_wav_bytes(result)


def _result_to_wav_bytes(result):
    """Convertește rezultatul Space-ului în WAV bytes.

    Space-ul poate returna:
      - un string (cale fișier WAV pe serverul Gradio) — descărcat și citit
      - un tuplu (sample_rate, numpy_array) — convertit direct
    """
    # Caz 1: string = cale fișier pe serverul Gradio → descărcăm conținutul
    if isinstance(result, str):
        return _download_gradio_file(result)

    # Caz 2: tuplu (sample_rate, numpy_array)
    if isinstance(result, (list, tuple)) and len(result) >= 2:
        sr, audio_np = result[0], result[1]
        return _numpy_to_wav(audio_np, int(sr))

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

    # ── Preseturi extinse (84 noi) ──────────────────────────────────────────

    elif preset == "wind_strong":
        w1 = pink(120, 5200) * am(rng.uniform(0.12, 0.30), 0.70, 0.30) * 0.78
        w2 = pink(400, 7000) * am(rng.uniform(0.18, 0.42), 0.60, 0.40) * 0.42
        gust = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            glen = min(int(rng.uniform(0.5, 2.0) * sr), n - p)
            if glen > 0:
                g = pink(200, 6000, glen) * np.sin(np.pi * np.linspace(0, 1, glen)) ** 0.3
                gust[p:p + glen] += g * float(rng.uniform(0.3, 0.6))
        sig = w1 + w2 + gust * 0.5

    elif preset == "ocean_storm":
        base = pink(40, 6000) * am(rng.uniform(0.08, 0.20), 0.45, 0.55) * 0.72
        crash = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.3, 1.2) * sr), n - p)
            if clen > 0:
                c = pink(100, 8000, clen) * np.exp(-np.linspace(0, 4, clen))
                crash[p:p + clen] += c * float(rng.uniform(0.4, 0.8))
        wind = pink(200, 5000) * am(rng.uniform(0.15, 0.35), 0.50, 0.50) * 0.35
        sig = base + crash * 0.55 + wind

    elif preset == "rain_window":
        base = pink(200, 7000) * am(rng.uniform(0.06, 0.16), 0.12, 0.88) * 0.40
        taps = footsteps(float(rng.uniform(20, 40)), lo=2000, hi=9000, amp=0.20)
        sig = base + taps * 0.30

    elif preset == "rainforest":
        leaves = pink(500, 7000) * am(rng.uniform(0.10, 0.25), 0.45, 0.55) * 0.28
        exo = birds(nb=16, lo_f=800, hi_f=6500) * 0.45
        drip = footsteps(float(rng.uniform(5, 12)), lo=1000, hi=5000, amp=0.15)
        sig = leaves + exo + drip * 0.25

    elif preset == "birds":
        sig = birds(nb=14, lo_f=1200, hi_f=6000) * 0.55
        sig += pink(200, 3000) * 0.06

    elif preset == "birds_morning":
        sig = birds(nb=20, lo_f=1000, hi_f=5500) * 0.50
        sig += pink(150, 2500) * am(rng.uniform(0.03, 0.08), 0.20, 0.80) * 0.10

    elif preset == "birds_lake":
        sig = birds(nb=10, lo_f=800, hi_f=4000) * 0.40
        sig += pink(60, 3000) * am(rng.uniform(0.05, 0.12), 0.25, 0.75) * 0.20
        quack = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            qlen = min(int(rng.uniform(0.08, 0.25) * sr), n - p)
            if qlen > 0:
                freq = float(rng.uniform(300, 600))
                tl = np.linspace(0, qlen / sr, qlen)
                env = np.exp(-np.linspace(0, 3, qlen))
                quack[p:p + qlen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.2, 0.4))
        sig += quack * 0.30

    elif preset == "crickets_night":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            freq = float(rng.uniform(2000, 3200))
            rate = float(rng.uniform(3.0, 5.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 14
            sig += chirp * sine(freq, 0.22)
        sig += pink(40, 800) * 0.04

    elif preset == "night":
        sig = pink(30, 500) * 0.03
        sig += birds(nb=4, lo_f=400, hi_f=2000) * 0.15
        crk = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            freq = float(rng.uniform(2000, 2800))
            rate = float(rng.uniform(3.5, 5.0))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 16
            crk += chirp * sine(freq, 0.15)
        sig += crk * 0.25

    elif preset == "night_city":
        traffic = fband(pink(40, 1200), 50, 1000) * am(rng.uniform(0.03, 0.08), 0.15, 0.85) * 0.25
        sig = traffic + birds(nb=3, lo_f=300, hi_f=1500) * 0.10

    elif preset == "spring":
        sig = birds(nb=18, lo_f=1500, hi_f=6000) * 0.45
        sig += pink(200, 4000) * am(rng.uniform(0.04, 0.10), 0.25, 0.75) * 0.12
        sig += footsteps(float(rng.uniform(3, 8)), lo=2000, hi=7000, amp=0.08)

    elif preset == "summer":
        sig = birds(nb=12, lo_f=1200, hi_f=5500) * 0.38
        sig += pink(100, 3000) * am(rng.uniform(0.05, 0.12), 0.30, 0.70) * 0.15
        crk = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            freq = float(rng.uniform(2200, 3000))
            rate = float(rng.uniform(4.0, 5.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 14
            crk += chirp * sine(freq, 0.18)
        sig += crk * 0.22

    elif preset == "autumn":
        leaves = pink(400, 6000) * am(rng.uniform(0.08, 0.20), 0.45, 0.55) * 0.22
        wind = pink(80, 2000) * am(rng.uniform(0.06, 0.14), 0.35, 0.65) * 0.12
        sig = leaves + wind + birds(nb=5, lo_f=800, hi_f=3500) * 0.20

    elif preset == "winter":
        wind = pink(100, 3000) * am(rng.uniform(0.05, 0.12), 0.40, 0.60) * 0.12
        sig = wind + birds(nb=3, lo_f=600, hi_f=2500) * 0.08

    elif preset == "countryside_morning":
        wind = pink(70, 2200) * am(rng.uniform(0.04, 0.09), 0.25, 0.75) * 0.12
        brd = birds(nb=18, lo_f=1000, hi_f=5500) * 0.48
        rooster = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.8 * n)))
            rlen = min(int(0.4 * sr), n - p)
            if rlen > 0:
                tl = np.linspace(0, rlen / sr, rlen)
                env = np.exp(-np.linspace(0, 2, rlen))
                freq = float(rng.uniform(500, 900))
                rooster[p:p + rlen] += np.sin(2 * np.pi * freq * tl) * env * 0.3
        sig = wind + brd + rooster * 0.35

    elif preset == "countryside_night":
        crk = np.zeros(n)
        for _ in range(int(rng.integers(5, 12))):
            freq = float(rng.uniform(2100, 3100))
            rate = float(rng.uniform(3.5, 5.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 14
            crk += chirp * sine(freq, 0.20)
        owl = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.2 * n), int(0.8 * n)))
            olen = min(int(0.5 * sr), n - p)
            if olen > 0:
                tl = np.linspace(0, olen / sr, olen)
                env = np.sin(np.pi * np.linspace(0, 1, olen))
                freq = float(rng.uniform(300, 500))
                owl[p:p + olen] += np.sin(2 * np.pi * freq * tl) * env * 0.25
        sig = crk * 0.55 + owl * 0.35 + pink(30, 400) * 0.03

    elif preset == "farm":
        sig = pink(80, 2500) * am(rng.uniform(0.04, 0.10), 0.20, 0.80) * 0.12
        moo = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            mlen = min(int(rng.uniform(0.5, 1.5) * sr), n - p)
            if mlen > 0:
                tl = np.linspace(0, mlen / sr, mlen)
                env = np.sin(np.pi * np.linspace(0, 1, mlen))
                freq = float(rng.uniform(150, 300))
                moo[p:p + mlen] += np.sin(2 * np.pi * freq * tl) * env * 0.25
        cluck = np.zeros(n)
        for _ in range(int(rng.integers(5, 12))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.05, 0.15) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(600, 1200))
                tl = np.linspace(0, clen / sr, clen)
                cluck[p:p + clen] += np.sin(2 * np.pi * freq * tl) * float(rng.uniform(0.1, 0.2))
        sig += moo * 0.30 + cluck * 0.20 + birds(nb=6, lo_f=800, hi_f=3500) * 0.15

    elif preset == "cart":
        sig = pink(50, 300) * am(rng.uniform(0.6, 1.2), 0.12, 0.88) * 0.30
        creak = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(80, 200))
                tl = np.linspace(0, clen / sr, clen)
                env = np.sin(np.pi * np.linspace(0, 1, clen))
                creak[p:p + clen] += np.sin(2 * np.pi * freq * tl) * env * 0.15
        sig += creak * 0.25 + footsteps(float(rng.uniform(1.0, 1.5)), lo=100, hi=2000, amp=0.20)

    elif preset == "tractor":
        engine = pink(40, 250) * am(rng.uniform(2.5, 4.5), 0.30, 0.70) * 0.42
        rattle = pink(300, 4000) * 0.12
        sig = engine + rattle

    elif preset == "frogs":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            freq = float(rng.uniform(200, 600))
            rate = float(rng.uniform(0.8, 2.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            croak = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 8
            sig += croak * sine(freq, 0.25)
        sig += pink(60, 2000) * 0.06

    elif preset == "lake":
        base = pink(50, 3000) * am(rng.uniform(0.05, 0.12), 0.20, 0.80) * 0.25
        sig = base + birds(nb=8, lo_f=700, hi_f=3500) * 0.30

    elif preset == "fountain":
        base = pink(2000, 10000) * am(rng.uniform(0.15, 0.30), 0.20, 0.80) * 0.30
        splash = footsteps(float(rng.uniform(15, 30)), lo=3000, hi=10000, amp=0.20)
        sig = base + splash * 0.25

    elif preset == "city_heavy":
        traffic = fband(pink(30, 1500), 40, 1200) * am(rng.uniform(0.05, 0.12), 0.25, 0.75) * 0.55
        hum = fband(pink(40, 100), 45, 95) * 0.20
        horns = np.zeros(n)
        for _ in range(int(rng.integers(2, 7))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(0.3, 2.5) * sr), n - p)
            if hlen > 0:
                freq = float(rng.uniform(250, 700))
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.28
                tl = np.linspace(0, hlen / sr, hlen)
                horns[p:p + hlen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.25, 0.55))
        brake = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.5, 1.5) * sr), n - p)
            if blen > 0:
                sc = pink(2000, 9000, blen) * np.exp(-np.linspace(0, 2, blen))
                brake[p:p + blen] += sc * 0.15
        sig = traffic + hum + horns * 0.40 + brake * 0.20

    elif preset == "sirens":
        wail = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, int(0.5 * n)))
            wlen = min(int(rng.uniform(2, 5) * sr), n - p)
            if wlen > 0:
                tl = np.linspace(0, wlen / sr, wlen)
                fmod = 400 + 300 * np.sin(2 * np.pi * 0.5 * tl)
                env = np.sin(np.pi * np.linspace(0, 1, wlen)) ** 0.3
                wail[p:p + wlen] += np.sin(2 * np.pi * fmod * tl) * env * 0.35
        sig = wail + pink(100, 2000) * 0.08

    elif preset == "airport":
        crowd = fband(pink(150, 3000), 160, 2500) * am(rng.uniform(0.04, 0.10), 0.20, 0.80) * 0.28
        jet = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, int(0.6 * n)))
            jlen = min(int(rng.uniform(3, 8) * sr), n - p)
            if jlen > 0:
                j = pink(40, 500, jlen) * np.sin(np.pi * np.linspace(0, 1, jlen)) ** 0.2
                jet[p:p + jlen] += j * 0.40
        pa = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.7 * n)))
            alen = min(int(rng.uniform(2, 5) * sr), n - p)
            if alen > 0:
                pa_n = fband(pink(300, 3500, alen), 300, 3500)
                syl = np.zeros(alen)
                sp = 0
                while sp < alen:
                    sd = int(rng.uniform(0.05, 0.18) * sr)
                    se = min(sp + sd, alen)
                    syl[sp:se] = float(rng.uniform(0.25, 0.9))
                    sp += sd + int(rng.uniform(0.02, 0.10) * sr)
                frame = np.sin(np.pi * np.linspace(0, 1, alen)) ** 0.28
                pa[p:p + alen] += pa_n * syl * frame * 0.30
        sig = crowd + jet * 0.45 + pa * 0.35

    elif preset == "metro":
        rumble = pink(25, 300) * am(rng.uniform(0.8, 1.5), 0.15, 0.85) * 0.48
        screech = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(1, 3) * sr), n - p)
            if slen > 0:
                sc = pink(2000, 8000, slen) * np.exp(-np.linspace(0, 3, slen))
                screech[p:p + slen] += sc * 0.15
        crowd = fband(pink(150, 2500), 160, 2200) * 0.12
        sig = rumble + screech + crowd

    elif preset == "bus":
        engine = pink(40, 300) * am(rng.uniform(1.5, 3.0), 0.20, 0.80) * 0.38
        rattle = pink(200, 3000) * 0.10
        sig = engine + rattle

    elif preset == "cars":
        passby = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            plen = min(int(rng.uniform(1, 3) * sr), n - p)
            if plen > 0:
                eng = pink(60, 400, plen) * np.sin(np.pi * np.linspace(0, 1, plen)) ** 0.3
                passby[p:p + plen] += eng * 0.30
        sig = passby + pink(100, 1500) * 0.08

    elif preset == "station_train_coming":
        crowd = fband(pink(180, 3000), 190, 2600) * 0.20
        approach = np.zeros(n)
        alen = int(min(rng.uniform(4, 8) * sr, n))
        rumble = pink(25, 500, alen)
        env = np.linspace(0, 1, alen) ** 0.5
        approach[:alen] = rumble * env * 0.45
        sig = crowd + approach

    elif preset == "harbor":
        water = pink(50, 3000) * am(rng.uniform(0.05, 0.12), 0.25, 0.75) * 0.25
        horn = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(1, 3) * sr), n - p)
            if hlen > 0:
                tl = np.linspace(0, hlen / sr, hlen)
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.3
                freq = float(rng.uniform(150, 350))
                horn[p:p + hlen] += np.sin(2 * np.pi * freq * tl) * env * 0.30
        sig = water + horn * 0.35 + pink(100, 2000) * 0.06

    elif preset == "boat":
        engine = pink(30, 250) * am(rng.uniform(1.0, 2.0), 0.18, 0.82) * 0.35
        water = pink(100, 4000) * am(rng.uniform(0.10, 0.25), 0.30, 0.70) * 0.15
        sig = engine + water

    elif preset == "bakery":
        murmur = fband(pink(140, 2800), 170, 2400) * am(rng.uniform(0.04, 0.10), 0.15, 0.85) * 0.28
        oven = pink(50, 500) * 0.08
        sig = murmur + oven + footsteps(float(rng.uniform(0.10, 0.30)), lo=2000, hi=8000, amp=0.12)

    elif preset == "restaurant":
        murmur = fband(pink(140, 3200), 170, 2600) * am(rng.uniform(0.05, 0.12), 0.18, 0.82) * 0.32
        clinks = footsteps(float(rng.uniform(0.10, 0.35)), lo=2000, hi=9000, amp=0.22)
        sig = murmur + clinks * 0.20

    elif preset == "store":
        murmur = fband(pink(150, 2800), 160, 2400) * 0.18
        beep = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            blen = min(int(0.1 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                beep[p:p + blen] += np.sin(2 * np.pi * 2000 * tl) * 0.10
        muzak = pink(300, 4000) * am(rng.uniform(0.3, 0.8), 0.20, 0.80) * 0.06
        sig = murmur + beep * 0.15 + muzak

    elif preset == "shopping_mall":
        crowd = fband(pink(150, 3000), 160, 2600) * am(rng.uniform(0.05, 0.12), 0.20, 0.80) * 0.25
        muzak = pink(300, 5000) * am(rng.uniform(0.4, 1.0), 0.25, 0.75) * 0.08
        footsteps_mall = footsteps(float(rng.uniform(0.5, 1.5)), lo=500, hi=4000, amp=0.10)
        sig = crowd + muzak + footsteps_mall * 0.15

    elif preset == "checkout":
        beep = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            blen = min(int(0.08 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                freq = float(rng.choice([2000, 2300, 1800]))
                beep[p:p + blen] += np.sin(2 * np.pi * freq * tl) * 0.12
        sig = beep + fband(pink(150, 2500), 160, 2200) * 0.10

    elif preset == "shopping_bags":
        rustle = np.zeros(n)
        for _ in range(int(rng.integers(8, 20))):
            p = int(rng.integers(0, n))
            rlen = min(int(rng.uniform(0.05, 0.2) * sr), n - p)
            if rlen > 0:
                r = pink(3000, 10000, rlen) * np.exp(-np.linspace(0, 5, rlen))
                rustle[p:p + rlen] += r * 0.15
        sig = rustle

    elif preset == "kitchen":
        sig = pink(100, 3000) * 0.08
        sizzle = pink(2000, 8000) * am(rng.uniform(0.5, 1.5), 0.20, 0.80) * 0.12
        chop = footsteps(float(rng.uniform(2, 5)), lo=500, hi=4000, amp=0.15)
        sig += sizzle + chop * 0.18

    elif preset == "coffee_machine":
        hiss = pink(2000, 9000) * am(rng.uniform(0.3, 0.8), 0.30, 0.70) * 0.20
        steam = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.5, 2.0) * sr), n - p)
            if slen > 0:
                s = pink(2000, 8000, slen) * np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.3
                steam[p:p + slen] += s * 0.15
        sig = hiss + steam

    elif preset == "tv":
        sig = pink(200, 8000) * am(rng.uniform(0.5, 2.0), 0.30, 0.70) * 0.12
        sig += footsteps(float(rng.uniform(0.05, 0.15)), lo=3000, hi=10000, amp=0.05)

    elif preset == "radio":
        sig = pink(300, 5000) * am(rng.uniform(0.3, 1.0), 0.25, 0.75) * 0.15
        static = pink(2000, 12000) * 0.04
        sig += static

    elif preset == "typing":
        sig = np.zeros(n)
        step = max(1, int(sr / float(rng.uniform(3, 8))))
        pos = int(rng.integers(0, step))
        while pos < n:
            clen = min(int(rng.uniform(0.005, 0.02) * sr), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 2000, 10000)
                sig[pos:pos + clen] += click * np.exp(-np.linspace(0, 20, clen)) * float(rng.uniform(0.1, 0.25))
            pos += step + int(rng.integers(-2, 3))
        sig += pink(100, 2000) * 0.03

    elif preset == "vacuum":
        sig = pink(200, 6000) * am(rng.uniform(2.0, 4.0), 0.15, 0.85) * 0.30
        sig += sine(120, 0.08) + sine(240, 0.04)

    elif preset == "washing":
        sig = pink(100, 2000) * am(rng.uniform(0.5, 2.0), 0.35, 0.65) * 0.20
        slosh = footsteps(float(rng.uniform(1, 3)), lo=200, hi=1500, amp=0.10)
        sig += slosh * 0.15

    elif preset == "bathroom":
        sig = pink(200, 5000) * am(rng.uniform(0.10, 0.25), 0.20, 0.80) * 0.10
        drip = footsteps(float(rng.uniform(0.5, 2.0)), lo=1000, hi=5000, amp=0.12)
        sig += drip * 0.15

    elif preset == "water_faucet":
        sig = pink(3000, 12000) * am(rng.uniform(0.15, 0.30), 0.10, 0.90) * 0.20
        sig += footsteps(float(rng.uniform(20, 40)), lo=4000, hi=12000, amp=0.08)

    elif preset == "makeup":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.02, 0.08) * sr), n - p)
            if blen > 0:
                brush = pink(2000, 8000, blen) * np.exp(-np.linspace(0, 8, blen))
                sig[p:p + blen] += brush * 0.12
        sig += pink(100, 1000) * 0.02

    elif preset == "heartbeat":
        sig = np.zeros(n)
        bpm = float(rng.uniform(60, 80))
        beat_int = 60.0 / bpm
        pos = 0.0
        while pos < dur:
            p = int(pos * sr)
            for off, amp_val in [(0, 0.45), (0.15, 0.30)]:
                bp = p + int(off * sr)
                blen = min(int(0.12 * sr), n - bp)
                if blen > 0 and bp >= 0:
                    tl = np.linspace(0, blen / sr, blen)
                    env = np.exp(-np.linspace(0, 8, blen))
                    freq = 60.0
                    sig[bp:bp + blen] += np.sin(2 * np.pi * freq * tl) * env * amp_val
            pos += beat_int

    elif preset == "clock":
        sig = np.zeros(n)
        tick_int = 1.0
        pos = float(rng.uniform(0, tick_int))
        while pos < dur:
            p = int(pos * sr)
            tlen = min(int(0.01 * sr), n - p)
            if tlen > 0 and p >= 0:
                tl = np.linspace(0, tlen / sr, tlen)
                sig[p:p + tlen] += np.sin(2 * np.pi * 4000 * tl) * np.exp(-np.linspace(0, 30, tlen)) * 0.15
            pos += tick_int

    elif preset == "helicopter":
        blade = sine(float(rng.uniform(12, 20)), 0.45) * am(float(rng.uniform(12, 20)), 0.50, 0.50)
        engine = pink(80, 800) * 0.20
        sig = blade + engine

    elif preset == "phone":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            blen = min(int(0.3 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                freq = float(rng.choice([800, 1000, 1200]))
                env = np.sin(np.pi * np.linspace(0, 1, blen)) ** 0.3
                sig[p:p + blen] += np.sin(2 * np.pi * freq * tl) * env * 0.15
        sig += pink(100, 2000) * 0.03

    elif preset == "dogs":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(4, 12))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.08, 0.3) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                freq = float(rng.uniform(300, 800))
                env = np.exp(-np.linspace(0, 3, blen))
                bark = np.sin(2 * np.pi * freq * tl) * env
                sig[p:p + blen] += bark * float(rng.uniform(0.25, 0.45))
        sig += pink(100, 2000) * 0.04

    elif preset == "cats":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            plen = min(int(rng.uniform(0.3, 1.0) * sr), n - p)
            if plen > 0:
                tl = np.linspace(0, plen / sr, plen)
                freq = float(rng.uniform(200, 400))
                env = np.sin(np.pi * np.linspace(0, 1, plen)) ** 0.5
                purr = (1 + 0.5 * np.sin(2 * np.pi * 25 * tl)) * np.sin(2 * np.pi * freq * tl)
                sig[p:p + plen] += purr * env * 0.20
        sig += pink(80, 1500) * 0.03

    elif preset == "squirrels":
        sig = birds(nb=12, lo_f=2000, hi_f=6000) * 0.30
        chatter = np.zeros(n)
        for _ in range(int(rng.integers(4, 10))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.03, 0.1) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(1500, 3500))
                tl = np.linspace(0, clen / sr, clen)
                chatter[p:p + clen] += np.sin(2 * np.pi * freq * tl) * 0.12
        sig += chatter * 0.20

    elif preset == "bees":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            freq = float(rng.uniform(180, 350))
            amp_val = float(rng.uniform(0.05, 0.12))
            ph = float(rng.uniform(0, 2 * np.pi))
            buzz = sine(freq, amp_val) * (1 + 0.3 * np.sin(2 * np.pi * float(rng.uniform(3, 8)) * t + ph))
            sig += buzz
        sig += pink(200, 2000) * 0.03

    elif preset == "crowd":
        murmur = fband(pink(150, 3000), 160, 2600) * am(rng.uniform(0.05, 0.15), 0.25, 0.75) * 0.35
        claps = np.zeros(n)
        for _ in range(int(rng.integers(2, 8))):
            p = int(rng.integers(0, n))
            clen = min(int(0.05 * sr), n - p)
            if clen > 0:
                cl = pink(1000, 8000, clen) * np.exp(-np.linspace(0, 10, clen))
                claps[p:p + clen] += cl * 0.15
        sig = murmur + claps * 0.15

    elif preset == "party":
        murmur = fband(pink(150, 3000), 160, 2600) * am(rng.uniform(0.08, 0.20), 0.30, 0.70) * 0.30
        music = pink(100, 5000) * am(rng.uniform(0.5, 1.5), 0.35, 0.65) * 0.15
        beat = footsteps(float(rng.uniform(0.5, 1.0)), lo=50, hi=200, amp=0.20)
        sig = murmur + music + beat * 0.20

    elif preset == "office":
        sig = pink(100, 2000) * 0.06
        sig += footsteps(float(rng.uniform(3, 8)), lo=2000, hi=8000, amp=0.05)
        ac = sine(60, 0.012)
        sig += ac

    elif preset == "library":
        sig = pink(80, 1500) * 0.04
        sig += footsteps(float(rng.uniform(0.1, 0.3)), lo=2000, hi=6000, amp=0.04)
        sig += sine(60, 0.008)

    elif preset == "hospital":
        sig = pink(80, 2000) * 0.05
        beep = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            blen = min(int(0.1 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                beep[p:p + blen] += np.sin(2 * np.pi * 1000 * tl) * 0.08
        sig += beep + sine(60, 0.010)

    elif preset == "school":
        murmur = fband(pink(150, 3000), 160, 2600) * am(rng.uniform(0.08, 0.18), 0.25, 0.75) * 0.22
        bell = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, n))
            rlen = min(int(rng.uniform(0.5, 2.0) * sr), n - p)
            if rlen > 0:
                tl = np.linspace(0, rlen / sr, rlen)
                freq = float(rng.uniform(400, 800))
                env = np.sin(np.pi * np.linspace(0, 1, rlen)) ** 0.3
                bell[p:p + rlen] += np.sin(2 * np.pi * freq * tl) * env * 0.15
        sig = murmur + bell * 0.20

    elif preset == "factory":
        machine = pink(50, 2000) * am(rng.uniform(1.0, 3.0), 0.25, 0.75) * 0.30
        clang = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.05, 0.2) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(200, 800))
                tl = np.linspace(0, clen / sr, clen)
                clang[p:p + clen] += np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 6, clen)) * 0.15
        sig = machine + clang * 0.20

    elif preset == "construction":
        drill = pink(2000, 8000) * am(rng.uniform(3, 8), 0.40, 0.60) * 0.20
        hammer = footsteps(float(rng.uniform(2, 5)), lo=500, hi=5000, amp=0.25)
        sig = drill + hammer * 0.25 + pink(50, 1000) * 0.08

    elif preset == "drilling":
        sig = pink(2000, 8000) * am(rng.uniform(5, 12), 0.35, 0.65) * 0.25
        sig += sine(float(rng.uniform(80, 150)), 0.10)

    elif preset == "explosion":
        sig = np.zeros(n)
        p = int(rng.integers(0, int(0.3 * n)))
        elen = min(int(rng.uniform(1, 3) * sr), n - p)
        if elen > 0:
            boom = pink(15, 2000, elen) * np.exp(-np.linspace(0, 3, elen))
            sig[p:p + elen] = boom * 0.60
        sig += pink(30, 500) * 0.10

    elif preset == "shooting":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            slen = min(int(0.08 * sr), n - p)
            if slen > 0:
                shot = pink(500, 10000, slen) * np.exp(-np.linspace(0, 20, slen))
                sig[p:p + slen] += shot * 0.25
        sig += pink(50, 1000) * 0.05

    elif preset == "lab":
        sig = pink(100, 3000) * 0.05
        sig += footsteps(float(rng.uniform(1, 3)), lo=2000, hi=8000, amp=0.06)
        beep = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, n))
            blen = min(int(0.05 * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                beep[p:p + blen] += np.sin(2 * np.pi * 1500 * tl) * 0.06
        sig += beep

    elif preset == "gym":
        sig = pink(100, 3000) * 0.08
        weights = footsteps(float(rng.uniform(0.3, 1.0)), lo=100, hi=2000, amp=0.25)
        breath = sine(float(rng.uniform(0.3, 0.8)), 0.04) * am(float(rng.uniform(0.3, 0.8)), 0.50, 0.50)
        sig += weights * 0.20 + breath

    elif preset == "pool":
        water = pink(500, 6000) * am(rng.uniform(0.3, 1.0), 0.30, 0.70) * 0.20
        splash = footsteps(float(rng.uniform(1, 3)), lo=1000, hi=8000, amp=0.15)
        sig = water + splash * 0.20

    elif preset == "ice":
        sig = pink(2000, 10000) * 0.06
        crack = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.05, 0.15) * sr), n - p)
            if clen > 0:
                cr = pink(2000, 8000, clen) * np.exp(-np.linspace(0, 8, clen))
                crack[p:p + clen] += cr * 0.12
        sig += crack

    elif preset == "dice":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            p = int(rng.integers(0, n))
            dlen = min(int(rng.uniform(0.03, 0.1) * sr), n - p)
            if dlen > 0:
                cl = pink(2000, 8000, dlen) * np.exp(-np.linspace(0, 12, dlen))
                sig[p:p + dlen] += cl * 0.15

    elif preset == "arcade":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(6, 15))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.05, 0.2) * sr), n - p)
            if blen > 0:
                tl = np.linspace(0, blen / sr, blen)
                freq = float(rng.choice([400, 600, 800, 1000, 1200]))
                env = np.exp(-np.linspace(0, 5, blen))
                sig[p:p + blen] += np.sin(2 * np.pi * freq * tl) * env * 0.10
        sig += pink(200, 4000) * 0.05

    elif preset == "baby":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.5, 2.0) * sr), n - p)
            if clen > 0:
                tl = np.linspace(0, clen / sr, clen)
                freq = float(rng.uniform(300, 600))
                env = np.sin(np.pi * np.linspace(0, 1, clen)) ** 0.3
                wail = (1 + 0.3 * np.sin(2 * np.pi * 5 * tl)) * np.sin(2 * np.pi * freq * tl)
                sig[p:p + clen] += wail * env * 0.20

    elif preset == "cooking":
        sizzle = pink(2000, 8000) * am(rng.uniform(0.5, 1.5), 0.20, 0.80) * 0.15
        chop = footsteps(float(rng.uniform(2, 5)), lo=500, hi=4000, amp=0.15)
        sig = sizzle + chop * 0.18 + pink(100, 2000) * 0.05

    elif preset == "eating":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(6, 15))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.03, 0.1) * sr), n - p)
            if clen > 0:
                cr = pink(500, 4000, clen) * np.exp(-np.linspace(0, 8, clen))
                sig[p:p + clen] += cr * 0.12

    elif preset == "chips":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(10, 20))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.01, 0.05) * sr), n - p)
            if clen > 0:
                cr = pink(3000, 10000, clen) * np.exp(-np.linspace(0, 15, clen))
                sig[p:p + clen] += cr * 0.10

    elif preset == "drinking":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.2, 0.5) * sr), n - p)
            if slen > 0:
                sw = pink(500, 4000, slen) * np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.3
                sig[p:p + slen] += sw * 0.10
        gulp = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(0, n))
            glen = min(int(0.1 * sr), n - p)
            if glen > 0:
                tl = np.linspace(0, glen / sr, glen)
                gulp[p:p + glen] += np.sin(2 * np.pi * 200 * tl) * np.exp(-np.linspace(0, 4, glen)) * 0.08
        sig += gulp

    elif preset == "footsteps":
        sig = footsteps(float(rng.uniform(1.0, 1.8)), lo=200, hi=5000, amp=0.55)

    elif preset == "footsteps_wood":
        sig = fband(footsteps(float(rng.uniform(1.0, 1.8)), lo=150, hi=4000, amp=0.55), 100, 5000)

    elif preset == "footsteps_tile":
        sig = fband(footsteps(float(rng.uniform(1.0, 1.8)), lo=300, hi=6000, amp=0.55), 200, 8000)

    elif preset == "footsteps_outside":
        sig = fband(footsteps(float(rng.uniform(1.0, 1.8)), lo=100, hi=3000, amp=0.50), 50, 4000)
        sig += pink(100, 2000) * 0.04

    elif preset == "heels":
        sig = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(1.3, 2.0))))
        pos = int(rng.integers(0, step_n // 2))
        while pos < n:
            clen = min(int(rng.uniform(0.006, 0.02) * sr), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 1500, 10000)
                sig[pos:pos + clen] += click * np.exp(-np.linspace(0, 20, clen)) * float(rng.uniform(0.5, 0.9))
            pos += step_n + int(rng.integers(-2, 3))
        sig = sig * 0.70

    elif preset == "heely":
        sig = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(1.0, 1.5))))
        pos = int(rng.integers(0, step_n // 2))
        while pos < n:
            clen = min(int(rng.uniform(0.02, 0.06) * sr), n - pos)
            if clen > 0:
                rub = pink(200, 3000, clen) * np.exp(-np.linspace(0, 6, clen))
                sig[pos:pos + clen] += rub * float(rng.uniform(0.2, 0.4))
            pos += step_n + int(rng.integers(-3, 4))
        sig = sig * 0.50

    elif preset == "stairs":
        sig = footsteps(float(rng.uniform(0.8, 1.5)), lo=200, hi=4000, amp=0.45)
        creak = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.1, 0.3) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(100, 250))
                tl = np.linspace(0, clen / sr, clen)
                creak[p:p + clen] += np.sin(2 * np.pi * freq * tl) * np.exp(-np.linspace(0, 4, clen)) * 0.10
        sig += creak

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


_PRESET_NAMES = {
    "storm", "blizzard", "rain", "ocean", "fire", "wind", "forest_walk", "crickets",
    "river", "train", "forest", "cafe", "city", "countryside", "station",
    "heels_parquet", "snow_walk", "snow", "room",
    "wind_strong", "ocean_storm", "rain_window", "rainforest", "birds", "birds_morning",
    "birds_lake", "crickets_night", "night", "night_city", "spring", "summer", "autumn",
    "winter", "countryside_morning", "countryside_night", "farm", "cart", "tractor",
    "frogs", "lake", "fountain", "city_heavy", "sirens", "airport", "metro", "bus",
    "cars", "station_train_coming", "harbor", "boat", "bakery", "restaurant", "store",
    "shopping_mall", "checkout", "shopping_bags", "kitchen", "coffee_machine", "tv",
    "radio", "typing", "vacuum", "washing", "bathroom", "water_faucet", "makeup",
    "heartbeat", "clock", "helicopter", "phone", "dogs", "cats", "squirrels", "bees",
    "crowd", "party", "office", "library", "hospital", "school", "factory",
    "construction", "drilling", "explosion", "shooting", "lab", "gym", "pool", "ice",
    "dice", "arcade", "baby", "cooking", "eating", "chips", "drinking", "footsteps",
    "footsteps_wood", "footsteps_tile", "footsteps_outside", "heels", "heely", "stairs",
}


def sound_effect(prompt, duration=6.0, prompt_influence=0.45):
    """Returnează un sunet ambient sintetizat local; nu apelează niciun API extern."""
    text = str(prompt or "").lower().strip()

    # Dacă prompt-ul este deja un nume de preset cunoscut, îl folosim direct.
    if text and text in _PRESET_NAMES:
        return _ambient_wav(text, duration=duration)

    presets = (
        ("storm",         ("tunet", "furtun", "thunder", "storm", "lightning", "fulger", "grindină")),
        ("blizzard",      ("crivăț", "viscol", "blizzard", "howling wind", "strong wind", "vânt puternic")),
        ("rain",          ("ploaie", "rain", "drizzle", "shower", "picături")),
        ("ocean",         ("mare", "val", "ocean", "wave", "beach", "litoral", "coastă")),
        ("fire",          ("foc", "campfire", "fire", "șemineu", "flacăr", "lumânare", "jar")),
        ("wind",          ("vânt", "wind", "breeze", "adiere", "suflare")),
        ("forest_walk",   ("pași pădure", "walking forest", "footsteps leaves", "leaves underfoot",
                           "crunch leaves", "rustling underfoot", "mers pădure", "foșnet pași")),
        ("crickets",      ("greier", "cricket", "noapte liniștit", "quiet night", "seară câmp")),
        ("river",         ("râu", "river", "pârâu", "brook", "stream", "cascadă", "waterfall")),
        ("train",         ("tren", "train", "railroad", "railway", "șine", "vagon")),
        ("forest",        ("pădure", "forest", "frunze", "copac", "woods", "jungle", "livadă")),
        ("cafe",          ("cafenea", "cafe", "coffee shop", "restaurant", "bistro", "bar", "ceainărie")),
        ("city",          ("oraș", "city", "trafic", "traffic", "stradă", "street", "urban", "bulevard")),
        ("countryside",   ("țară", "sat", "countryside", "fermă", "câmp", "rural", "birds chirp", "livadă")),
        ("station",       ("gară", "station", "peron", "aeroport", "airport", "terminal",
                           "announcement", "anunț", "metrou", "autogară")),
        ("heels_parquet", ("tocuri", "heels", "parchet", "parquet", "podea", "floor click",
                           "toc pantof", "pantof cu toc", "lemn podea")),
        ("snow_walk",     ("pași zăpadă", "walking snow", "snow crunch", "footsteps snow",
                           "snow underfoot", "zăpadă pași", "zăpadă trotuар")),
        ("snow",          ("ninso", "zăpad", "snow", "iarnă liniș", "fulgi")),
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)
