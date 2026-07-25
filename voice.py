"""Generare vocală cu XTTS-v2 Romanian v2 (primar) + Chatterbox TTS (fallback).

Suportă:
- Clonare voce din mostră audio
- Limbă română (model XTTS finetuning specific)
- Expresivitate emoțională
- 100% gratuit, open-source
- Funcționează direct în procesul Streamlit, fără server separat
"""

import base64
import hashlib
import io
import os
import re
import time
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
_log = logging.getLogger("voice")


class VoiceGenerationError(RuntimeError):
    """Eroare user-facing de la serviciul de generare vocală."""


# ════════════════════════════════════════════════════
#  Configurare
# ════════════════════════════════════════════════════

XTTS_MODEL_REPO = "eduardem/xtts-v2-romanian-v2"
XTTS_MODEL_DIR = os.environ.get("XTTS_MODEL_DIR", "/tmp/xtts_v2_romanian_model")

_engines: dict = {}  # "xtts" | "chatterbox" → model instance
_engine_available: dict = {}  # cache engine availability checks
_voice_samples: dict = {}  # voice_id → sample_bytes


# ════════════════════════════════════════════════════
#  Detectare motor disponibil
# ════════════════════════════════════════════════════

def _check_xtts():
    """Verifică dacă XTTS-v2 poate fi importat (Coqui TTS trebuie instalat)."""
    if "xtts" in _engine_available:
        return _engine_available["xtts"]
    try:
        from TTS.tts.configs.xtts_config import XttsConfig  # noqa
        from TTS.tts.models.xtts import Xtts  # noqa
        _engine_available["xtts"] = True
        print("🔊 Motor XTTS-v2 disponibil")
        return True
    except ImportError:
        _engine_available["xtts"] = False
        print("⚠️  Motor XTTS-v2 nu e disponibil (TTS package neinstalat)")
        return False


def _check_chatterbox():
    """Verifică dacă Chatterbox TTS poate fi importat."""
    if "chatterbox" in _engine_available:
        return _engine_available["chatterbox"]
    try:
        from chatterbox.tts import ChatterboxTTS  # noqa
        _engine_available["chatterbox"] = True
        print("🔊 Motor Chatterbox TTS disponibil")
        return True
    except ImportError:
        _engine_available["chatterbox"] = False
        print("⚠️  Motor Chatterbox TTS nu e disponibil")
        return False


# ════════════════════════════════════════════════════
#  Motor XTTS-v2 Romanian v2
# ════════════════════════════════════════════════════

def _ensure_xtts_model():
    """Descarcă modelul XTTS-v2 Romanian v2 (dacă nu e deja) și îl încarcă."""
    if "xtts" in _engines:
        return _engines["xtts"]

    print(f"⏳ Se descarcă modelul XTTS-v2 Romanian v2 ({XTTS_MODEL_REPO})...")
    start = time.time()

    from huggingface_hub import snapshot_download
    from TTS.tts.configs.xtts_config import XttsConfig
    from TTS.tts.models.xtts import Xtts
    import torch

    # Descarcă model (doar prima dată, cache în /tmp)
    os.makedirs(XTTS_MODEL_DIR, exist_ok=True)
    snapshot_download(
        repo_id=XTTS_MODEL_REPO,
        local_dir=XTTS_MODEL_DIR,
        local_dir_use_symlinks=False,
    )

    # Încarcă
    config = XttsConfig()
    config.load_json(os.path.join(XTTS_MODEL_DIR, "config.json"))

    model = Xtts(config)
    model.load_checkpoint(
        config,
        checkpoint_path=os.path.join(XTTS_MODEL_DIR, "model.pth"),
        use_deepspeed=False,
    )
    model = model.to("cpu")
    model.eval()

    elapsed = time.time() - start
    print(f"✅ Model XTTS-v2 Romanian v2 încărcat în {elapsed:.1f}s")
    _engines["xtts"] = model
    return model


