import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
EMERGENT_LLM_KEY = os.environ.get("EMERGENT_LLM_KEY")

# Provider selection (highest priority first):
#   - GEMINI_API_KEY   -> Google Gemini (google-genai SDK)
#   - GROQ_API_KEY     -> Groq (OpenAI-compatible)
#   - EMERGENT_LLM_KEY -> Emergent integrations (Emergent preview)
#   - none of the above (external free hosting, no keys) -> Pollinations (KEYLESS)
USE_GEMINI = bool(GEMINI_API_KEY)
USE_GROQ = bool(GROQ_API_KEY) and not USE_GEMINI
USE_EMERGENT = bool(EMERGENT_LLM_KEY) and not USE_GEMINI and not USE_GROQ
USE_POLLINATIONS = not (USE_GEMINI or USE_GROQ or USE_EMERGENT)

GEMINI_TEXT_MODEL = os.environ.get("GEMINI_TEXT_MODEL", "gemini-2.5-flash")
GEMINI_IMAGE_MODEL = os.environ.get("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

GROQ_TEXT_MODEL = os.environ.get("GROQ_TEXT_MODEL", "llama-3.3-70b-versatile")
GROQ_VISION_MODEL = os.environ.get("GROQ_VISION_MODEL", "qwen/qwen3.6-27b")
GROQ_STT_MODEL = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")

POLLINATIONS_TEXT_MODEL = os.environ.get("POLLINATIONS_TEXT_MODEL", "openai")

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
