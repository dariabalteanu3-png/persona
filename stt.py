import os
import asyncio
import mimetypes
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv

from provider import (
    USE_GEMINI,
    USE_GROQ,
    GEMINI_TEXT_MODEL,
    GROQ_STT_MODEL,
    gemini_client,
    groq_client,
)

load_dotenv(Path(__file__).parent / ".env")

KEY = os.environ.get("EMERGENT_LLM_KEY")


def _groq_transcribe(audio_bytes, filename):
    bio = BytesIO(audio_bytes)
    bio.name = filename
    resp = groq_client().audio.transcriptions.create(
        model=GROQ_STT_MODEL, file=bio, language="ro", response_format="text"
    )
    if isinstance(resp, str):
        return resp.strip()
    return getattr(resp, "text", str(resp)).strip()


def _gemini_transcribe(audio_bytes, filename):
    from google.genai import types
    mime = mimetypes.guess_type(filename)[0] or "audio/wav"
    resp = gemini_client().models.generate_content(
        model=GEMINI_TEXT_MODEL,
        contents=[
            "Transcrie fidel acest fișier audio în limba română. "
            "Returnează DOAR textul transcris, fără alte comentarii.",
            types.Part.from_bytes(data=audio_bytes, mime_type=mime),
        ],
    )
    return (resp.text or "").strip()


async def _transcribe(bio):
    from emergentintegrations.llm.openai import OpenAISpeechToText
    stt = OpenAISpeechToText(api_key=KEY)
    return await stt.transcribe(
        file=bio, model="whisper-1", language="ro", response_format="text"
    )


def transcribe(audio_bytes, filename="audio.wav"):
    """Transcribe audio bytes to text (Romanian)."""
    if USE_GROQ:
        return _groq_transcribe(audio_bytes, filename)
    if USE_GEMINI:
        return _gemini_transcribe(audio_bytes, filename)
    bio = BytesIO(audio_bytes)
    bio.name = filename
    resp = asyncio.run(_transcribe(bio))
    if isinstance(resp, str):
        return resp.strip()
    return getattr(resp, "text", str(resp)).strip()
