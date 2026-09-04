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
CEREBRAS_API_KEY = clean_key(os.environ.get("CEREBRAS_API_KEY"), "csk_")
OPENROUTER_API_KEY = clean_key(os.environ.get("OPENROUTER_API_KEY"), "sk-or-")
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

# Provider availability (all can be true simultaneously for fallback)
HAS_GEMINI = bool(GEMINI_API_KEY)
HAS_GROQ = bool(GROQ_API_KEY)
HAS_CEREBRAS = bool(CEREBRAS_API_KEY)
HAS_OPENROUTER = bool(OPENROUTER_API_KEY)
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

GROQ_TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "openai/gpt-oss-120b")
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")

# Cerebras — fallback automat când Groq e indisponibil (API compatibil OpenAI)
# NOTE: gpt-oss-120b e modelul actual; llama-3.3-70b a fost retras de Cerebras
CEREBRAS_TEXT_MODEL = os.environ.get("CEREBRAS_TEXT_MODEL", "gpt-oss-120b")
CEREBRAS_BASE_URL = os.environ.get("CEREBRAS_BASE_URL", "https://api.cerebras.ai/v1")

# OpenRouter — al doilea fallback (agregator multi-modele, API compatibil OpenAI)
# NOTE: deepseek-chat-v3-0324:free a devenit pay-only; nemotron e gratuit și performant
OPENROUTER_TEXT_MODEL = os.environ.get("OPENROUTER_TEXT_MODEL", "nvidia/nemotron-3-super-120b-a12b:free")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# Timeout (sec) pentru apelurile de chat — evită blocarea la timeout-uri lungi
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "90"))

_gemini = None
_groq = None
_cerebras = None
_openrouter = None


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
        _groq = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1", timeout=LLM_TIMEOUT)
    return _groq


def cerebras_client():
    """Client OpenAI-compatibil pentru Cerebras (fallback #1 după Groq)."""
    global _cerebras
    if _cerebras is None:
        from openai import OpenAI
        _cerebras = OpenAI(api_key=CEREBRAS_API_KEY, base_url=CEREBRAS_BASE_URL, timeout=LLM_TIMEOUT)
    return _cerebras


def openrouter_client():
    """Client OpenAI-compatibil pentru OpenRouter (fallback #2 după Cerebras)."""
    global _openrouter
    if _openrouter is None:
        from openai import OpenAI
        _openrouter = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL, timeout=LLM_TIMEOUT)
    return _openrouter
