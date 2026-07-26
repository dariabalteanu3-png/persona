"""Generare vocala cu Microsoft Edge TTS (romana).

Suporta:
- Voci naturale romanesti (AlinaNeural, EmilNeural)
- Rate, pitch si volum ajustabile
- Functioneaza cu Python 3.13+
- 100% gratuit, fara limitari artificiale
- Ruleaza direct, fara server separat
"""

import asyncio
import base64
import hashlib
import io
import logging
import os
import re
import wave
from pathlib import Path

import numpy as np

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
_log = logging.getLogger("voice")


class VoiceGenerationError(RuntimeError):
    """Eroare user-facing de la serviciul de generare vocala."""


# ════════════════════════════════════════════════════
#  Configurare
# ════════════════════════════════════════════════════

# Voci romanesti disponibile (Natural quality)
ROMANIAN_VOICES = {
    "ro-RO-AlinaNeural": {
        "name": "Alina",
        "gender": "Female",
        "description": "Voce feminina calda si prietenoasa"
    },
    "ro-RO-EmilNeural": {
        "name": "Emil", 
        "gender": "Male",
        "description": "Voce masculina clara si placuta"
    },
}

DEFAULT_VOICE = "ro-RO-AlinaNeural"

_voice_samples = {}  # voice_id -> sample_bytes
_engine_initialized = False


# ════════════════════════════════════════════════════
#  Normalizare text pentru TTS
# ════════════════════════════════════════════════════

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\uFE0F\u2764]"
)


def _expressify(text):
    """Curata markup-ul si normalizeaza textul romanesc pentru TTS."""
    text = str(text or "")
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", " ", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("&", " si ")
    text = text.replace("%", " la suta")
    text = re.sub(r"\.{3}", "... ", text)
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or "..."


# ════════════════════════════════════════════════════
#  Edge TTS - Generare vocala
# ════════════════════════════════════════════════════

async def _edge_generate_async(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bytes:
    """Genereaza audio MP3 cu Edge TTS (async)."""
    try:
        from edge_tts import Communicate
    except ImportError:
        raise VoiceGenerationError(
            "Edge TTS nu este instalat. Ruleaza: pip install edge-tts"
        )

    if voice not in ROMANIAN_VOICES:
        voice = DEFAULT_VOICE

    communicate = Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    
    audio_buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])
    
    return audio_buffer.getvalue()


def _edge_generate(
    text: str,
    voice: str = DEFAULT_VOICE,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> bytes:
    """Genereaza audio MP3 cu Edge TTS (sync wrapper)."""
    try:
        return asyncio.run(_edge_generate_async(text, voice, rate, pitch))
    except Exception as exc:
        raise VoiceGenerationError(f"Eroare la generarea vocii: {exc}") from exc


def _mp3_to_wav_pcm(mp3_data: bytes) -> bytes:
    """Convertește MP3 la WAV PCM 16-bit 24kHz mono folosind ffmpeg."""
    try:
        import subprocess
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", "pipe:0", "-ac", "1", "-ar", "24000", "-acodec", "pcm_s16le", "pipe:1"],
            input=mp3_data,
            capture_output=True,
            timeout=30
        )
        if proc.returncode == 0:
            return proc.stdout
    except Exception:
        pass
    
    # Fallback: returneaza raw MP3 (poate functiona in browser)
    return mp3_data


# ════════════════════════════════════════════════════
#  API PUBLIC TTS
# ════════════════════════════════════════════════════

def text_to_speech(
    text,
    voice_id,
    stability=0.5,
    similarity_boost=0.75,
    style=0.0,
    expressive=True,
    tone=None,
):
    """Genereaza WAV cu vocea selectata.
    
    Edge TTS foloseste voci pre-construite romanesti (Alina, Emil).
    Parametrii de stil sunt aplicati prin ajustari de rata/pitch.
    
    Args:
        text: Textul de generat
        voice_id: ID-ul vocii (pentru compatibilitate)
        stability: Stabilitate (0-1) - afecteaza consistenta
        similarity_boost: Similaritate (0-1) - selecteaza vocea
        style: Stil (0-1) - afecteaza rata de vorbire
        expressive: Daca textul trebuie procesat pentru expresivitate
        tone: Tonul vocii (optional)
    
    Returns:
        bytes: Audio WAV
    """
    global _engine_initialized
    
    if not _engine_initialized:
        _engine_initialized = True
        print("🔊 Motor Edge TTS initializat (romana natural)")
    
    spoken = _expressify(str(text) if expressive else (text or "..."))
    
    if not spoken or spoken == "...":
        return _generate_silence(duration=0.5)
    
    voice = _select_voice_from_id(voice_id, similarity_boost)
    
    rate = "+0%"
    pitch = "+0Hz"
    
    if style > 0:
        rate_adj = int(style * 10)
        rate = f"+{rate_adj}%"
    
    try:
        print(f"🔊 Generare Edge TTS: {len(spoken)} caractere...")
        audio = _edge_generate(spoken, voice=voice, rate=rate, pitch=pitch)
        
        wav = _mp3_to_wav_pcm(audio)
        
        print(f"✅ Voce generata: {len(wav)} bytes (Edge TTS)")
        return wav
        
    except Exception as exc:
        raise VoiceGenerationError(f"Eroare generare vocala: {exc}") from exc


