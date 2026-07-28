"""
Generare vocală cu F5-TTS (voice cloning) și gTTS (fallback).

Motor principal: F5-TTS (cdorob/f5-tts-romanian fine-tune sau F5TTS_v1_Base).
Clonează vocea dintr-o mostră audio (10-30 secunde recomandat).
Fallback: gTTS (Google TTS, necesită internet).
"""

import base64
import hashlib
import io
import logging
import os
import re
import time
import wave
from pathlib import Path

import numpy as np

from dotenv import load_dotenv

# ──────────────────────────────────────────────
#  F5-TTS — voice cloning locală (PyTorch)
# ──────────────────────────────────────────────

_F5_MODEL_DIR = os.environ.get("F5_MODEL_DIR", "/tmp/f5_tts_models")
# Romanian fine-tune on HuggingFace (cdorob/f5-tts-romanian)
_F5_RO_CKPT_URL = (
    "https://huggingface.co/cdorob/f5-tts-romanian/resolve/main/model_last.pt"
)
_F5_RO_VOCAB_URL = (
    "https://huggingface.co/cdorob/f5-tts-romanian/resolve/main/vocab.txt"
)
_F5_RO_CKPT = os.path.join(_F5_MODEL_DIR, "romanian_model_last.pt")
_F5_RO_VOCAB = os.path.join(_F5_MODEL_DIR, "romanian_vocab.txt")

_f5_engine = None  # cached F5TTS instance
_f5_loading = False
_f5_load_time = 0.0
_F5_CACHE_TTL = 3600  # seconds before re-checking model availability


def _ensure_f5_model():
    """Încarcă modelul F5-TTS (base sau Romanian fine-tune) la prima cerere."""
    global _f5_engine, _f5_loading, _f5_load_time

    if _f5_engine is not None:
        return True

    if _f5_loading:
        print("⏳ Modelul F5-TTS se încarcă deja...")
        return False

    _f5_loading = True
    try:
        # Check if Romanian fine-tune files exist, download if needed
        ckpt_path = _F5_RO_CKPT
        vocab_path = _F5_RO_VOCAB

        os.makedirs(_F5_MODEL_DIR, exist_ok=True)

        need_download = not os.path.exists(ckpt_path) or not os.path.exists(vocab_path)

        if need_download:
            print("📥 Descarc model F5-TTS românesc (~1.0 GB)...")
            print("   (Poate dura câteva minute la prima rulare)")
            _download_with_progress(_F5_RO_CKPT_URL, ckpt_path)
            _download_with_progress(_F5_RO_VOCAB_URL, vocab_path)

        if not os.path.exists(ckpt_path) or not os.path.exists(vocab_path):
            print("⚠️ Modelul românesc nu a putut fi descărcat complet.")
            return False

        # Import F5-TTS
        print("🔊 Încarc motorul F5-TTS românesc (poate dura 30-60s)...")
        from f5_tts.api import F5TTS

        _f5_engine = F5TTS(
            model="F5TTS_v1_Base",
            ckpt_file=ckpt_path,
            vocab_file=vocab_path,
            device="cpu",
        )
        _f5_load_time = time.time()
        print("✅ Motor F5-TTS românesc încărcat (clonare vocală)")
        return True

    except ImportError:
        print("⚠️ pachetul f5-tts nu e instalat. Instalează cu: pip install f5-tts")
        return False
    except Exception as e:
        print(f"⚠️ Nu am putut încărca F5-TTS: {e}")
        return False
    finally:
        _f5_loading = False


def _download_with_progress(url, dest_path):
    """Descarcă un fișier cu indicator de progres."""
    import urllib.request

    def report(block_count, block_size, total_size):
        downloaded = block_count * block_size
        if total_size > 0:
            pct = min(100, int(downloaded * 100 / total_size))
            if block_count % 20 == 0:  # report every ~20 blocks
                mb = downloaded / (1024 * 1024)
                total_mb = total_size / (1024 * 1024)
                print(f"   {pct}% ({mb:.0f}/{total_mb:.0f} MB)")

    try:
        urllib.request.urlretrieve(url, dest_path, reporthook=report)
        print(f"   ✅ Descărcat: {os.path.basename(dest_path)}")
    except Exception as e:
        print(f"   ⚠️ Eroare descărcare {os.path.basename(dest_path)}: {e}")
        raise


def _f5_generate(text, sample_bytes, sample_name="reference.wav", ref_text=None):
    """Generează audio cu F5-TTS (voice cloning din mostră).

    Args:
        text: Textul de generat
        sample_bytes: Mostra audio (bytes WAV)
        sample_name: Numele fișierului mostrei
        ref_text: Transcrierea mostrei (opțional, dar recomandat)

    Returns:
        bytes: Audio WAV generat
    """
    import soundfile as sf

    if not _ensure_f5_model() or _f5_engine is None:
        raise RuntimeError("F5-TTS nu este disponibil.")

    # Scrie mostra temporar
    ref_path = os.path.join(_F5_MODEL_DIR, sample_name)
    with open(ref_path, "wb") as f:
        f.write(sample_bytes)

    # Dacă nu avem text de referință, încercăm să generăm unul
    # (folosind transcript aproximativ)
    ref_text = ref_text or ""

    print(f"🔊 F5-TTS: clonare voce din {sample_name} ({len(sample_bytes)} bytes)")
    t0 = time.time()

    audio, sample_rate = _f5_engine.infer(
        ref_file=ref_path,
        ref_text=ref_text if ref_text else " ",
        gen_text=text,
        nfe_step=24,       # mai puțini pași = mai rapid pe CPU
        cfg_strength=2.0,
        seed=None,
    )

    elapsed = time.time() - t0
    print(f"✅ F5-TTS generat în {elapsed:.1f}s ({len(audio)} samples @ {sample_rate}Hz)")

    # Convertește np array la WAV bytes
    buf = io.BytesIO()
    sf.write(buf, audio, sample_rate, format="WAV")
    return buf.getvalue()


def _f5_generate_no_clone(text):
    """Generează audio cu F5-TTS fără clonare (folosește o voce implicită).

    Pentru F5-TTS, fără o mostră de voce, nu putem face TTS pur.
    Folosim gTTS în acest caz.
    """
    # F5-TTS necesită întotdeauna o referință audio pentru a genera.
    # Fără mostră, trimitem la gTTS.
    raise VoiceGenerationError("F5-TTS necesită o mostră audio pentru clonare.")


# ──────────────────────────────────────────────
#  gTTS fallback
# ──────────────────────────────────────────────

try:
    from gtts import gTTS as _gTTS

    _GTTS_AVAILABLE = True
except ImportError:
    _GTTS_AVAILABLE = False
    print("⚠️ gTTS nu e instalat.")

load_dotenv(Path(__file__).parent / ".env")
_log = logging.getLogger("voice")


