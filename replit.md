# Persona

Persona is a Romanian Streamlit app for creating AI characters, chatting with them,
and optionally generating speech from a user's reference recording.

## Run

The app runs through the `Start application` workflow:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 5000 --server.headless true
```

## Voice generation

Character speech uses **Chatterbox TTS** (open-source, `chatterbox-tts` pip package) running
as a local FastAPI service on port 5001 (`tts_server.py`). No ElevenLabs, Hugging Face, or any
paid provider is used. No commercial limits (no characters/month, no credits).

**No reference text needed** — Chatterbox clones a voice directly from a short audio sample
(10–30 seconds recommended). Just upload the recording and Chatterbox does the rest.

Background ambience is generated locally as WAV presets inside the app
(rain, storm, ocean, forest, fire, café, wind, crickets, city, snow, room,
countryside, river, and train). Selecting and playing these sounds does not call
any external API.

From the profile settings, "Șterge vocile mele" removes the saved reference
samples and voice settings from the user's characters while preserving the
characters, conversations, and messages. The action requires an explicit
confirmation phrase.

## Architecture

- **Streamlit app** (`app.py`) — port 5000 — main UI
- **Chatterbox TTS server** (`tts_server.py`) — port 5001 — local FastAPI service
  - POST `/register` — saves voice sample to `/tmp/persona_voices/`
  - POST `/tts` — generates WAV from text + voice_id
  - POST `/preview` — generates WAV from raw bytes (before saving a character)
  - GET `/health` — service status
- **voice.py** — calls the TTS server via HTTP (localhost:5001); ambient DSP unchanged
- **stt.py** — speech-to-text via Groq/Gemini (separate from TTS)

## Run

The app runs through two parallel workflows:
```bash
# Workflow 1: Chatterbox TTS Server
python tts_server.py

# Workflow 2: Streamlit app
streamlit run app.py --server.address 0.0.0.0 --server.port 5000 --server.headless true
```

The TTS server loads the Chatterbox model on first use (~1-2 minutes, ~2GB download).
Subsequent calls are fast once the model is in memory.

## Project preferences

- Keep the existing Streamlit/Python structure.
- Prefer free/open-source services for voice generation.
- Keep user-facing text in Romanian.
- The primary user is visually impaired — minimize required manual text input wherever possible.
- Chatterbox TTS is the active voice backend — do not revert to F5-TTS or HF Spaces for TTS.