def _xtts_generate(text, sample_bytes, similarity_boost=0.75, style=0.0):
    """Generează WAV cu XTTS-v2 Romanian v2."""
    import torch
    import numpy as np
    import wave

    model = _ensure_xtts_model()

    # Salvează mostra temporar
    ref_path = "/tmp/_xtts_ref.wav"
    with open(ref_path, "wb") as f:
        f.write(sample_bytes)

    # Obține condiționare
    gpt_cond, speaker_emb = model.get_conditioning_latents(
        audio_path=ref_path,
        gpt_cond_len=3,
        max_ref_length=60,
    )

    # Generează
    temperature = max(0.1, 1.0 - float(similarity_boost) * 0.6)
    top_p = max(0.5, float(similarity_boost))
    length_penalty = 1.0 + float(style) * 0.3

    outputs = model.inference(
        text=text,
        language="ro",
        gpt_cond_latent=gpt_cond,
        speaker_embedding=speaker_emb,
        temperature=temperature,
        length_penalty=length_penalty,
        repetition_penalty=2.0,
        top_k=50,
        top_p=top_p,
    )

    wav = outputs.get("wav") if isinstance(outputs, dict) else outputs
    if torch.is_tensor(wav):
        wav_np = wav.cpu().numpy()
    else:
        wav_np = np.array(wav, dtype=np.float32)
    if wav_np.ndim > 1:
        wav_np = wav_np.squeeze()

    output_sr = 24000
    wav_int = (np.clip(wav_np, -1.0, 1.0) * 32767).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(output_sr)
        wf.writeframes(wav_int.tobytes())

    return buf.getvalue()


# ════════════════════════════════════════════════════
#  Motor Chatterbox TTS (fallback)
# ════════════════════════════════════════════════════

def _ensure_chatterbox():
    """Încarcă Chatterbox TTS (doar dacă e disponibil)."""
    if "chatterbox" in _engines:
        return _engines["chatterbox"]

    if not _check_chatterbox():
        raise VoiceGenerationError("Chatterbox TTS nu e instalat.")

    print("⏳ Se încarcă modelul Chatterbox TTS (poate dura 2-3 minute)...")
    from chatterbox.tts import ChatterboxTTS

    model = ChatterboxTTS.from_pretrained(device="cpu")
    print("✅ Model Chatterbox încărcat!")
    _engines["chatterbox"] = model
    return model


def _chatterbox_generate(text, sample_bytes, similarity_boost=0.75, style=0.0):
    """Generează WAV cu Chatterbox TTS."""
    import torchaudio
    import numpy as np

    model = _ensure_chatterbox()

    exaggeration = max(0.0, min(1.0, float(style) * 1.5 + 0.25))
    cfg_weight = max(0.0, min(1.0, float(similarity_boost)))

    wav = model.generate(
        text=text,
        audio_prompt=sample_bytes,
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
    )

    buf = io.BytesIO()
    torchaudio.save(buf, wav, model.sr, format="wav")
    return buf.getvalue()


# ════════════════════════════════════════════════════
#  Decodare mostră
# ════════════════════════════════════════════════════

def _decode_sample(sample_b64):
    if not sample_b64:
        return None
    if sample_b64.startswith("data:"):
        sample_b64 = sample_b64.split(",", 1)[-1]
    try:
        return base64.b64decode(sample_b64)
    except Exception as exc:
        raise VoiceGenerationError("Mostra audio este invalidă.") from exc


def voice_id_for_sample(sample_bytes):
    if not sample_bytes:
        return None
    return "v:" + hashlib.sha256(sample_bytes).hexdigest()[:24]


def forget_registered_voices(voice_ids=None):
    if voice_ids is None:
        _voice_samples.clear()
        return
    for voice_id in voice_ids:
        _voice_samples.pop(voice_id, None)


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
    """Generează WAV cu vocea clonată.
    
    Încearcă: XTTS-v2 Romanian v2 → Chatterbox TTS → VoiceGenerationError
    Rulează direct în proces — fără server separat.
    """
    sample_bytes = _voice_samples.get(voice_id)
    if not sample_bytes:
        raise VoiceGenerationError(
            "Vocea personajului nu are o mostră salvată. "
            "Editează personajul și reîncarcă mostra audio."
        )

    spoken = _expressify(str(text) if expressive else (text or "..."))

    # Încearcă XTTS-v2 Romanian v2
    xtts_ok = _check_xtts()
    if xtts_ok:
        try:
            print(f"🔊 Generare XTTS-v2 (română): {len(spoken)} caractere...")
            wav = _xtts_generate(
                spoken,
                sample_bytes,
                similarity_boost=similarity_boost,
                style=style,
            )
            print(f"✅ Voce generată: {len(wav)} bytes (XTTS-v2)")
            return wav
        except Exception as exc:
            msg = str(exc)
            if "Killed" in msg or "OutOfMemory" in msg or "SIGKILL" in msg:
                print("⚠️  XTTS-v2: OOM, trec la Chatterbox TTS")
                _engine_available["xtts"] = False
            else:
                print(f"⚠️  XTTS-v2: eroare {exc}, trec la Chatterbox TTS")

    # Fallback: Chatterbox TTS
    if _check_chatterbox():
        try:
            print(f"🔊 Generare Chatterbox: {len(spoken)} caractere...")
            wav = _chatterbox_generate(
                spoken,
                sample_bytes,
                similarity_boost=similarity_boost,
                style=style,
            )
            print(f"✅ Voce generată: {len(wav)} bytes (Chatterbox)")
            return wav
        except Exception as exc:
            print(f"⚠️  Chatterbox: eroare {exc}")

    raise VoiceGenerationError(
        "Niciun motor TTS disponibil. Instalează coqui-tts (pentru XTTS) "
        "sau chatterbox-tts. Vezi README pentru instrucțiuni."
    )