class VoiceGenerationError(RuntimeError):
    """Eroare user-facing de la serviciul de generare vocală."""


# ════════════════════════════════════════════════════
#  Normalizare text pentru TTS
# ════════════════════════════════════════════════════

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\uFE0F\u2764]"
)

# Normalizare cedilla pentru română
_CEDILLA_TO_COMMA = str.maketrans({
    "\u015f": "\u0219",  # ş -> ș
    "\u0163": "\u021b",  # ţ -> ț
    "\u015e": "\u0218",  # Ş -> Ș
    "\u0162": "\u021a",  # Ţ -> Ț
})


def _normalize_romanian(text: str) -> str:
    """Normalizează caracterele cedilla pentru TTS."""
    return str(text).translate(_CEDILLA_TO_COMMA)


def _expressify(text):
    """Curăță markup-ul și normalizează textul românesc pentru TTS."""
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
#  API PUBLIC TTS
# ════════════════════════════════════════════════════

def text_to_speech(
    text,
    voice_id=None,
    stability=0.5,
    similarity_boost=0.75,
    style=0.0,
    expressive=True,
    tone=None,
):
    """Generează WAV cu F5-TTS (voice cloning) sau gTTS (fallback).

    Ordinea preferată:
    1. F5-TTS cu clonare vocală (dacă există o mostră înregistrată pentru voice_id)
    2. gTTS (fallback, necesită internet)

    Args:
        text: Textul de generat
        voice_id: ID-ul vocii (pentru clonare din mostră)
        stability: (menținut pentru compatibilitate API)
        similarity_boost: (menținut pentru compatibilitate API)
        style: (menținut pentru compatibilitate API)
        expressive: Dacă textul trebuie procesat
        tone: (menținut pentru compatibilitate API)

    Returns:
        bytes: Audio WAV
    """
    spoken = _expressify(str(text) if expressive else (text or "..."))

    if not spoken or spoken == "...":
        return _generate_silence(duration=0.5)

    # 1. F5-TTS cu clonare (dacă avem mostră pentru voice_id)
    if voice_id and voice_id in _voice_samples:
        sample_bytes = _voice_samples[voice_id]
        try:
            print(f"🔊 F5-TTS clonare: {len(spoken)} caractere, voce {voice_id[:20]}...")
            wav = _f5_generate(spoken, sample_bytes, sample_name=f"voice_{voice_id}.wav")
            print(f"✅ F5-TTS generat, {len(wav)} bytes")
            return wav
        except Exception as exc:
            print(f"⚠️ F5-TTS a eșuat: {exc}")

    # 2. F5-TTS fără clonare (folosim un fallback intern)
    # F5-TTS necesită mostră, deci trecem direct la gTTS
    # 3. gTTS fallback
    if _GTTS_AVAILABLE:
        try:
            print(f"🔊 gTTS: {len(spoken)} caractere")
            return _gtts_generate(spoken)
        except Exception as gtts_err:
            print(f"⚠️ gTTS a eșuat: {gtts_err}")

    # Niciun TTS nu a funcționat
    raise VoiceGenerationError(
        "Nici F5-TTS, nici gTTS nu funcționează. "
        "Verifică conexiunea la internet sau instalează f5-tts și gTTS."
    )


def _gtts_generate(text, lang="ro") -> bytes:
    """Generare vocală cu Google TTS (fallback)."""
    from pydub import AudioSegment

    tts = _gTTS(text=text, lang=lang, slow=False)
    mp3_buf = io.BytesIO()
    tts.write_to_fp(mp3_buf)
    mp3_buf.seek(0)

    audio = AudioSegment.from_mp3(mp3_buf)
    wav_buf = io.BytesIO()
    audio.export(wav_buf, format="wav")
    wav_buf.seek(0)

    return wav_buf.read()


def _generate_silence(duration=1.0, sample_rate=22050) -> bytes:
    """Generează tăcere WAV."""
    n_samples = int(sample_rate * duration)
    silence = np.zeros(n_samples, dtype=np.int16)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(silence.tobytes())

    return buf.getvalue()


def text_to_speech_from_sample(
    text, sample_bytes, reference_text=None, sample_name="reference.wav"
):
    """Generează audio cu F5-TTS (voice cloning din mostră).

    Args:
        text: Textul de generat
        sample_bytes: Mostra audio (bytes WAV)
        reference_text: Transcrierea mostrei (opțional)
        sample_name: Numele fișierului mostrei

    Returns:
        bytes: Audio WAV generat
    """
    spoken = _expressify(str(text) or "...")

    # 1. F5-TTS cu clonare
    try:
        if _ensure_f5_model():
            return _f5_generate(spoken, sample_bytes, sample_name, reference_text)
    except Exception as exc:
        print(f"⚠️ F5-TTS a eșuat: {exc}")

    # 2. gTTS fallback
    if _GTTS_AVAILABLE:
        try:
            return _gtts_generate(spoken)
        except Exception as gtts_err:
            print(f"⚠️ gTTS fallback a eșuat: {gtts_err}")

    raise VoiceGenerationError("Eroare generare audio: niciun TTS disponibil.")


# ════════════════════════════════════════════════════
#  Gestionare mostre de voce
# ════════════════════════════════════════════════════

_voice_samples = {}  # voice_id -> sample_bytes


def _decode_sample(sample_b64):
    """Decodează mostra audio din base64."""
    if not sample_b64:
        return None
    if sample_b64.startswith("data:"):
        sample_b64 = sample_b64.split(",", 1)[-1]
    try:
        return base64.b64decode(sample_b64)
    except Exception as exc:
        raise VoiceGenerationError("Mostra audio este invalidă.") from exc


def voice_id_for_sample(sample_bytes):
    """Generează un ID unic pentru mostra de voce."""
    if not sample_bytes:
        return None
    return "v:" + hashlib.sha256(sample_bytes).hexdigest()[:24]


def register_character_voice(char):
    """Înregistrează mostra de voce pentru un personaj (F5-TTS).

    F5-TTS poate clona vocea din această mostră la generare.
    """
    voice_id = char.get("voice_id")
    sample_b64 = char.get("voice_sample_b64")

    if voice_id and sample_b64:
        sample_bytes = _decode_sample(sample_b64)
        if sample_bytes:
            _voice_samples[voice_id] = sample_bytes
            print(f"🔊 Mostră de voce înregistrată pentru {voice_id[:20]}...")


def forget_registered_voices(voice_ids=None):
    """Șterge mostrele de voce din memorie."""
    if voice_ids is None:
        _voice_samples.clear()
        return
    for voice_id in voice_ids:
        _voice_samples.pop(voice_id, None)


# ════════════════════════════════════════════════════
#  Acces la lista de voci disponibile
# ════════════════════════════════════════════════════

