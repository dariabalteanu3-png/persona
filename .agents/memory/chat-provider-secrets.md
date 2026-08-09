---
name: Chat provider secrets
description: Chatul text și generarea vocală folosesc configurații separate.
---

Chatul text are nevoie de cel puțin un provider LLM configurat prin `GROQ_API_KEY`, `GEMINI_API_KEY` sau `EMERGENT_LLM_KEY`; `FISH_AUDIO_API_KEY` este doar pentru voce.

**Why:** O cheie Fish Audio validă nu poate genera răspunsurile text ale conversației, iar lipsa unui provider LLM produce ecranul generic de retry.

**How to apply:** La depanarea chatului, verifică mai întâi providerul LLM și păstrează cheia vocală ca o configurație separată.