def text_to_speech_from_sample(text, sample_bytes, reference_text=None, sample_name="reference.wav"):
    """Generează preview direct din mostră (înainte de salvarea personajului)."""
    xtts_ok = _check_xtts()
    spoken = _expressify(str(text) or "...")

    if xtts_ok:
        try:
            return _xtts_generate(spoken, sample_bytes, similarity_boost=0.7, style=0.3)
        except Exception:
            pass

    if _check_chatterbox():
        try:
            return _chatterbox_generate(spoken, sample_bytes, similarity_boost=0.5, style=0.0)
        except Exception:
            pass

    raise VoiceGenerationError("Niciun motor TTS disponibil.")


# ════════════════════════════════════════════════════
#  Normalizare text
# ════════════════════════════════════════════════════

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\uFE0F\u2764]"
)


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
    text = text.replace("&", " și ")
    text = text.replace("%", " la sută")
    text = re.sub(r"\.{3}", "… ", text)
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or "..."

# ════════════════════════════════════════════════════
#  SINTEZA AMBIENTALĂ DSP (neschimbată)
# ════════════════════════════════════════════════════
# [Pastrăm funcțiile _ambient_wav și sound_effect exact ca în versiunea anterioară]

def _ambient_wav(preset, duration=12.0, sample_rate=22050):
    """DSP-based ambient synthesis using numpy. Fiecare apel sună ușor diferit."""
    try:
        import numpy as np
    except ImportError:
        output = io.BytesIO()
        with wave.open(output, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * int(sample_rate * duration))
        return output.getvalue()

    import wave
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

    # Preseturi
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
    """Returnează un sunet ambient sintetizat local."""
    text = str(prompt or "").lower()
    presets = (
        ("storm", ("tunet", "furtun", "thunder", "storm", "lightning", "fulger", "grindină")),
        ("blizzard", ("crivăț", "viscol", "blizzard", "howling wind", "strong wind", "vânt puternic")),
        ("rain", ("ploaie", "rain", "drizzle", "shower", "picături")),
        ("ocean", ("mare", "val", "ocean", "wave", "beach", "litoral", "coastă")),
        ("fire", ("foc", "campfire", "fire", "șemineu", "flacăr", "lumânare", "jar")),
        ("wind", ("vânt", "wind", "breeze", "adiere", "suflare")),
        ("forest_walk", ("pași pădure", "walking forest", "footsteps leaves", "leaves underfoot", "crunch leaves", "rustling underfoot", "mers pădure", "foșnet pași")),
        ("crickets", ("greier", "cricket", "noapte liniștit", "quiet night", "seară câmp")),
        ("river", ("râu", "river", "pârâu", "brook", "stream", "cascadă", "waterfall")),
        ("train", ("tren", "train", "railroad", "railway", "șine", "vagon")),
        ("forest", ("pădure", "forest", "frunze", "copac", "woods", "jungle", "livadă")),
        ("cafe", ("cafenea", "cafe", "coffee shop", "restaurant", "bistro", "bar", "ceainărie")),
        ("city", ("oraș", "city", "trafic", "traffic", "stradă", "street", "urban", "bulevard")),
        ("countryside", ("țară", "sat", "countryside", "fermă", "câmp", "rural", "birds chirp", "livadă")),
        ("station", ("gară", "station", "peron", "aeroport", "airport", "terminal", "announcement", "anunț", "metrou", "autogară")),
        ("heels_parquet", ("tocuri", "heels", "parchet", "parquet", "podea", "floor click", "toc pantof", "pantof cu toc", "lemn podea")),
        ("snow_walk", ("pași zăpadă", "walking snow", "snow crunch", "footsteps snow", "snow underfoot", "zăpadă pași")),
        ("snow", ("ninso", "zăpad", "snow", "iarnă liniș", "fulgi")),
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)
