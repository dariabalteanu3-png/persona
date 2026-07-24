"""Server local FastAPI pentru generarea vocii cu Coqui XTTS v2.

Ruleaza separat de Streamlit, pe portul 5001.
XTTS v2 cloneaza vocea direct din mostra audio — nu necesita text deReferinta.
Suporta romana cu expresivitate emotionala ridicata.
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
_speaker_wavs: dict = {}


def _check_cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except ImportError:
        return False


def _load_model():
    global _model
    if _model is not None:
        return _model
    log.info("Se incarca modelul Coqui XTTS v2 (prima data poate dura 3-5 minute)...")
    from TTS.api import TTS
    device = "cuda" if _check_cuda() else "cpu"
    log.info(f"Folosesc device: {device}")
    _model = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    log.info("Modelul Coqui XTTS v2 a fost incarcat.")
    return _model


def _audio_to_wav_bytes(wav_array, sample_rate=24000):
    buf = io.BytesIO()
    wav_array = np.asarray(wav_array, dtype=np.float32)
    wav_array = np.clip(wav_array, -1.0, 1.0)
    sf.write(buf, wav_array, sample_rate, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def _is_wav(path):
    try:
        sf.read(str(path))
        return True
    except Exception:
        return False


def _convert_to_wav(src_path, dst_path):
    data, sr = sf.read(str(src_path))
    if len(data.shape) > 1:
        data = data.mean(axis=1)
    sf.write(str(dst_path), data, sr, format="WAV", subtype="PCM_16")
    return str(dst_path)


# ── Modele Pydantic ─────────────────────────────────────────

class RegisterRequest(BaseModel):
    voice_id: str
    audio_b64: str
    sample_name: str = "reference.wav"


class TTSRequest(BaseModel):
    text: str
    voice_id: str
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


class PreviewRequest(BaseModel):
    text: str
    audio_b64: str
    sample_name: str = "reference.wav"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


# ── Endpoints ────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None}


@app.post("/warmup")
def warmup():
    _load_model()
    return {"status": "ok"}


@app.post("/register")
def register(req: RegisterRequest):
    try:
        import base64
        sample_bytes = base64.b64decode(req.audio_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Mostra audio invalida: {exc}")

    suffix = Path(req.sample_name).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"

    voice_path = _VOICE_DIR / f"{req.voice_id}{suffix}"
    voice_path.write_bytes(sample_bytes)
    log.info("Voce inregistrata: %s (%d bytes)", req.voice_id, len(sample_bytes))

    # Convertim in WAV daca e necesar
    final_path = str(voice_path)
    if suffix != ".wav":
        try:
            wav_path = _VOICE_DIR / f"{req.voice_id}.wav"
            _convert_to_wav(str(voice_path), str(wav_path))
            final_path = str(wav_path)
            log.info("Convertit in WAV pentru: %s", req.voice_id)
        except Exception as exc:
            log.warning("Nu s-a putut converti in WAV: %s", exc)

    _speaker_wavs[req.voice_id] = final_path
    return {"status": "ok", "voice_id": req.voice_id}


@app.post("/tts")
def tts(req: TTSRequest):
    speaker_wav = _speaker_wavs.get(req.voice_id)
    if speaker_wav is None:
        for ext in [".wav", ".mp3", ".m4a", ".ogg", ".flac"]:
            p = _VOICE_DIR / f"{req.voice_id}{ext}"
            if p.exists():
                speaker_wav = str(p)
                if not _is_wav(p):
                    wav_path = _VOICE_DIR / f"{req.voice_id}.wav"
                    _convert_to_wav(speaker_wav, str(wav_path))
                    speaker_wav = str(wav_path)
                _speaker_wavs[req.voice_id] = speaker_wav
                break

    if speaker_wav is None:
        raise HTTPException(
            status_code=404,
            detail="Mostra vocala nu a fost gasita. Editeaza personajul si reincarca mostra audio.",
        )

    model = _load_model()
    text = str(req.text).strip() or "..."

    log.info("Generare TTS: %d caractere, voice_id=%s", len(text), req.voice_id)
    try:
        wav = model.tts(
            text=text,
            speaker_wav=speaker_wav,
            language="ro",
        )
    except Exception as exc:
        log.error("Eroare generare TTS: %s", exc)
        raise HTTPException(status_code=500, detail=f"Eroare generare voce: {exc}")

    wav_bytes = _audio_to_wav_bytes(wav)
    log.info("Audio generat: %d bytes", len(wav_bytes))
    return Response(content=wav_bytes, media_type="audio/wav")


@app.post("/preview")
def preview(req: PreviewRequest):
    try:
        import base64
        sample_bytes = base64.b64decode(req.audio_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Mostra audio invalida: {exc}")

    suffix = Path(req.sample_name).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(sample_bytes)
        tmp_path = tmp.name

    wav_tmp_path = None
    effective_path = tmp_path
    try:
        if suffix != ".wav":
            wav_tmp_path = _VOICE_DIR / f"_preview_{os.getpid()}.wav"
            _convert_to_wav(tmp_path, str(wav_tmp_path))
            effective_path = str(wav_tmp_path)
    except Exception:
        effective_path = tmp_path

    model = _load_model()
    text = str(req.text).strip() or "..."

    log.info("Preview TTS: %d caractere", len(text))
    try:
        wav = model.tts(
            text=text,
            speaker_wav=effective_path,
            language="ro",
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


# ── Startup ──────────────────────────────────────────────────

@app.on_event("startup")
async def _startup_warmup():
    import threading
    threading.Thread(target=_load_model, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001, log_level="info")
