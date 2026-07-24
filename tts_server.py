"""Server local FastAPI pentru generarea vocii cu Chatterbox TTS.

Rulează separat de Streamlit, pe portul 5001.
Chatterbox nu necesită text de referință — clonează vocea direct din mostră audio.
"""
import base64
import io
import logging
import os
import tempfile
from pathlib import Path

import torchaudio
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TTS] %(message)s")
log = logging.getLogger("tts_server")

app = FastAPI(title="Persona TTS Server", version="1.0")

_model = None
_VOICE_DIR = Path(os.environ.get("VOICE_SAMPLES_DIR", "/tmp/persona_voices"))
_VOICE_DIR.mkdir(parents=True, exist_ok=True)


def _load_model():
    global _model
    if _model is not None:
        return _model
    log.info("Se încarcă modelul Chatterbox TTS (prima rulare poate dura 2-3 minute)...")
    from chatterbox.tts import ChatterboxTTS
    _model = ChatterboxTTS.from_pretrained(device="cpu")
    log.info("Modelul Chatterbox a fost încărcat.")
    return _model


# ── Modele Pydantic ─────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    voice_id: str
    audio_b64: str
    sample_name: str = "reference.wav"


class TTSRequest(BaseModel):
    text: str
    voice_id: str
    exaggeration: float = 0.5   # intensitate emoțională (0=neutru, 1=dramatic)
    cfg_weight: float = 0.5     # fidelitate față de vocea de referință


class PreviewRequest(BaseModel):
    text: str
    audio_b64: str
    sample_name: str = "reference.wav"
    exaggeration: float = 0.5
    cfg_weight: float = 0.5


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _model is not None}


@app.post("/warmup")
def warmup():
    """Pre-încarcă modelul în memorie fără să genereze audio."""
    _load_model()
    return {"status": "ok"}


@app.post("/register")
def register(req: RegisterRequest):
    """Salvează o mostră vocală pe disc, indexată după voice_id."""
    try:
        sample_bytes = base64.b64decode(req.audio_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Mostră audio invalidă: {exc}")

    suffix = Path(req.sample_name).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"

    voice_path = _VOICE_DIR / f"{req.voice_id}{suffix}"
    voice_path.write_bytes(sample_bytes)
    log.info("Voce înregistrată: %s (%d bytes)", req.voice_id, len(sample_bytes))
    return {"status": "ok", "voice_id": req.voice_id}


@app.post("/tts")
def tts(req: TTSRequest):
    """Generează audio pentru un text, folosind vocea indexată după voice_id."""
    # Caută fișierul de mostră
    voice_path = None
    for ext in [".wav", ".mp3", ".m4a", ".ogg", ".flac"]:
        p = _VOICE_DIR / f"{req.voice_id}{ext}"
        if p.exists():
            voice_path = str(p)
            break

    if voice_path is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Mostra vocală nu a fost găsită. Editează personajul și "
                "re-încarcă mostra audio pentru a activa vocea."
            ),
        )

    model = _load_model()
    text = str(req.text).strip() or "..."
    exaggeration = max(0.0, min(1.0, float(req.exaggeration)))
    cfg_weight = max(0.0, min(1.0, float(req.cfg_weight)))

    log.info("Generare TTS: %d caractere, voice_id=%s", len(text), req.voice_id)
    try:
        wav = model.generate(
            text,
            audio_prompt_path=voice_path,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )
    except Exception as exc:
        log.error("Eroare generare TTS: %s", exc)
        raise HTTPException(status_code=500, detail=f"Eroare generare voce: {exc}")

    buf = io.BytesIO()
    torchaudio.save(buf, wav, model.sr, format="wav")
    log.info("Audio generat: %d bytes", buf.tell())
    return Response(content=buf.getvalue(), media_type="audio/wav")


@app.post("/preview")
def preview(req: PreviewRequest):
    """Generează un preview audio direct din bytes (înainte de salvare)."""
    try:
        sample_bytes = base64.b64decode(req.audio_b64)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Mostră audio invalidă: {exc}")

    suffix = Path(req.sample_name).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"

    model = _load_model()
    text = str(req.text).strip() or "..."
    exaggeration = max(0.0, min(1.0, float(req.exaggeration)))
    cfg_weight = max(0.0, min(1.0, float(req.cfg_weight)))

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(sample_bytes)
        tmp_path = tmp.name

    try:
        log.info("Preview TTS: %d caractere", len(text))
        wav = model.generate(
            text,
            audio_prompt_path=tmp_path,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
        )
    except Exception as exc:
        log.error("Eroare preview TTS: %s", exc)
        raise HTTPException(status_code=500, detail=f"Eroare generare preview: {exc}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    buf = io.BytesIO()
    torchaudio.save(buf, wav, model.sr, format="wav")
    return Response(content=buf.getvalue(), media_type="audio/wav")


# ── Startup ──────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup_warmup():
    """Încarcă modelul în fundal imediat după pornirea serverului."""
    import threading
    threading.Thread(target=_load_model, daemon=True).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001, log_level="info")
