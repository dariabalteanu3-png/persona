"""Server local FastAPI pentru generarea vocii cu Coqui XTTS v2.

Rulează separat de Streamlit, pe portul 5001.
XTTS v2 clonează vocea direct din mostra audio — nu necesită text de referință.
Suportă română cu expresivitate emoțională ridicată.
"""
import os
import io
import logging
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TTS] %(message)s")
log = logging.getLogger("tts_server")

app = FastAPI(title="Persona TTS Server (Coqui XTTS v2)", version="1.0")

_VOICE_DIR = Path(os.environ.get("VOICE_SAMPLES_DIR", "/tmp/persona_voices"))
_VOICE_DIR.mkdir(parents=True, exist_ok=True)

_model = None
_speaker_wavs: dict = {}  # voice_id -> path to wav file


def _check_cuda():
    """Verifică dacă CUDA este disponibil."""
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _load_model():
    """Încarcă modelul Coqui XTTS v2 (prima dată descarcă ~1.7 GB)."""
    global _model
    if _model is not None:
        return _model
    log.info(
        "Se încarcă modelul Coqui XTTS v2 (prima dată poate dura 3-5 minute)..."
    )
    from TTS.api import TTS

    device = "cuda" if _check_cuda() else "cpu"
    log.info(f"Folosesc device: {device}")
    _model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    log.info("Modelul Coqui XTTS v2 a fost încărcat.")
    return _model


def _audio_to_wav_bytes(wav_array, sample_rate=24000):
    """Convertește numpy array la bytes WAV."""
    buf = io.BytesIO()
    wav_array = np.asarray(wav_array, dtype=np.float32)
    wav_array = np.clip(wav_array, -1.0, 1.0)
    sf.write(buf, wav_array, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _is_wav(path):
    """Verifică dacă fișierul este WAV valid."""
    try:
        data, sr = sf.read(str(path))
        return True
    except Exception:
        return False


def _convert_to_wav(src_path, dst_path):
    """Convertește orice format audio în WAV pentru compatibilitate cu XTTS."""
    data, sr = sf.read(str(src_path))
    if len(data.shape) > 1:
        data = data.mean(axis=1)  # mono if stereo
    sf.write(str(dst_path), data, sr, format="WAV", subtype="PCM_16")
    return str(dst_path)


# ── Modele Pydantic ─────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    voice_id: str
    audio_b64: str
    sample_name: str = "reference.wav"


class TTSRequest(BaseModel):
    text: str
    voice_id: str
    exaggeration: float = 0.5   # intensivitate emoțională (0=neutru, 1=dramatic)
    cfg_weight: float = 0.5     # (ignorat de XTTS, păstrat pentru compatibilitate)


class PreviewRequest(BaseModel):
    text: str
    audio_b64: str
    sample_name: str = "reference.wav"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None}


@app.post("/warmup")
def warmup():
    """Pre-încarcă modelul în memorie fără a genera audio."""
    _load_model()
    return {"status": "ok"}


@app.post("/register")
def register(req: RegisterRequest):
    """Salvează o mostră vocală pe disc, indexată după voice_id."""
    try:
        import base64
        sample_bytes = base64.b64decode(req.audio_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Mostră audio invalidă: {exc}")

    suffix = Path(req.sample_name).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"

    voice_path = _VOICE_DIR / f"{req.voice_id}{suffix}"
    voice_path.write_bytes(sample_bytes)
    log.info("Voce înregistrată: %s (%d bytes)", req.voice_id, len(sample_bytes))

    # Convertește în WAV dacă e necesar (XTTS funcționează cel mai bine cu WAV)
    if suffix != ".wav":
        try:
            wav_path = _VOICE_DIR / f"{req.voice_id}.wav"
            _convert_to_wav(str(voice_path), str(wav_path))
            voice_path = wav_path
            log.info("Conversie în WAV completată pentru: %s", req.voice_id)
        except Exception as exc:
            log.warning("Nu s-a putut converti în WAV: %s", exc)

    _speaker_wavs[req.voice_id] = str(voice_path)
    return {"status": "ok", "voice_id": req.voice_id}


@app.post("/tts")
def tts(req: TTSRequest):
    """Generează audio pentru un text, folosind vocea indexată după voice_id."""
    # Caută fișierul de mostră
    speaker_wav = _speaker_wavs.get(req.voice_id)
    if speaker_wav is None:
        for ext in [".wav", ".mp3", ".m4a", ".ogg", ".flac"]:
            p = _VOICE_DIR / f"{req.voice_id}{ext}"
            if p.exists():
                speaker_wav = str(p)
                if not _is_wav(p):
                    # Convertim pe loc
                    wav_path = _VOICE_DIR / f"{req.voice_id}.wav"
                    _convert_to_wav(speaker_wav, str(wav_path))
                    speaker_wav = str(wav_path)
                _speaker_wavs[req.voice_id] = speaker_wav
                break

    if speaker_wav is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Mostra vocală nu a fost găsită. Editează personajul și "
                "re-încarcă mostra audio."
            ),
        )

    model = _load_model()
    text = str(req.text).strip() or "..."
    # Mapare: exaggeration 0-1 → temperature 0.5-1.3
    # temperature mai mare = mai expresiv / emocional
    temperature = max(0.1, min(1.5, 0.5 + float(req.exaggeration) * 0.8))

    log.info("Generare TTS: %d caractere, voice_id=%s, temp=%.2f", len(text), req.voice_id, temperature)
    try:
        wav = model.tts(
            text=text,
            speaker_wav=speaker_wav,
            language="ro",
            temperature=temperature,
        )
    except Exception as exc:
        log.error("Eroare generare TTS: %s", exc)
        raise HTTPException(status_code=500, detail=f"Eroare generare voce: {exc}")

    wav_bytes = _audio_to_wav_bytes(wav)
    log.info("Audio generat: %d bytes", len(wav_bytes))
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/preview")
def preview(req: PreviewRequest):
    """Generează un preview audio direct din bytes (înainte de salvare)."""
    try:
        import base64
        sample_bytes = base64.b64decode(req.audio_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Mostră audio invalidă: {exc}")

    suffix = Path(req.sample_name).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"

    # Salvăm temporar
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(sample_bytes)
        tmp_path = tmp.name

    # Convertim în WAV dacă e nevoie
    wav_tmp_path = None
    try:
        if suffix != ".wav":
            wav_tmp_path = _VOICE_DIR / f"_preview_{os.getpid()}.wav"
            _convert_to_wav(tmp_path, str(wav_tmp_path))
            effective_path = str(wav_tmp_path)
        else:
            effective_path = tmp_path
    except Exception:
        effective_path = tmp_path

    model = _load_model()
    text = str(req.text).strip() or "..."
    temperature = max(0.1, min(1.5, 0.5 + float(req.exaggeration) * 0.8))

    log.info("Preview TTS: %d caractere, temp=%.2f", len(text), temperature)
    try:
        wav = model.tts(
            text=text,
            speaker_wav=effective_path,
            language="ro",
            temperature=temperature,
        )
    except Exception as exc:
        log.error("Eroare preview TTS: %s", exc)
        raise HTTPException(status_code=500, detail=f"Eroare generare preview: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        if wav_tmp_path:
            try:
                os.unlink(str(wav_tmp_path))
            except OSError:
                pass

    wav_bytes = _audio_to_wav_bytes(wav)
    return Response(content=wav_bytes, media_type="audio/wav")


# ── Startup ──────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup_warmup():
    """Încarcă modelul în fundal imediat după pornirea serverului."""
    import threading
    threading.Thread(target=_load_model, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001, log_level="info")