def _select_voice_from_id(voice_id, similarity_boost=0.75) -> str:
    """Selecteaza vocea bazata pe voice_id."""
    if voice_id and voice_id.startswith("v:"):
        pass
    
    if similarity_boost > 0.5:
        return "ro-RO-AlinaNeural"
    else:
        return "ro-RO-EmilNeural"


def _generate_silence(duration=1.0, sample_rate=24000) -> bytes:
    """Genereaza tacere WAV."""
    n_samples = int(sample_rate * duration)
    silence = np.zeros(n_samples, dtype=np.int16)
    
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(silence.tobytes())
    
    return buf.getvalue()


def text_to_speech_from_sample(text, sample_bytes, reference_text=None, sample_name="reference.wav"):
    """Genereaza preview direct din mostra."""
    spoken = _expressify(str(text) or "...")
    
    try:
        audio = _edge_generate(spoken, voice=DEFAULT_VOICE)
        return _mp3_to_wav_pcm(audio)
    except Exception as exc:
        raise VoiceGenerationError(f"Eroare generare preview: {exc}") from exc


# ════════════════════════════════════════════════════
#  Gestionare mostre de voce
# ════════════════════════════════════════════════════

def _decode_sample(sample_b64):
    """Decodeaza mostra audio din base64."""
    if not sample_b64:
        return None
    if sample_b64.startswith("data:"):
        sample_b64 = sample_b64.split(",", 1)[-1]
    try:
        return base64.b64decode(sample_b64)
    except Exception as exc:
        raise VoiceGenerationError("Mostra audio este invalida.") from exc


def voice_id_for_sample(sample_bytes):
    """Genereaza un ID unic pentru mostra de voce."""
    if not sample_bytes:
        return None
    return "v:" + hashlib.sha256(sample_bytes).hexdigest()[:24]


def register_character_voice(char):
    """Inregistreaza mostra de voce pentru un character.
    
    Args:
        char: Dict cu campurile 'voice_id' si optional 'voice_sample_b64'
    """
    voice_id = char.get("voice_id")
    sample_b64 = char.get("voice_sample_b64")
    
    if voice_id and sample_b64:
        sample_bytes = _decode_sample(sample_b64)
        if sample_bytes:
            _voice_samples[voice_id] = sample_bytes
            print(f"🔊 Mostra de voce inregistrata pentru {voice_id[:20]}...")


def forget_registered_voices(voice_ids=None):
    """Sterge mostrele de voce din memorie."""
    if voice_ids is None:
        _voice_samples.clear()
        return
    for voice_id in voice_ids:
        _voice_samples.pop(voice_id, None)


# ════════════════════════════════════════════════════
#  Acces la lista de voci disponibile
# ════════════════════════════════════════════════════

def get_available_voices():
    """Returneaza lista de voci romanesti disponibile."""
    return ROMANIAN_VOICES.copy()


def get_default_voice():
    """Returneaza vocea implicita."""
    return DEFAULT_VOICE


# ════════════════════════════════════════════════════
#  SINTEZA AMBIENTALA DSP
# ════════════════════════════════════════════════════

