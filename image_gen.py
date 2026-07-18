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


def generate_playlist_cover(name, personality, scenario):
    """Generate a small stylized playlist/album cover in the character's style. base64 or None."""
    prompt = (
        f"A square music playlist album cover art inspired by the character \"{name}\" "
        f"(personality: {personality or 'warm and friendly'}; mood/setting: "
        f"{scenario or 'a warm atmospheric vibe'}). Abstract, artistic, evocative cover with a "
        "musical feeling — subtle motifs of soundwaves, vinyl or headphones, rich colors matching "
        "the character's vibe, painterly digital-art style, centered composition. "
        "No text, no words, no letters, no watermark, no border."
    )
    if USE_GEMINI:
        return _gemini_image(prompt)
    if USE_GROQ:
        return _pollinations_image(prompt)
    images = asyncio.run(_gen(prompt))
    if images:
        return images[0]["data"]
    return None
