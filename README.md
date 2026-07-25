---
title: Persona
emoji: 🎭
colorFrom: pink
colorTo: purple
sdk: docker
pinned: false
---

# Persona — AI Companion App

Aplicație de companie AI cu voci generate de Chatterbox TTS.

**Variabile de mediu necesare (Secrets în HF Spaces):**
- `DATABASE_URL` *(opțional)* — PostgreSQL pentru date persistente (ex: Neon.tech gratuit). Fără acesta, datele se resetează la repornire.
- `OPENAI_API_KEY` *(opțional)* — pentru funcțiile de chat AI
