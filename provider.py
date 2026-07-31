import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def clean_key(raw, prefix=None):
    """Repair a possibly-corrupted secret where two keys got glued together with a
    quote or whitespace (e.g. 'gsk_AAA"gsk_BBB'). Returns the first well-formed token,
    preferring one that starts with `prefix`. Safe no-op for a single clean key."""
    if not raw:
        return raw
    parts = [p for p in re.split(r'["\'\s]+', raw.strip()) if p]
    if not parts:
        return raw.strip()
    if prefix:
        for p in parts:
            if p.startswith(prefix):
                return p
    return parts[0]


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = clean_key(os.environ.get("GROQ_API_KEY"), "gsk_")
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

# Provider availability (both can be true simultaneously for fallback)
HAS_GEMINI = bool(GEMINI_API_KEY)
HAS_GROQ = bool(GROQ_API_KEY)
HAS_EMERGENT = bool(EMERGENT_LLM_KEY)

# Primary provider selection (highest priority first):
#   - GROQ_API_KEY     -> Groq (fast, OpenAI-compatible) — preferred primary
#   - GEMINI_API_KEY   -> Google Gemini (smart, good fallback)
#   - EMERGENT_LLM_KEY -> Emergent integrations
USE_GROQ = HAS_GROQ
USE_GEMINI = HAS_GEMINI and not USE_GROQ
USE_EMERGENT = HAS_EMERGENT and not USE_GROQ and not USE_GEMINI

GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

GROQ_TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")

_gemini = None
_groq = None


def gemini_client():
    global _gemini
    if _gemini is None:
        from google import genai
        _gemini = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini


def groq_client():
    global _groq
    if _groq is None:
        from openai import OpenAI
        _groq = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    return _groq
