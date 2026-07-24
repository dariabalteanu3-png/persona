---
name: Chatterbox TTS architecture
description: Sistemul vocal Chatterbox — cum rulează, ce fișiere sunt implicate, ce nu trebuie schimbat.
---

# Chatterbox TTS — arhitectură

## Regulă
Vocea personajelor folosește Chatterbox TTS (open-source, `chatterbox-tts`) ca server local FastAPI pe portul 5001 (`tts_server.py`). Nu folosiți HF Spaces, F5-TTS, ElevenLabs, sau orice alt serviciu extern pentru TTS.

**Why:** Utilizatoarea a cerut explicit eliminarea HF Spaces pentru TTS (ZeroGPU, F5-TTS) și înlocuirea cu o soluție fără limite comerciale. Chatterbox nu necesită text de referință — clonează vocea direct din mostră audio.

## How to apply
- `tts_server.py` — serverul FastAPI; trebuie să ruleze ca workflow separat pe portul 5001
- `voice.py` — clientul HTTP care apelează serverul; conține și tot DSP-ul ambient (neatins)
- Modelul se descarcă din HuggingFace la primul apel TTS (~2GB, o singură dată, se cachează în `~/.cache/huggingface`)
- Prima generare durează 1-2 minute (descărcare model); cele ulterioare sunt rapide
- `voice_ref_text` din datele personajelor este ignorat (păstrat în DB pentru compatibilitate)
- Biblioteca de sunete ambientale (_ambient_wav, sound_effect) este în voice.py și nu a fost modificată

## Workflow-uri necesare
1. `Chatterbox TTS Server` — `python tts_server.py` — port 5001 (console)
2. `Start application` — `streamlit run app.py ...` — port 5000 (webview)

## Parametri Chatterbox
- `exaggeration` (0-1): intensitate emoțională — mapat din `voice_style`
- `cfg_weight` (0-1): fidelitate față de vocea de referință — mapat din `voice_similarity`
- `stability` din ElevenLabs nu are echivalent direct în Chatterbox
