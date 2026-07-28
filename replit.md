# Persona

Persona is a Romanian Streamlit app for creating AI characters, chatting with them,
and optionally generating speech from a user's reference recording.

## Run

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 5000 --server.headless true
```

## Voice generation

Character speech uses **F5-TTS** (open-source, `f5-tts` pip package) running
**directly in the Streamlit process** — no separate server, no external API.

✅ Suportă limba română (fine-tune cdorob/f5-tts-romanian)
✅ Clonare vocală dintr-o mostră audio (10–30 secunde recomandat)
✅ Fără limite comerciale (minute, caractere, credite)
✅ 100% gratuit și open-source (MIT License)

Sunetele ambientale sunt sintetizate local ca preseturi WAV
(ploaie, furtună, ocean, foc, vânt, pădure, cafea, greieri, oraș, tren, etc.)
— fără API extern.

Selecția ambientală automată pe bază de locație: personajele aleg automat
un fundal sonor potrivit contextului conversației (plajă → valuri,
bucătărie → zgomote de gătit, pădure → păsări și frunze, etc.).

Din setările de profil, "Șterge vocile mele" elimină mostrele vocale salvate
și setările de voce ale personajelor, păstrând personajele și conversațiile.

## Architecture

- **Streamlit app** (`app.py`) — port 5000 — UI principal + TTS in-proces
- **voice.py** — F5-TTS voice cloning (PyTorch); sunete ambientale locale + selecție contextuală
- **stt.py** — speech-to-text via Groq/Gemini (separat de TTS)

F5-TTS încarcă modelul la primul apel (~30-60s pe CPU, ~1.0GB download).
Comenzile ulterioare sunt rapide după ce modelul e în memorie.

## Project preferences

- Keep the existing Streamlit/Python structure.
- Prefer free/open-source services for voice generation.
- Keep user-facing text in Romanian.
- The primary user is visually impaired — minimize required manual text input wherever possible.
- F5-TTS rulează in-proces — nu mai există `tts_server.py`.
