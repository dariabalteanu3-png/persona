import os
import re
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from elevenlabs import ElevenLabs, VoiceSettings

load_dotenv(Path(__file__).parent / ".env")

from provider import clean_key

_api = clean_key(os.environ.get("ELEVENLABS_API_KEY"), "sk_")
_client = ElevenLabs(api_key=_api)

# Emoji -> ElevenLabs v3 audio tag / emotion cue
EMOJI_TAGS = {
    "😂": " [laughs] ", "🤣": " [laughs] ", "😹": " [laughs] ", "😆": " [laughs] ",
    "😅": " [nervous laughter] ", "😄": " [happily] ", "😁": " [happily] ",
    "😉": " [playfully] ", "😊": " [warmly] ", "🥰": " [warmly] ", "😍": " [excited] ",
    "😢": " [sadly] ", "😭": " [crying] ", "😔": " [sighs] ", "😞": " [sighs] ",
    "😮": " [gasps] ", "😲": " [gasps] ", "😱": " [gasps] ", "🤯": " [shocked] ",
    "😡": " [angrily] ", "😠": " [angrily] ", "🤬": " [angrily] ",
    "🤫": " [whispers] ", "🤭": " [giggles] ", "😏": " [smugly] ", "😌": " [calmly] ",
    "🥺": " [pleading] ", "😴": " [yawns] ", "😨": " [fearfully] ", "😰": " [nervously] ",
    "🔥": " [excited] ", "🎉": " [excited] ", "🤔": " [thoughtfully] ", "😳": " [embarrassed] ",
    "❤️": " [warmly] ", "💔": " [sadly] ", "😜": " [playfully] ", "😝": " [playfully] ",
}

# Keyword -> tag for *stage directions* like *râde* / *șoptește*
_ACTION_MAP = [
    (("râd", "rad", "hah", "haha", "chicot", "laugh", "giggl"), "[laughs]"),
    (("oftea", "suspin", "sigh"), "[sighs]"),
    (("șopt", "sopt", "whisper", "murmur"), "[whispers]"),
    (("țip", "tip", "strig", "url", "scream", "shout", "răcnesc"), "[shouts]"),
    (("plâng", "plang", "cry", "lăcrim", "lacrim"), "[crying]"),
    (("gâfâ", "gafa", "gasp", "icnesc"), "[gasps]"),
    (("mormă", "morma", "mutter"), "[mutters]"),
]

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\U00002190-\U000021FF\uFE0F\u2764]"
)


def _map_action(word):
    w = word.lower()
    for keys, tag in _ACTION_MAP:
        if any(k in w for k in keys):
            return " " + tag + " "
    return " "  # drop non-emotional stage directions from speech


def _is_emotional(word):
    w = word.lower()
    return any(k in w for keys, _tag in _ACTION_MAP for k in keys)


def extract_actions(text):
    """Return physical (non-vocal) *stage actions* that should become sound effects."""
    out = []
    for a in re.findall(r"\*([^*]+)\*", text or ""):
        a = a.strip()
        if a and not _is_emotional(a):
            out.append(a)
    return out


def expressify(text):
    """Turn a chat reply into expressive ElevenLabs v3 text with audio tags."""
    # *stage directions* -> tag (or removed)
    text = re.sub(r"\*([^*]+)\*", lambda m: _map_action(m.group(1)), text)
    # emojis -> tags
    for emo, tag in EMOJI_TAGS.items():
        text = text.replace(emo, tag)
    # remove any leftover emoji so they are not read literally
    text = _EMOJI_RE.sub("", text)
    # ALL-CAPS word(s) -> shouting
    text = re.sub(r"\b([A-ZĂÂÎȘȚ]{3,}(?:\s+[A-ZĂÂÎȘȚ]{2,})*)\b",
                  lambda m: " [shouts] " + m.group(0), text, count=1)
    # strong exclamation -> excited
    if "!!" in text or "!?" in text:
        text = "[excited] " + text
    text = re.sub(r"!{2,}", "!", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or "..."


def sound_effect(prompt, duration=6.0):
    """Generate an ambient sound effect (mp3 bytes) from a text description."""
    audio = _client.text_to_sound_effects.convert(
        text=prompt, duration_seconds=float(duration)
    )
    data = b""
    for chunk in audio:
        if chunk:
            data += chunk
    return data


def list_voices():
    """Return list of (voice_id, name) for available ElevenLabs voices."""
    resp = _client.voices.get_all()
    return [(v.voice_id, v.name) for v in resp.voices]


def clone_voice(name, file_bytes, filename, description=""):
    """Create an Instant Voice Clone from an uploaded audio sample."""
    bio = BytesIO(file_bytes)
    bio.name = filename or "sample.mp3"
    voice = _client.voices.ivc.create(
        name=name,
        files=[bio],
        description=description or None,
    )
    return voice.voice_id


TONE_TAGS = {
    "Șoaptă": "[whispers] ",
    "Voce de somn": "[whispering softly] ",
    "Voce veselă": "[cheerfully] ",
    "Voce blândă": "[gently] ",
}


def text_to_speech(text, voice_id, stability=0.5, similarity_boost=0.75, style=0.0, expressive=True, tone=None):
    """Generate expressive speech (mp3) using ElevenLabs v3 audio tags."""
    spoken = expressify(text) if expressive else text
    tag = TONE_TAGS.get(tone or "")
    if tag:
        spoken = tag + spoken
        if tone in ("Șoaptă", "Voce de somn", "Voce blândă"):
            stability = max(float(stability), 0.7)
    audio = _client.text_to_speech.convert(
        text=spoken,
        voice_id=voice_id,
        model_id="eleven_v3",
        voice_settings=VoiceSettings(
            stability=float(stability),
            similarity_boost=float(similarity_boost),
        ),
    )
    data = b""
    for chunk in audio:
        if chunk:
            data += chunk
    return data

