"""Free voice generation through the public Hugging Face F5-TTS Space.

The Space is intentionally accessed lazily so the app can still start when the
optional Gradio client has not been installed yet (for example during a local
syntax check).  No paid provider or API key is used here.
"""

import base64
import hashlib
import io
import math
import os
import random
import re
import tempfile
import urllib.request
import wave
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

F5_TTS_SPACE = os.environ.get("F5_TTS_SPACE", "mrfakename/E2-F5-TTS")
_WHISPER_SPACE = os.environ.get("WHISPER_SPACE", "openai/whisper")
_client = None
_handle_file = None
_voice_samples = {}
_whisper_client = None


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


def transcribe_sample(audio_bytes, filename="audio.wav", language="ro"):
    """Transcribe an audio sample using the STT module (Groq/Gemini) if available,
    otherwise fall back to a free public Whisper Gradio Space on Hugging Face.
    Returns the transcribed text or raises VoiceGenerationError on failure."""
    global _whisper_client
    # Try existing STT providers first (Groq / Gemini) — they're faster
    try:
        import stt as _stt
        from provider import USE_GROQ, USE_GEMINI
        if USE_GROQ or USE_GEMINI:
            return _stt.transcribe(audio_bytes, filename)
    except Exception:
        pass
    # Fallback: free public Whisper space via gradio_client
    try:
        from gradio_client import Client, handle_file
    except ImportError as exc:
        raise VoiceGenerationError("gradio_client lipsește.") from exc
    try:
        if _whisper_client is None:
            _whisper_client = Client(_WHISPER_SPACE)
        suffix = Path(str(filename or "audio.wav")).suffix.lower()
        if suffix not in {".wav", ".mp3", ".m4a", ".ogg", ".flac", ".webm"}:
            suffix = ".wav"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        try:
            # openai/whisper space: predict(audio, task) → text string
            result = _whisper_client.predict(
                handle_file(tmp_path),
                "transcribe",          # task: 'transcribe' (nu 'translate')
                api_name="/predict",
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        if isinstance(result, dict):
            text = result.get("text") or result.get("output") or ""
        elif isinstance(result, (list, tuple)):
            text = str(result[0]) if result else ""
        else:
            text = str(result or "")
        text = text.strip()
        if not text:
            raise VoiceGenerationError("Transcrierea nu a returnat text.")
        return text
    except VoiceGenerationError:
        raise
    except Exception as exc:
        raise VoiceGenerationError(
            f"Nu am putut transcrie automat mostra: {exc}"
        ) from exc


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
                True,   # remove_silence → voce mai curată, fără pauze lungi la început/sfârșit
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


def _ambient_wav(preset, duration=12.0, sample_rate=16000):
    """Create a small mono WAV ambience locally, with no API or subscription."""
    duration = max(2.0, min(float(duration), 30.0))
    count = int(sample_rate * duration)
    rng = random.Random(hashlib.sha256(preset.encode("utf-8")).digest())
    previous = 0.0
    samples = []
    for index in range(count):
        t = index / sample_rate
        noise = rng.uniform(-1.0, 1.0)
        previous = previous * 0.985 + noise * 0.015
        if preset == "rain":
            value = noise * 0.14 + previous * 0.42
            if rng.random() < 0.00035:
                value += rng.uniform(-0.8, 0.8)
        elif preset == "storm":
            value = noise * 0.18 + previous * 0.44
            if rng.random() < 0.00065:
                value += rng.uniform(-0.95, 0.95)
        elif preset == "ocean":
            value = (math.sin(2 * math.pi * 0.09 * t) ** 8) * 0.32
            value += previous * 0.28
        elif preset == "fire":
            value = previous * 0.34
            if rng.random() < 0.0012:
                value += rng.uniform(-0.8, 0.8)
        elif preset == "wind":
            value = previous * 0.82 + math.sin(2 * math.pi * 0.05 * t) * 0.12
        elif preset == "crickets":
            chirp = max(0.0, math.sin(2 * math.pi * 4.2 * t)) ** 18
            value = chirp * math.sin(2 * math.pi * 2400 * t) * 0.22 + previous * 0.12
        elif preset == "river":
            value = previous * 0.3 + math.sin(2 * math.pi * 0.18 * t) * 0.18
        elif preset == "train":
            value = math.sin(2 * math.pi * 3.2 * t) * 0.16 + previous * 0.3
        elif preset == "forest":
            # foliaj ușor + vânt blând + ocazional o pasăre
            value = previous * 0.62 + noise * 0.06
            value += math.sin(2 * math.pi * 0.07 * t) * 0.08
            if rng.random() < 0.00018:
                value += math.sin(2 * math.pi * rng.uniform(1800, 3400) * t) * rng.uniform(0.1, 0.28)
        elif preset == "cafe":
            # murmur de fond + ocazional zgomot de ceașcă / mașinărie de cafea
            value = previous * 0.55 + noise * 0.04
            if rng.random() < 0.00030:
                value += rng.uniform(-0.45, 0.45)
            value += math.sin(2 * math.pi * 0.11 * t) * 0.04
        elif preset == "city":
            # trafic continuu + bocănituri ocazionale de motor / claxon scurt
            value = previous * 0.50 + noise * 0.08
            value += math.sin(2 * math.pi * 0.04 * t) * 0.10
            if rng.random() < 0.00055:
                value += rng.uniform(-0.65, 0.65)
        elif preset == "countryside":
            # vânt fin + păsări dimineață + greierei ocazionali
            value = previous * 0.70 + noise * 0.03
            value += math.sin(2 * math.pi * 0.06 * t) * 0.06
            chirp2 = max(0.0, math.sin(2 * math.pi * 2.8 * t)) ** 22
            value += chirp2 * math.sin(2 * math.pi * 3200 * t) * 0.10
            if rng.random() < 0.00012:
                value += math.sin(2 * math.pi * rng.uniform(2000, 4000) * t) * rng.uniform(0.08, 0.18)
        elif preset == "snow":
            # vânt arctic foarte lin + tăcere cu pulsații rare
            value = previous * 0.88 + math.sin(2 * math.pi * 0.03 * t) * 0.05
            if rng.random() < 0.00012:
                value += rng.uniform(-0.25, 0.25)
        else:
            value = previous * 0.25
            if rng.random() < 0.00025:
                value += rng.uniform(-0.55, 0.55)
        samples.append(value)

    peak = max((abs(value) for value in samples), default=1.0) or 1.0
    pcm = b"".join(
        int(max(-1.0, min(1.0, value / peak)) * 32767).to_bytes(
            2, "little", signed=True
        )
        for value in samples
    )
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def sound_effect(prompt, duration=6.0, prompt_influence=0.45):
    """Return a locally synthesized ambience; no paid provider is contacted."""
    text = str(prompt or "").lower()
    presets = (
        ("storm", ("tunet", "furtun", "thunder", "storm")),
        ("rain", ("ploaie", "rain")),
        ("ocean", ("mare", "val", "ocean", "wave")),
        ("fire", ("foc", "campfire", "fire", "șemineu")),
        ("wind", ("vânt", "wind", "breeze")),
        ("crickets", ("greier", "cricket", "noapte")),
        ("river", ("râu", "river", "apă")),
        ("train", ("tren", "train")),
        ("forest", ("pădure", "forest", "frunze")),
        ("cafe", ("cafenea", "cafe", "coffee")),
        ("city", ("oraș", "city", "trafic")),
        ("countryside", ("țară", "sat", "countryside", "fermă")),
        ("snow", ("ninso", "zăpad", "snow")),
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)


def forget_registered_voices(voice_ids=None):
    """Forget persisted voice samples from this process after user deletion."""
    if voice_ids is None:
        _voice_samples.clear()
        return
    for voice_id in voice_ids:
        _voice_samples.pop(voice_id, None)


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