def get_available_voices():
    """Returnează informații despre vocile disponibile."""
    voices = {
        "f5-romanian": {
            "name": "F5-TTS Românesc — clonare vocală",
            "description": "Clonare vocală din mostră audio (10-30s) cu model F5-TTS fine-tuned pentru română",
            "features": ["voice-cloning", "romanian", "free", "local", "open-source"],
        },
    }
    if _GTTS_AVAILABLE:
        voices["gtts"] = {
            "name": "gTTS — Google TTS (fallback)",
            "description": "Sinteză vocală online Google, nu face clonare",
            "features": ["romanian", "free", "online"],
        }
    return voices


def get_default_voice():
    """Returnează tipul de voce implicit."""
    return "f5-romanian"



# ════════════════════════════════════════════════════
#  SINTEZĂ AMBIENTALĂ DSP
# ════════════════════════════════════════════════════

def _ambient_wav(preset, duration=12.0, sample_rate=22050):
    """DSP-based ambient synthesis using numpy."""
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
        if lo > 0:
            S[f < lo] = 0
        if hi:
            S[f > hi] = 0
        return np.fft.irfft(S, len(sig))

    def pink(lo=20, hi=8000, size=n):
        f = np.fft.rfftfreq(size, 1 / sr)
        with np.errstate(divide="ignore", invalid="ignore"):
            mag = np.where(f > 0, 1.0 / np.sqrt(np.maximum(f, 0.1)), 0)
        mag[f < lo] = 0
        if hi:
            mag[f > hi] = 0
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

    # --- NOI preseturi DSP ---

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
                env = np.concatenate(
                    [np.linspace(0, 1, max(1, tlen // 8)),
                     np.exp(-np.linspace(0, 5, tlen - tlen // 8))]
                )[:tlen]
                thunder[pos:pos + tlen] += boom * env * float(rng.uniform(0.55, 1.0))
        sig = rain + rumble + thunder * 0.90
    elif preset == "ocean":
        base = pink(55, 4500)
        w1 = (
            np.abs(np.sin(2 * np.pi * float(rng.uniform(0.05, 0.10)) * t
                          + float(rng.uniform(0, np.pi))))
            ** 0.55
        )
        w2 = (
            np.abs(np.sin(2 * np.pi * float(rng.uniform(0.07, 0.14)) * t
                          + float(rng.uniform(0, np.pi))))
            ** 0.55
        )
        sig = base * (0.5 * w1 + 0.4 * w2) * 0.88
    elif preset == "fire":
        base = pink(55, 2800) * am(rng.uniform(1.5, 3.5), 0.3, 0.7) * 0.30
        crackle = footsteps(float(rng.uniform(14, 26)), lo=500, hi=5500, amp=0.45)
        pops = np.zeros(n)
        for _ in range(int(rng.integers(3, 10))):
            p = int(rng.integers(0, n))
            plen = min(int(0.055 * sr), n - p)
            if plen > 0:
                pops[p:p + plen] = (
                    rng.uniform(-1, 1, plen) * np.exp(-np.linspace(0, 8, plen))
                )
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
                twigs[p:p + tlen] += (
                    snap * np.exp(-np.linspace(0, 12, tlen))
                    * float(rng.uniform(0.4, 0.85))
                )
        sig = leaves + birds(nb=8) * 0.38 + steps * 0.50 + twigs
    elif preset == "cafe":
        murmur = (
            fband(pink(140, 3200), 170, 2600)
            * am(rng.uniform(0.04, 0.12), 0.15, 0.85)
            * 0.37
        )
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
        traffic = (
            fband(pink(45, 1600), 55, 1300)
            * am(rng.uniform(0.04, 0.12), 0.20, 0.80)
            * 0.45
        )
        hum = fband(pink(48, 130), 52, 120) * 0.17
        horns = np.zeros(n)
        for _ in range(int(rng.integers(1, 5))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(0.3, 2.0 * sr)), n - p)
            if hlen > 0:
                freq = float(rng.uniform(300, 750))
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.28
                tl = np.linspace(0, hlen / sr, hlen)
                horns[p:p + hlen] += (
                    np.sin(2 * np.pi * freq * tl) * env
                    * float(rng.uniform(0.22, 0.55))
                )
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
        steps = fband(
            footsteps(float(rng.uniform(0.7, 1.1)), lo=100, hi=2800, amp=0.52),
            80, 3200
        )
        sig = base + steps * 0.65
    elif preset == "station":
        crowd = (
            fband(pink(180, 3200), 190, 2600)
            * am(rng.uniform(0.04, 0.10), 0.20, 0.80)
            * 0.31
        )
        trains = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, int(0.7 * n)))
            tlen = min(int(rng.uniform(3, 9 * sr)), n - p)
            if tlen > 0:
                rumble = pink(28, 550, tlen)
                third = tlen // 3
                env = np.concatenate([
                    np.linspace(0, 1, third), np.ones(third),
                    np.linspace(1, 0, tlen - 2 * third)
                ])[:tlen]
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
                clicks[pos:pos + clen] += (
                    click * np.exp(-np.linspace(0, 18, clen))
                    * float(rng.uniform(0.5, 1.0))
                )
                if float(rng.random()) < 0.62:
                    cp = pos + clen
                    crk_len = min(int(rng.uniform(0.06, 0.28 * sr)), n - cp)
                    if crk_len > 0:
                        crk_f = float(rng.uniform(190, 620))
                        crk = fband(
                            rng.uniform(-1, 1, crk_len), crk_f - 80, crk_f + 240
                        )
                        clicks[cp:cp + crk_len] += (
                            crk * np.exp(-np.linspace(0, 9, crk_len))
                            * float(rng.uniform(0.24, 0.56))
                        )
            pos += step_n + int(rng.integers(-spread, spread + 1))
        sig = base + clicks * 0.75

    # ═══════════════════════════════════════════════
    #  NOI preseturi (extinderea bibliotecii)
    # ═══════════════════════════════════════════════

    elif preset == "arcade":
        """Retro game arcade cu beep-uri și blips."""
        sig = np.zeros(n)
        for _ in range(int(rng.uniform(5, 15))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.02, 0.12) * sr), n - p)
            if blen > 0:
                freq = float(rng.uniform(200, 5000))
                tl = np.linspace(0, blen / sr, blen)
                beep = np.sin(2 * np.pi * freq * tl)
                env = np.exp(-np.linspace(0, 9, blen))
                sig[p:p + blen] += beep * env * float(rng.uniform(0.15, 0.45))
        # fundal zgomot ușor
        sig += pink(500, 7500) * 0.055

    elif preset == "factory":
        """Zgomot industrial / fabrică."""
        rumble = pink(22, 180) * am(rng.uniform(0.3, 0.7), 0.20, 0.80) * 0.55
        hiss = pink(1500, 8500) * am(rng.uniform(0.8, 1.5), 0.30, 0.70) * 0.18
        clanks = np.zeros(n)
        for _ in range(int(rng.integers(3, 10))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.02, 0.15) * sr), n - p)
            if clen > 0:
                cf = float(rng.uniform(400, 2500))
                bang = fband(rng.uniform(-1, 1, clen), cf - 300, cf + 500)
                env = np.exp(-np.linspace(0, 14, clen))
                clanks[p:p + clen] += bang * env * float(rng.uniform(0.3, 0.8))
        sig = rumble + hiss + clanks * 0.40

    elif preset == "construction":
        """Șantier / construcții."""
        rumble = pink(25, 220) * am(rng.uniform(0.15, 0.30), 0.25, 0.75) * 0.40
        impacts = np.zeros(n)
        for _ in range(int(rng.uniform(4, 14))):
            p = int(rng.integers(0, n))
            ilen = min(int(rng.uniform(0.08, 0.35) * sr), n - p)
            if ilen > 0:
                burst = fband(rng.uniform(-1, 1, ilen), 80, 3000)
                env = np.exp(-np.linspace(0, 8, ilen))
                impacts[p:p + ilen] += burst * env * float(rng.uniform(0.25, 0.65))
        sig = rumble + impacts * 0.50 + pink(600, 5000) * 0.065

    elif preset == "bees":
        """Albine / insecte zumzăind."""
        sig = np.zeros(n)
        for _ in range(6):
            freq = float(rng.uniform(140, 380))
            rate = float(rng.uniform(140, 250))  # wing beat variation
            ph = float(rng.uniform(0, 2 * np.pi))
            buzz = np.sin(2 * np.pi * freq * t + ph)
            buzz *= 0.5 + 0.5 * np.sin(2 * np.pi * rate * t)
            env = 0.3 + 0.7 * am(float(rng.uniform(0.2, 0.8)), 0.30, 0.70)
            sig += buzz * env * 0.12
        sig += pink(1000, 5000) * 0.03

    elif preset == "boat":
        """Motor de barcă / vapor."""
        rumble = pink(20, 120) * am(rng.uniform(0.3, 0.6), 0.20, 0.80) * 0.50
        water = pink(80, 3500) * am(rng.uniform(0.05, 0.12), 0.35, 0.65) * 0.20
        waves = (
            np.abs(np.sin(2 * np.pi * float(rng.uniform(0.08, 0.15)) * t))
            ** 0.60
        ) * 0.22
        sig = rumble + water + waves

    elif preset == "pool":
        """Piscină cu înot și stropi."""
        water = pink(200, 4500) * am(rng.uniform(0.3, 0.8), 0.40, 0.60) * 0.15
        splashes = np.zeros(n)
        for _ in range(int(rng.uniform(5, 15))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.1, 0.45) * sr), n - p)
            if slen > 0:
                splash = fband(rng.uniform(-1, 1, slen), 400, 6000)
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.3
                splashes[p:p + slen] += splash * env * float(rng.uniform(0.25, 0.55))
        sig = water + splashes * 0.45

    elif preset == "ice":
        """Patinaj pe gheață."""
        base = pink(300, 3500) * 0.06
        scrapes = np.zeros(n)
        for _ in range(int(rng.uniform(3, 10))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.3, 2.5) * sr), n - p)
            if slen > 0:
                freq = float(rng.uniform(800, 4000))
                tl = np.linspace(0, slen / sr, slen)
                scrape = fband(rng.uniform(-1, 1, slen), freq - 200, freq + 400)
                env = np.sin(np.pi * np.linspace(0, 1, slen)) ** 0.5
                scrapes[p:p + slen] += scrape * env * float(rng.uniform(0.12, 0.30))
        sig = base + scrapes * 0.40 + birds(nb=3, lo_f=2000, hi_f=4000) * 0.15

    elif preset == "gym":
        """Sală de sport / fitness."""
        base = pink(60, 3000) * am(rng.uniform(0.2, 0.5), 0.20, 0.80) * 0.25
        thuds = np.zeros(n)
        for _ in range(int(rng.uniform(3, 12))):
            p = int(rng.integers(0, n))
            tlen = min(int(rng.uniform(0.05, 0.20) * sr), n - p)
            if tlen > 0:
                thud = fband(rng.uniform(-1, 1, tlen), 40, 300)
                env = np.exp(-np.linspace(0, 10, tlen))
                thuds[p:p + tlen] += thud * env * float(rng.uniform(0.30, 0.70))
        sig = base + thuds * 0.50
        # muzică de fundal distantă
        sig += fband(pink(600, 3000), 600, 3000) * 0.04

    elif preset == "harbor":
        """Port / chei."""
        water = pink(40, 3200) * am(rng.uniform(0.05, 0.12), 0.20, 0.80) * 0.20
        wind_amb = pink(200, 2000) * am(rng.uniform(0.04, 0.08), 0.15, 0.85) * 0.08
        ropes = np.zeros(n)
        for _ in range(int(rng.uniform(2, 6))):
            p = int(rng.integers(0, n))
            rlen = min(int(rng.uniform(0.2, 1.0) * sr), n - p)
            if rlen > 0:
                creak = fband(sine(float(rng.uniform(60, 200)), 1.0)[:rlen], 50, 500)
                env = np.sin(np.pi * np.linspace(0, 1, rlen)) ** 0.4
                ropes[p:p + rlen] += creak * env * float(rng.uniform(0.10, 0.25))
        horns = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.3 * n), n))
            hlen = min(int(rng.uniform(0.5, 1.8) * sr), n - p)
            if hlen > 0:
                f_low = float(rng.uniform(55, 120))
                tl = np.linspace(0, hlen / sr, hlen)
                horns[p:p + hlen] += (
                    np.sin(2 * np.pi * f_low * tl)
                    * np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.3
                    * float(rng.uniform(0.28, 0.50))
                )
        sig = water + wind_amb + ropes * 0.35 + horns * 0.40
        # păsări marine
        sig += birds(nb=6, lo_f=1200, hi_f=3500) * 0.25

    elif preset == "shooting":
        """Tir / focuri de armă."""
        sig = pink(30, 3000) * 0.04
        shots = np.zeros(n)
        for _ in range(int(rng.uniform(2, 8))):
            p = int(rng.integers(0, n))
            slen = min(int(rng.uniform(0.005, 0.12) * sr), n - p)
            if slen > 0:
                bang = rng.uniform(-1, 1, slen)
                env = np.exp(-np.linspace(0, 25, slen))
                shots[p:p + slen] += bang * env * float(rng.uniform(0.5, 1.0))
            # ecou
            ep = p + int(sr * float(rng.uniform(0.08, 0.25)))
            elen = min(int(0.06 * sr), n - ep)
            if elen > 0 and ep < n:
                echo = fband(rng.uniform(-1, 1, elen), 200, 3000)
                shots[ep:ep + elen] += echo * np.exp(-np.linspace(0, 8, elen)) * 0.25
        sig += shots * 0.60

    elif preset == "lab":
        """Laborator științific."""
        hum = pink(45, 180) * am(rng.uniform(0.2, 0.4), 0.10, 0.90) * 0.10
        beeps = np.zeros(n)
        for _ in range(int(rng.uniform(4, 12))):
            p = int(rng.integers(0, n))
            blen = min(int(rng.uniform(0.01, 0.08) * sr), n - p)
            if blen > 0:
                freq = float(rng.uniform(1000, 4000))
                tl = np.linspace(0, blen / sr, blen)
                beeps[p:p + blen] += (
                    np.sin(2 * np.pi * freq * tl)
                    * np.exp(-np.linspace(0, 15, blen))
                    * float(rng.uniform(0.2, 0.5))
                )
        sig = hum + beeps * 0.40 + pink(1200, 6000) * 0.025

    elif preset == "explosion":
        """Explozii / detunături."""
        sig = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, int(0.3 * n)))
            elen = min(int(rng.uniform(0.3, 1.5) * sr), n - p)
            if elen > 0:
                boom = rng.uniform(-1, 1, elen)
                env = np.concatenate([
                    np.linspace(0, 1, max(1, elen // 5)),
                    np.exp(-np.linspace(0, 6, elen - elen // 5))
                ])[:elen]
                boom = fband(boom, 15, 500)
                sig[p:p + elen] += boom * env * float(rng.uniform(0.6, 1.0))
        sig = norm(sig) * 0.95
        sig += pink(30, 3000) * 0.03

    elif preset == "baby":
        """Bebeluș / plânset."""
        sig = np.zeros(n)
        for _ in range(int(rng.uniform(2, 6))):
            p = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.5, 2.5) * sr), n - p)
            if clen > 0:
                freq = float(rng.uniform(350, 700))
                tl = np.linspace(0, clen / sr, clen)
                cry = np.sin(2 * np.pi * freq * tl)
                cry += 0.3 * np.sin(2 * np.pi * freq * 1.5 * tl)
                env = np.concatenate([
                    np.linspace(0, 1, max(1, clen // 6)),
                    np.sin(np.pi * np.linspace(0, 1, clen - clen // 6)) ** 0.3
                ])[:clen]
                cry_mod = cry * (0.7 + 0.3 * np.sin(2 * np.pi * 3.5 * tl))
                sig[p:p + clen] += cry_mod * env * float(rng.uniform(0.15, 0.35))
        sig += fband(pink(200, 3000), 200, 3000) * 0.02

    elif preset == "drilling":
        """Foraj / freză dentară."""
        sig = np.zeros(n)
        for _ in range(int(rng.uniform(2, 6))):
            p = int(rng.integers(0, n))
            dlen = min(int(rng.uniform(0.5, 3.0) * sr), n - p)
            if dlen > 0:
                freq = float(rng.uniform(4000, 9000))
                tl = np.linspace(0, dlen / sr, dlen)
                drill = np.sin(2 * np.pi * freq * tl)
                drill += 0.4 * np.sin(2 * np.pi * freq * 2.5 * tl)
                env = np.sin(np.pi * np.linspace(0, 1, dlen)) ** 0.2
                am_mod = 0.6 + 0.4 * np.sin(2 * np.pi * float(rng.uniform(8, 15)) * tl)
                sig[p:p + dlen] += drill * env * am_mod * float(rng.uniform(0.10, 0.25))
        sig += pink(2000, 9000) * 0.04

    elif preset == "phone":
        """Telefon / notificări."""
        sig = np.zeros(n)
        # vibrație
        vib = pink(60, 400) * 0.04
        # sonerie / notificări
        for _ in range(int(rng.uniform(1, 5))):
            p = int(rng.integers(0, n))
            rlen = min(int(rng.uniform(0.1, 1.0) * sr), n - p)
            if rlen > 0:
                f1, f2 = float(rng.uniform(600, 1200)), float(rng.uniform(1400, 2400))
                tl = np.linspace(0, rlen / sr, rlen)
                ring = np.sin(2 * np.pi * f1 * tl)
                ring += 0.6 * np.sin(2 * np.pi * f2 * tl)
                env = np.sin(np.pi * np.linspace(0, 1, rlen)) ** 0.5
                # pauză între tonuri
                for si in range(int(rlen // (sr // 4))):
                    sp = si * sr // 4
                    if sp + sr // 8 < rlen:
                        ring[sp:sp + sr // 8] *= 0.0
                sig[p:p + rlen] += ring * env * float(rng.uniform(0.15, 0.35))
        sig += vib

    else:
        # Default: fundal liniștit
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
    """Returnează un sunet ambiental sintetizat local."""
    text = str(prompt or "").lower()
    presets = (
        # Natură și vreme
        ("storm", ("tunet", "furtun", "thunder", "storm", "lightning", "fulger",
                    "grindina", "fulgere", "trăznete")),
        ("blizzard", ("crivat", "viscol", "blizzard", "howling wind", "strong wind",
                       "vant puternic", "vânt puternic", "rafale")),
        ("rain_heavy", ("ploaie torențial", "ploaie abundent", "furtună ploaie",
                         "showers heavy")),
        ("rain", ("ploaie", "rain", "drizzle", "shower", "picaturi", "picături",
                   "ploaie ușoar", "ploaie moderată")),
        ("rain_window", ("ploaie geam", "ploaie pe geam", "picaturi geam",
                          "rain on window", "ploaie pe acoperiș")),
        ("thunder_distant", ("tunete îndepărtat", "tunet departe", "thunder distant")),
        ("thunder_close", ("tunete apropiat", "tunet aproape", "tunete apropiate",
                            "thunder close")),
        ("snow", ("ninso", "zapad", "snow", "iarna linist", "fulgi", "ninsoare",
                   "zăpadă")),
        ("snow_walk", ("pasi zapada", "walking snow", "snow crunch", "footsteps snow",
                        "snow underfoot", "zapada pasi", "pași zăpadă",
                        "mers prin zăpadă")),
        ("wind", ("vant", "wind", "breeze", "adiere", "suflare", "vânt", "adieri")),
        ("wind_strong", ("vant puternic", "vânt puternic", "wind strong", "furtună vânt")),
        # Apă și natură
        ("ocean", ("mare", "val", "ocean", "wave", "beach", "litoral", "coasta",
                    "valuri", "plajă", "delfini", "dolphin")),
        ("ocean_storm", ("mare agitat", "furtună mare", "ocean storm", "valuri mari",
                          "valuri puternice")),
        ("river", ("rau", "river", "parau", "brook", "stream", "cascada", "waterfall",
                    "râu", "pârâu", "cascadă")),
        ("fountain", ("fântân", "fountain", "artezian", "izvor", "spring",
                       "apă curgând", "jet apă")),
        ("lake", ("lac", "lake", "lebede", "swan", "stuf", "trestie", "pont", " pontoane")),
        ("rainforest", ("pădure tropical", "jungle", "rainforest", "tropice")),
        # Oraș și transport
        ("city", ("oras", "city", "trafic", "traffic", "strada", "street", "urban",
                   "bulevard", "oraș", "intersecție", "aglomerat")),
        ("city_heavy", ("trafic intens", "ambuteiaj", "mult", "many cars",
                         "heavy traffic", "ore vârf", "claxoane")),
        ("train", ("tren", "train", "railroad", "railway", "sine", "vagon",
                    "tren în mers", "tren în tunel")),
        ("station", ("gara", "station", "peron", "aeroport", "airport", "terminal",
                      "announcement", "anunt", "metrou", "autogara", "gară", "stație")),
        ("station_train_coming", ("tren sosire", "tren care vine", "tren intrare",
                                   "tren plecare", "train arriving")),
        ("metro", ("metrou", "metro", "subway", "tramvai", "tram")),
        ("bus", ("autobuz", "bus", "troleibuz", "trolleybus", "taxi")),
        ("cars", ("mașin", "masin", "cars", "automobil", "motor", "vehicul")),
        ("sirens", ("siren", "politi", "ambulanta", "pompieri", "sirenă",
                     "mașină poliție")),
        ("airport", ("avion", "airport", "decolare", "aterizare", "poartă", "îmbarcare")),
        # NOI - transport avansat
        ("boat", ("vapor", "barcă", "barca", "navă", "vas", "bărcuță", "ferry",
                   "șalupă", "pescuit", "pescar")),
        ("harbor", ("port", "chei", "dană", "doc", "marină", "port pescuit")),
        # Animale
        ("crickets", ("greier", "cricket", "noapte linistita", "quiet night",
                       "seara camp", "noapte", "insecte")),
        ("birds_morning", ("păsări dimineață", "birds morning", "cânt păsări",
                            "păsări cântă", "birds chirping")),
        ("birds", ("păsări", "birds", "pasari", "ciocănit", "pădure păsări")),
        ("birds_lake", ("păsări lac", "rațe", "lebede", "broaște", "lake birds",
                         "pescăruși")),
        ("farm", ("fermă", "farm", "găini", "cocoș", "vacă", "oi", "capre",
                   "animal", "grajd", "curte")),
        ("dogs", ("câine", "caine", "dog", "câini", "dogs", "lătrat", "latrat")),
        ("cats", ("pisică", "pisica", "cat", "pisici", "cats", "tors", "miorcăit")),
        ("squirrels", ("veveri", "squirrel", "veveriță", "frunze", "nuci")),
        ("bees", ("albine", "albină", "stup", "miere", "viespe", "bondar", "muscă")),
        ("frogs", ("broaște", "broască", "frog", "baltă", "mlaștină")),
        # Interioare
        ("cafe", ("cafenea", "cafe", "coffee shop", "restaurant", "bistro", "bar",
                   "ceainarie", "clopoțel", "vânzător")),
        ("restaurant", ("restaurant", "restaurant aglomerat", "restaurant quiet")),
        ("bakery", ("brutărie", "bakery", "cuptor", "pâine", "covrig",
                     "croasant", "foieta")),
        ("store", ("magazin", "store", "supermarket", "cumpărături",
                    "cărucior", "produse")),
        ("library", ("bibliotecă", "library", "liniște", "lectură")),
        ("office", ("birou", "office", "tastatură", "imprimantă", "telefon")),
        ("hospital", ("spital", "hospital", "cabinet medical", "clinică")),
        ("school", ("școală", "school", "universitate", "curs", "studenți")),
        # NOI - interioare speciale
        ("lab", ("laborator", "știință", "cercetare", "experiment", "reactor")),
        ("drilling", ("freză", "dentist", "foraj", "percuție", "burghiu",
                       "mașină de găurit", "polizor")),
        ("gym", ("sală sport", "sala sport", "fitness", "gimnastică", "exerciții",
                  "greutăți", "antrenament")),
        ("pool", ("piscină", "înot", "natatie", "înotat", "inot", "piscina")),
        ("arcade", ("jocuri", "arcadă", "retro jocuri", "pacman", "flipper",
                     "sală jocuri", "gaming")),
        # Activități
        ("kitchen", ("bucătărie", "kitchen", "mixer", "blender", "tigaie",
                      "fierbător", "espressor", "café")),
        ("cooking", ("gătit", "gatit", "cooking", "prăjit", "fiert", "cuptor",
                      "Tigaie", "capac", "scântei")),
        ("typing", ("tastatură", "typing", "keyboard", "calculator", "computer",
                     "click", "mouse")),
        ("vacuum", ("aspirator", "vacuum", "curățenie", "mop", "găleată")),
        ("washing", ("mașină spălat", "washing machine", "usucator", "spălat rufe")),
        ("tv", ("televizor", "tv", "television", "telecomandă", "știri", "emisiune")),
        ("radio", ("radio", "radio on", "muzică radio")),
        ("phone", ("telefon", "telefon mobil", "sonerie", "apel", "notificare",
                    "rington", "ding", "alarmă telefon")),
        # Food sounds
        ("chips", ("chips", "ronțăit", "chipsuri", "alune", "porumb",
                    "popcorn", "covrig", "biscuiți")),
        ("eating", ("mestec", "mancat", "eating", "înghițit", "gheață", "băut")),
        ("drinking", ("băut", "drinking", "cafea", "apă", "suc", "halba", "pahar")),
        ("coffee_machine", ("espreso", "espresso", "café", "aparat cafea", "cafea prepar")),
        ("baby", ("bebeluș", "copil", "plânset", "trosnituri", "mama")),
        # Birou și casă
        ("room", ("cameră", "room", "casă", "house", "home", "interior", "liniștit")),
        ("heartbeat", ("bătăi inimă", "heartbeat", "inimă", "emoții")),
        ("clock", ("ceas", "clock", "ticăit", "tac", "timp")),
        # Scări și mișcare
        ("stairs", ("scar", "stairs", "scări", "urcare", "coborâre", "lift", "ascensor")),
        ("footsteps", ("pași", "footsteps", "pas", "mers", "alerg", "alergare")),
        ("footsteps_wood", ("pași parchet", "footsteps wood", "parchet", "lemn",
                             "lemn podea")),
        ("footsteps_tile", ("pași gresie", "gresie", "tile floor", "beton")),
        ("footsteps_outside", ("pași afară", "pietris", "asfalt", "iérbă", "outside")),
        ("heels", ("tocuri", "heels", "pantof cu toc", "toc pantof",
                    "tocuri pe parchet", "tocuri pe gresie")),
        ("heely", ("adidași", "adidasi", "sneakers", "sport", "tenisi")),
        ("ice", ("patinaj", "gheață", "patine", "patinoar", "schi", "snowboard")),
        # Countryside
        ("countryside", ("tara", "sat", "countryside", "ferma", "cimp", "rural",
                          "birds chirp", "livada", "țară", "mediu rural")),
        ("countryside_morning", ("dimineață țară", "morning countryside", "cocoș",
                                  "găini", "soare", "sat dimineață")),
        ("countryside_night", ("noapte sat", "countryside night", "greieri",
                                "linie", "tăcere")),
        ("tractor", ("tractor", "tractor câmp", "tractor drum", "agricol", "combine")),
        ("cart", ("căruță", "caruta", "căruț", "roți lemn", "cal căruță",
                   "trăsură", "clopoței")),
        ("chickens", ("pui", "găini", "chickens", "pio", "rațe", "boboci")),
        # Shopping
        ("shopping_mall", ("mall", "centru comercial", "scări rulante", "lift", "aglomerat")),
        ("checkout", ("casă marcat", "checkout", "scanare", "bip", "plată",
                       "card", "numerar", "bancnot")),
        ("shopping_bags", ("sacose", "pungi", "shopping bags", "cumpărături")),
        # Machiaj și baie
        ("makeup", ("machiaj", "makeup", "pensul", "fard", "ruj", "pudră",
                     "oglindă", "cremă")),
        ("bathroom", ("baie", "bathroom", "robinet", "duș", "cadă",
                       "prosoape", "sertar", "chiuvetă")),
        ("water_faucet", ("apă curge", "robinet", "faucet", "duș",
                           "apă rece", "apă caldă")),
        # Sezoane
        ("spring", ("primăvară", "spring", "păsări", "natură", "flori")),
        ("summer", ("vară", "summer", "greieri", "căldură", "zgomote vară")),
        ("autumn", ("toamnă", "autumn", "frunze", "vânt", "ploaie")),
        ("winter", ("iarnă", "winter", "zăpadă", "ger", "vânt rece", "crivăț")),
        # Diverse
        ("forest", ("padure", "forest", "frunze", "copac", "woods",
                     "jungle", "livada", "pădure")),
        ("forest_walk", ("pasi padure", "walking forest", "footsteps leaves",
                          "leaves underfoot", "crunch leaves", "rustling underfoot",
                          "mers padure", "fosnet pasi", "frunze", "crengi")),
        ("party", ("petrecere", "party", "muzică", "aplaud", "haha",
                    "petrec", "festival")),
        ("crowd", ("mulțime", "crowd", "oameni", "aglomerat", "galerie", "stadio")),
        ("airplane", ("avion", "airplane", "decolare", "zbor", "motor", "cabină")),
        ("helicopter", ("elicopter", "helicopter", "rotor", "zbor")),
        # NOI - industriale
        ("factory", ("fabrică", "uzină", "industrie", "productie", "linie asamblare",
                      "hale", "atelier mecanic")),
        ("construction", ("constructie", "șantier", "excavator", "buldozer",
                           "macara", "ciocan", "daltă", "găuri", "zidar")),
        ("shooting", ("tir", "arma", "pistol", "pușcă", "foc", "focuri",
                       "împușcături", "glonț", "muniție", "range")),
        ("explosion", ("explozie", "bombă", "detunătură", "grenadă", "dinamită",
                        "erupție", "big bang")),
        # Noapte
        ("night", ("noapte", "night", "liniște", "tăcere", "stele")),
        ("night_city", ("noapte oras", "night city", "neon", "stradă noapte",
                         "trafic noapte")),
        ("crickets_night", ("greieri noapte", "crickets night", "insecte noapte", "broaște")),
        ("wolf", ("lup", "wolf", "noapte", "pădure noapte", "urlat")),
        ("owl", ("bufniță", "owl", "noapte", "pădure noapte", "huhuz")),
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)


def mix_voice_ambient(voice_bytes, ambient_bytes, voice_vol=1.0, ambient_vol=0.5):
    """Combină o voce cu un sunet ambiental într-un singur fișier audio.

    Args:
        voice_bytes: Audio-ul vocii (bytes)
        ambient_bytes: Audio-ul ambiental (bytes)
        voice_vol: Volumul vocii (0.0 - 1.0)
        ambient_vol: Volumul ambiental (0.0 - 1.0)

    Returns:
        bytes: Audio combinat (WAV)
    """
    try:
        import soundfile as sf

        # Citește vocea
        voice_data, voice_sr = sf.read(io.BytesIO(voice_bytes))
        if len(voice_data.shape) > 1:
            voice_data = voice_data.mean(axis=1)

        # Citește ambientul
        ambient_data, ambient_sr = sf.read(io.BytesIO(ambient_bytes))
        if len(ambient_data.shape) > 1:
            ambient_data = ambient_data.mean(axis=1)

        # Resample ambientul la rata vocii dacă e necesar
        if ambient_sr != voice_sr:
            ratio = voice_sr / ambient_sr
            new_len = int(len(ambient_data) * ratio)
            ambient_data = np.interp(
                np.linspace(0, len(ambient_data) - 1, new_len),
                np.arange(len(ambient_data)),
                ambient_data
            )

        # Extinde ambientul pentru a acoperi toată durata vocii
        voice_len = len(voice_data)
        ambient_len = len(ambient_data)

        if ambient_len < voice_len:
            repeats = (voice_len // ambient_len) + 2
            ambient_extended = np.tile(ambient_data, repeats)
            ambient_final = ambient_extended[:voice_len]
        else:
            ambient_final = ambient_data[:voice_len]

        # Normalizează
        voice_norm = voice_data / (np.max(np.abs(voice_data)) + 1e-9)
        ambient_norm = ambient_final / (np.max(np.abs(ambient_final)) + 1e-9)

        # Mix
        mixed = voice_norm * voice_vol + ambient_norm * ambient_vol
        mixed = np.clip(mixed, -1.0, 1.0)

        # Salvează
        output = io.BytesIO()
        sf.write(output, mixed.astype(np.float32), voice_sr, format='WAV')
        return output.getvalue()

    except ImportError:
        return voice_bytes
    except Exception as e:
        print(f"⚠️ Eroare mix: {e}")
        return voice_bytes


def generate_ambient(name_or_category, duration=12.0):
    """Generează un sunet ambiental bazat pe nume sau categorie.

    Args:
        name_or_category: Numele sau categoria sunetului
        duration: Durata în secunde

    Returns:
        bytes: Audio-ul generat
    """
    text = str(name_or_category or "").lower()
    return sound_effect(text, duration=duration)


# ════════════════════════════════════════════════════
#  CONTEXT-AWARE AMBIENT SELECTION
# ════════════════════════════════════════════════════

# Hartă locații / contexte în română → preseturi ambientale
_LOCATION_AMBIENT_MAP: dict[str, str] = {
    # Natură
    "plajă": "ocean",
    "mare": "ocean",
    "litoral": "ocean",
    "ocean": "ocean",
    "în vacanță la mare": "ocean",
    "în concediu la mare": "ocean",
    "pe plajă": "ocean",
    "la malul mării": "ocean",

    "acasă": "room",
    "în casă": "room",
    "acasă la el": "room",
    "înapoi acasă": "room",
    "intră în casă": "room",

    "pădure": "forest",
    "în pădure": "forest",
    "la pădure": "forest",
    "mers prin pădure": "forest_walk",
    "plimbare prin pădure": "forest_walk",
    "drumeție": "forest_walk",

    "munte": "wind_strong",
    "la munte": "wind_strong",
    "în munți": "wind_strong",
    "pe munte": "wind_strong",

    "parc": "birds_morning",
    "la parc": "birds_morning",
    "în parc": "birds_morning",

    "grădină": "birds_morning",
    "în grădină": "birds_morning",

    # Oraș
    "oraș": "city",
    "centru": "city",
    "orașul vechi": "city",
    "stradă": "city",
    "în oraș": "city",

    "trafic": "city_heavy",
    "intersecție": "city_heavy",
    "la semafor": "city_heavy",

    "cafenea": "cafe",
    "café": "cafe",
    "coffee": "cafe",
    "cafenea cu prieteni": "cafe",
    "ieșit la cafenea": "cafe",

    "bar": "cafe",
    "restaurant": "restaurant",
    "la restaurant": "restaurant",
    "cină la restaurant": "restaurant",

    # Clădiri
    "bibliotecă": "library",
    "la bibliotecă": "library",

    "birou": "office",
    "la birou": "office",
    "servici": "office",
    "muncă": "office",
    "serviciu": "office",

    "școală": "school",
    "la școală": "school",
    "universitate": "school",
    "facultate": "school",
    "curs": "school",

    "spital": "hospital",
    "la spital": "hospital",

    # Magazin
    "magazin": "store",
    "la cumpărături": "store",
    "supermarket": "store",

    "mall": "shopping_mall",
    "centru comercial": "shopping_mall",

    # Casă
    "bucătărie": "kitchen",
    "în bucătărie": "kitchen",
    "gătește": "cooking",
    "gătit": "cooking",

    "baie": "bathroom",
    "în baie": "bathroom",
    "duș": "bathroom",

    "dormitor": "room",
    "sufragerie": "room",
    "living": "room",

    "televizor": "tv",
    "tv": "tv",
    "emisiune": "tv",

    # Transport
    "tren": "train",
    "în tren": "train",
    "cu trenul": "train",

    "gară": "station",
    "în gară": "station",

    "metrou": "metro",
    "cu metroul": "metro",

    "autobuz": "bus",
    "cu autobuzul": "bus",

    "mașină": "cars",
    "mașina": "cars",
    "cu mașina": "cars",
    "conduce": "cars",
    "condus": "cars",
    "șofer": "cars",

    "avion": "airport",
    "aeroport": "airport",
    "în avion": "airport",
    "zbor": "airport",
    "călătorește cu avionul": "airport",
    "în așteptare la poartă": "airport",
    "îmbarcare": "airport",

    "taxi": "cars",

    # Activități
    "petrecere": "party",
    "la petrecere": "party",
    "party": "party",

    "gym": "gym",
    "sport": "gym",
    "antrenament": "gym",
    "sală": "gym",

    "piscină": "pool",
    "înot": "pool",

    "pescuit": "boat",
    "la pescuit": "boat",
    "pe lac": "river",

    "plimbare": "forest_walk",
    "plimbare prin parc": "forest_walk",

    # Vreme
    "plouă": "rain",
    "ploaie": "rain",
    "incepe să plouă": "rain",
    "plouă afară": "rain",
    "ploaie torențială": "storm",

    "furtună": "storm",
    "este furtună": "storm",
    "tună": "storm",
    "fulgeră": "storm",

    "zăpadă": "snow",
    "ninge": "snow",
    "ninsoare": "snow",
    "iarnă": "snow",

    "vânt": "wind",
    "bate vântul": "wind",
    "este vânt": "wind",

    # Diverse
    "foc": "fire",
    "foc de tabără": "fire",
    "șemineu": "fire",
    "la foc": "fire",

    "noapte": "crickets_night",
    "noaptea": "crickets_night",
    "seara": "crickets_night",
    "noapte târziu": "crickets_night",

    "dimineață": "birds_morning",
    "dimineața": "birds_morning",

    # Cultură
    "concert": "party",
    "teatru": "crowd",
    "film": "tv",
    "cinema": "tv",
    "la film": "tv",
    "la cinema": "tv",

    # Țară / rural
    "la țară": "countryside",
    "sat": "countryside",
    "în sat": "countryside",
    "la fermă": "farm",
    "fermă": "farm",
}


def suggest_ambient_for_location(location_text: str) -> str:
    """Sugerează un preset ambiental pe baza textului de locație.

    Args:
        location_text: Textul care descrie locația (română sau engleză)

    Returns:
        str: Numele presetului ambiental sau None dacă nu se găsește
    """
    text = str(location_text or "").lower().strip()

    if not text:
        return None

    # Căutare exactă
    if text in _LOCATION_AMBIENT_MAP:
        return _LOCATION_AMBIENT_MAP[text]

    # Căutare substring
    for key, preset in _LOCATION_AMBIENT_MAP.items():
        if key in text:
            return preset

    return None


def extract_actions(text):
    """Extrage cuvinte cheie de acțiune din text pentru sugestii ambientale.

    Args:
        text: Textul din care se extrag acțiunile

    Returns:
        list: Listă de cuvinte cheie
    """
    if not text:
        return []

    text_lower = str(text).lower()
    keywords = [
        "ploaie", "plouă", "storm", "furtună", "tunet", "fulger",
        "mare", "ocean", "plajă", "valuri",
        "pădure", "forest", "munte", "parc",
        "cafea", "cafenea", "restaurant", "bar",
        "tren", "avion", "mașină", "autobuz", "metrou", "gară", "aeroport",
        "bucătărie", "gătit", "mâncare",
        "chiuvetă", "baie", "duș",
        "petrecere", "concert", "muzică", "dans",
        "plimbare", "alergare", "sport",
        "grădină", "flori", "natură",
        "foc", "șemineu",
        "casă", "dormitor", "living", "sufragerie",
        "noapte", "seară", "dimineață",
        "iarnă", "zăpadă", "ploaie", "vânt",
        "fabrică", "industria", "construcție",
        "telefon", "apel", "convorbire",
        "joc", "jocuri", "gaming",
        "pescuit", "lac", "râu",
        "piscină", "înot",
        "sport", "exercițiu", "fitness",
        "școală", "curs", "studiu",
        "spital", "dentist", "medic",
        "tir", "armă",
        "explozie", "bombă",
        "laborator", "experiment",
        "televizor", "film", "știri",
        "radio", "muzică",
        "copil", "bebeluș",
        "cumpărături", "magazin", "mall",
    ]
    found = [kw for kw in keywords if kw in text_lower]
    return found