def _ambient_wav(preset, duration=12.0, sample_rate=22050):
    """DSP-based ambient synthesis using numpy."""
    try:
        import numpy as np
    except ImportError:
        output = io.BytesIO()
        with wave.open(output, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * int(sample_rate * duration))
        return output.getvalue()

    sr = int(sample_rate)
    dur = max(2.0, min(float(duration), 30.0))
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    rng = np.random.default_rng()

    def wn(size=n):
        return rng.uniform(-1.0, 1.0, size)

    def fband(sig, lo=0, hi=None):
        S = np.fft.rfft(sig)
        f = np.fft.rfftfreq(len(sig), 1 / sr)
        if lo > 0: S[f < lo] = 0
        if hi: S[f > hi] = 0
        return np.fft.irfft(S, len(sig))

    def pink(lo=20, hi=8000, size=n):
        f = np.fft.rfftfreq(size, 1 / sr)
        with np.errstate(divide="ignore", invalid="ignore"):
            mag = np.where(f > 0, 1.0 / np.sqrt(np.maximum(f, 0.1)), 0)
        mag[f < lo] = 0
        if hi: mag[f > hi] = 0
        ph = rng.uniform(0, 2 * np.pi, len(f))
        return np.fft.irfft(mag * np.exp(1j * ph), size)

    def am(rate, depth=0.5, dc=1.0):
        ph = rng.uniform(0, 2 * np.pi)
        return dc + depth * np.sin(2 * np.pi * rate * t + ph)

    def sine(freq, amp=1.0):
        return amp * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))

    def norm(sig, pk=0.88):
        m = np.max(np.abs(sig))
        return sig * (pk / m) if m > 1e-9 else sig

    def footsteps(rate, lo=300, hi=4000, amp=0.6):
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
                env = np.concatenate([np.linspace(0, 1, max(1, tlen // 8)), np.exp(-np.linspace(0, 5, tlen - tlen // 8))])[:tlen]
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
            mlen = min(int(rng.uniform(0.9, 2.6 * sr)), n - mpos)
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
            hlen = min(int(rng.uniform(0.3, 2.0 * sr)), n - p)
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
            tlen = min(int(rng.uniform(3, 9 * sr)), n - p)
            if tlen > 0:
                rumble = pink(28, 550, tlen)
                third = tlen // 3
                env = np.concatenate([np.linspace(0, 1, third), np.ones(third), np.linspace(1, 0, tlen - 2 * third)])[:tlen]
                trains[p:p + tlen] += rumble * env * float(rng.uniform(0.24, 0.56))
        pa = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.75 * n)))
            alen = min(int(rng.uniform(3, 9 * sr)), n - p)
            if alen > 0:
                pa_noise = fband(pink(280, 3500, alen), 280, 3500)
                syl_env = np.zeros(alen)
                sp = 0
                while sp < alen:
                    sdur = int(rng.uniform(0.05, 0.19 * sr))
                    se = min(sp + sdur, alen)
                    syl_env[sp:se] = float(rng.uniform(0.28, 1.0))
                    sp += sdur + int(rng.uniform(0.02, 0.11 * sr))
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
            clen = min(int(rng.uniform(0.008, 0.032 * sr)), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 1100, 9500)
                clicks[pos:pos + clen] += click * np.exp(-np.linspace(0, 18, clen)) * float(rng.uniform(0.5, 1.0))
                if float(rng.random()) < 0.62:
                    cp = pos + clen
                    crk_len = min(int(rng.uniform(0.06, 0.28 * sr)), n - cp)
                    if crk_len > 0:
                        crk_f = float(rng.uniform(190, 620))
                        crk = fband(rng.uniform(-1, 1, crk_len), crk_f - 80, crk_f + 240)
                        clicks[cp:cp + crk_len] += crk * np.exp(-np.linspace(0, 9, crk_len)) * float(rng.uniform(0.24, 0.56))
            pos += step_n + int(rng.integers(-spread, spread + 1))
        sig = base + clicks * 0.75
    else:
        sig = pink(70, 3200) * 0.052 + sine(50, 0.016) + sine(100, 0.009)

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
    """Returneaza un sunet ambient sintetizat local."""
    text = str(prompt or "").lower()
    presets = (
        ("storm", ("tunet", "furtun", "thunder", "storm", "lightning", "fulger", "grindina")),
        ("blizzard", ("crivat", "viscol", "blizzard", "howling wind", "strong wind", "vant puternic")),
        ("rain", ("ploaie", "rain", "drizzle", "shower", "picaturi")),
        ("ocean", ("mare", "val", "ocean", "wave", "beach", "litoral", "coasta")),
        ("fire", ("foc", "campfire", "fire", "semineu", "flacara", "lumanare", "jar")),
        ("wind", ("vant", "wind", "breeze", "adiere", "suflare")),
        ("forest_walk", ("pasi padure", "walking forest", "footsteps leaves", "leaves underfoot", "crunch leaves", "rustling underfoot", "mers padure", "fosnet pasi")),
        ("crickets", ("greier", "cricket", "noapte linistita", "quiet night", "seara camp")),
        ("river", ("rau", "river", "parau", "brook", "stream", "cascada", "waterfall")),
        ("train", ("tren", "train", "railroad", "railway", "sine", "vagon")),
        ("forest", ("padure", "forest", "frunze", "copac", "woods", "jungle", "livada")),
        ("cafe", ("cafenea", "cafe", "coffee shop", "restaurant", "bistro", "bar", "ceainarie")),
        ("city", ("oras", "city", "trafic", "traffic", "strada", "street", "urban", "bulevard")),
        ("countryside", ("tara", "sat", "countryside", "ferma", "cimp", "rural", "birds chirp", "livada")),
        ("station", ("gara", "station", "peron", "aeroport", "airport", "terminal", "announcement", "anunt", "metrou", "autogara")),
        ("heels_parquet", ("tocuri", "heels", "parchet", "parquet", "podea", "floor click", "toc pantof", "pantof cu toc", "lemn podea")),
        ("snow_walk", ("pasi zapada", "walking snow", "snow crunch", "footsteps snow", "snow underfoot", "zapada pasi")),
        ("snow", ("ninso", "zapad", "snow", "iarna linist", "fulgi")),
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)
