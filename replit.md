# Persona

Persona is a Romanian Streamlit app for creating AI characters, chatting with them,
and optionally generating speech from a user's reference recording.

## Run

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 5000 --server.headless true
```

## Voice generation

Character speech uses **Chatterbox TTS** (open-source, `chatterbox-tts` pip package) running
**directly in the Streamlit process** — no separate server, no external API.

✅ Suportă limba română
✅ Clonare vocală dintr-o mostră audio (10–30 secunde recomandat)
✅ Fără text de referință — Chatterbox clonează vocea direct din mostră
✅ Fără limite comerciale (minute, caractere, credite)
✅ 100% gratuit și open-source

Sunetele ambientale sunt sintetizate local ca preseturi WAV
(ploaie, furtună, ocean, foc, vânt, pădure, cafea, greieri, oraș, tren, etc.)
— fără API extern.

Din setările de profil, "Șterge vocile mele" elimină mostrele vocale salvate
și setările de voce ale personajelor, păstrând personajele și conversațiile.

## Architecture

- **Streamlit app** (`app.py`) — port 5000 — UI principal + TTS in-proces
- **voice.py** — Chatterbox TTS inline (fără server HTTP); sunete ambientale locale
- **stt.py** — speech-to-text via Groq/Gemini (separat de TTS)

Chatterbox TTS încarcă modelul la primul apel (~1-2 minute, ~2GB download).
Comenzile ulterioare sunt rapide după ce modelul e în memorie.

## Project preferences

- Keep the existing Streamlit/Python structure.
- Prefer free/open-source services for voice generation.
- Keep user-facing text in Romanian.
- The primary user is visually impaired — minimize required manual text input wherever possible.
- Chatterbox TTS rulează in-proces — nu mai există `tts_server.py`.
