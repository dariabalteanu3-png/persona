---
title: Persona
emoji: 🎭
colorFrom: pink
colorTo: purple
sdk: docker
pinned: false
---

# Persona — AI Companion App

Aplicație de companie AI cu voci generate de XTTS-v2 Romanian v2 (clonare vocală).

**Caracteristici:**
- Clonare vocală din mostra audio
- Model XTTS-v2 finetuned pentru limba română
- Suport oficial pentru caracterele românești (ș, ț, Ș, Ț)
- Expresivitate emoțională
- Funcții de ambient soundscape
- 100% gratuit, open-source

**Cerințe tehnice:**
- Python 3.11
- GPU recomandat (NVIDIA CUDA) pentru performanță optimă
- Minimum 8GB RAM

**Variabile de mediu necesare (Secrets în HF Spaces):**
- `DATABASE_URL` *(opțional)* — PostgreSQL pentru date persistente (ex: Neon.tech gratuit). Fără acesta, datele se resetează la repornire.
- `OPENAI_API_KEY` *(opțional)* — pentru funcțiile de chat AI
