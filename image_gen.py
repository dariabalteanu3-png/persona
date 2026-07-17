import os
import asyncio
import base64
import urllib.parse
from pathlib import Path

import requests
from dotenv import load_dotenv

from provider import USE_GEMINI, USE_GROQ, GEMINI_IMAGE_MODEL, gemini_client

load_dotenv(Path(__file__).parent / ".env")

KEY = os.environ.get("EMERGENT_LLM_KEY")


async def _gen(prompt):
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    chat = LlmChat(
        api_key=KEY,
        session_id="avatar-gen",
        system_message="You generate expressive character portrait images.",
    )
    chat.with_model("gemini", "gemini-3.1-flash-image-preview").with_params(
        modalities=["image", "text"]
    )
    _text, images = await chat.send_message_multimodal_response(UserMessage(text=prompt))
    return images


def _gemini_image(prompt):
    from google.genai import types
    resp = gemini_client().models.generate_content(
        model=GEMINI_IMAGE_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    cands = resp.candidates or []
    if not cands:
        return None
    for part in (cands[0].content.parts or []):
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            return base64.b64encode(inline.data).decode()
    return None


def _pollinations_image(prompt):
    """Free, keyless image generation via Pollinations.ai. Returns base64 or None."""
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(prompt)
        + "?width=768&height=768&nologo=true"
    )
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    if not r.content:
        return None
    return base64.b64encode(r.content).decode()


def generate_avatar(name, personality, scenario):
    """Generate a stylized character portrait. Returns a base64 PNG/JPEG string or None."""
    prompt = (
        f"A stylized, high-quality character portrait of \"{name}\". "
        f"Personality: {personality or 'friendly and curious'}. "
        f"Setting / scenario: {scenario or 'a neutral atmospheric backdrop'}. "
        "Head-and-shoulders portrait, expressive face, cinematic dramatic lighting, "
        "painterly digital-art style, centered composition, richly detailed, "
        "atmospheric background matching the setting. No text, no watermark, no border."
    )
    if USE_GEMINI:
        return _gemini_image(prompt)
    if USE_GROQ:
        return _pollinations_image(prompt)
    images = asyncio.run(_gen(prompt))
    if images:
        return images[0]["data"]  # base64 string
    return None
