"""Free voice generation through the public Hugging Face F5-TTS Space.

The Space is intentionally accessed lazily so the app can still start when the
optional Gradio client has not been installed yet (for example during a local
syntax check).  No paid provider or API key is used here.
"""

import base64
import os
import re
import tempfile
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

F5_TTS_SPACE = os.environ.get("F5_TTS_SPACE", "mrfakename/E2-F5-TTS")
_client = None
_handle_file = None
_voice_samples = {}


class VoiceGenerationError(RuntimeError):
    """A user-facing error from the free F5-TTS service."""


def _decode_sample(sample_b64):
    if not sample_b64:
        return None
    if sample_b64.startswith("data:"):
        sample_b64 = sample_b64.split(",", 1)[-1]
    try:
        return base64.b64decode(sample_b64)
    except (ValueError, TypeError) as exc:
        raise VoiceGenerationError("Mostra audio salvată invalidă.") from exc


def voice_id_for_sample(sample_bytes):
    """Return a stable local identifier for a reference sample."""
    import hashlib

    if not sample_bytes:
        return None
    return "f5tts:" + hashlib.sha256(sample_bytes).hexdigest()[:24]


def register_voice(voice_id, sample_b64, reference_text, sample_name="reference.wav"):
    """Make a character's persisted reference sample available to the TTS call."""
    if not voice_id or not sample_b64:
        return
    sample = _decode_sample(sample_b64)
    if sample and reference_text:
        suffix = Path(str(sample_name or "reference.wav")).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
            suffix = ".wav"
        _voice_samples[voice_id] = (sample, str(reference_text).strip(), suffix)


def register_character_voice(character):
    register_voice(
        character.get("voice_id"),
        character.get("voice_sample_b64"),
        character.get("voice_ref_text"),
        character.get("voice_sample_name", "reference.wav"),
    )


def _get_client():
    global _client, _handle_file
    if _client is not None:
        return _client, _handle_file
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise VoiceGenerationError(
            "Lipsește pachetul gradio_client. Repornește aplicația după instalarea "
            "dependențelor din requirements.txt."
        ) from exc
    try:
        _client = Client(F5_TTS_SPACE)
        _handle_file = handle_file
    except Exception as exc:  # noqa: broad-except
        raise VoiceGenerationError(
            "Nu m-am putut conecta la spațiul public F5-TTS. Încearcă din nou."
        ) from exc
    return _client, _handle_file


def _read_generated_audio(result):
    """Normalize Gradio's file output to WAV bytes."""
    if isinstance(result, (list, tuple)):
        result = result[0] if result else None
    if isinstance(result, dict):
        result = result.get("path") or result.get("url")
    if result is None:
        raise VoiceGenerationError("F5-TTS nu a returnat un fișier audio.")
    if isinstance(result, bytes):
        return result
    if hasattr(result, "path"):
        result = result.path
    result = str(result)
    if result.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(result, timeout=60) as response:
                return response.read()
        except Exception as exc:  # noqa: broad-except
            raise VoiceGenerationError("Nu am putut descărca WAV-ul generat.") from exc
    try:
        with open(result, "rb") as audio_file:
            data = audio_file.read()
    except OSError as exc:
        raise VoiceGenerationError("F5-TTS a returnat o cale audio invalidă.") from exc
    if not data:
        raise VoiceGenerationError("F5-TTS a returnat un fișier audio gol.")
    return data


def _generate(text, sample_bytes, reference_text, sample_name="reference.wav"):
    if not sample_bytes:
        raise VoiceGenerationError(
            "Personajul nu are o mostră audio. Încarcă o mostră pentru a-i activa vocea."
        )
    if not reference_text or not str(reference_text).strip():
        raise VoiceGenerationError(
            "Completează textul exact rostit în mostra audio pentru F5-TTS."
        )

    client, handle_file = _get_client()
    suffix = Path(str(sample_name or "reference.wav")).suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac"}:
        suffix = ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as reference_file:
        reference_file.write(sample_bytes)
        reference_file.flush()
        try:
            result = client.predict(
                handle_file(reference_file.name),
                str(reference_text).strip(),
                str(text).strip() or "...",
                False,
                api_name="/predict",
            )
        except Exception as exc:  # noqa: broad-except
            detail = str(exc).lower()
            if "queue" in detail or "timeout" in detail or "busy" in detail:
                raise VoiceGenerationError(
                    "Spațiul public F5-TTS este ocupat momentan. Încearcă din nou peste puțin."
                ) from exc
            raise VoiceGenerationError(
                "Generarea F5-TTS a eșuat. Verifică mostra și textul de referință."
            ) from exc
    return _read_generated_audio(result)


# Emoji -> a clean speech string. F5-TTS does not support ElevenLabs-style tags.
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
    """Return physical (non-vocal) stage actions for the optional ambient layer."""
    return [
        action.strip()
        for action in re.findall(r"\*([^*]+)\*", text or "")
        if action.strip() and not _is_emotional(action)
    ]


def expressify(text):
    """Remove markup and emoji that F5-TTS would otherwise read literally."""
    text = re.sub(r"\*([^*]+)\*", " ", text or "")
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or "..."


def sound_effect(prompt, duration=6.0, prompt_influence=0.45):
    """Ambient generation is not part of F5-TTS and no longer uses a paid API."""
    raise VoiceGenerationError(
        "Efectele ambientale generate nu sunt disponibile în modul gratuit F5-TTS."
    )


def text_to_speech(
    text,
    voice_id,
    stability=0.5,
    similarity_boost=0.75,
    style=0.0,
    expressive=True,
    tone=None,
):
    """Generate a WAV response with the character's persisted reference voice."""
    sample = _voice_samples.get(voice_id)
    if not sample:
        raise VoiceGenerationError(
            "Vocea acestui personaj nu are o mostră F5-TTS salvată. Editează personajul "
            "și încarcă din nou mostra audio."
        )
    spoken = expressify(text) if expressive else (text or "...")
    return _generate(spoken, sample[0], sample[1], sample[2])


def text_to_speech_from_sample(text, sample_bytes, reference_text, sample_name="reference.wav"):
    """Generate a preview directly from an uploaded sample before saving it."""
    return _generate(expressify(text), sample_bytes, reference_text, sample_name)