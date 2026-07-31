---
name: Chatterbox TTS architecture
description: Sistemul vocal Chatterbox — arhitectură in-proces, fără server separat.
---

# Chatterbox TTS — arhitectură

## Regulă
Vocea personajelor folosește **Chatterbox TTS** (open-source, `chatterbox-tts`) care rulează **direct în procesul Streamlit** (`voice.py`). Nu există server FastAPI separat, nu se folosește HF Spaces, F5-TTS, ElevenLabs, sau orice serviciu extern pentru TTS.

**De ce:** Utilizatoarea a cerut eliminarea HF Spaces și a soluțiilor comerciale. Chatterbox nu necesită text de referință — clonează vocea direct din mostră audio. Noua arhitectură e mai simplă (fără server separat pe port 5001).

## Cum funcționează
- `voice.py` — importă `ChatterboxTTS` din `chatterbox.tts` și îl rulează direct
- `_load_model()` — apelează `ChatterboxTTS.from_pretrained(device="cpu")` — prima dată descarcă modelul (~2GB)
- `text_to_speech()` — generează WAV direct, fără HTTP calls
- `sound_effect()` — sintetizează sunete ambientale local cu numpy
- Modelul se descarcă din HuggingFace la primul apel TTS (~2GB, o singură dată, se cachează în `~/.cache/huggingface`)
- Prima generare durează 1-2 minute (descărcare model); cele ulterioare sunt rapide

## Fișiere eliminate
- `tts_server.py` — a fost eliminat; Chatterbox TTS rulează acum in-proces
- `start.sh` — actualizat să nu mai pornească tts_server.py
- `.replit` — actualizat, workflow-ul "Chatterbox TTS Server" eliminat

## Parametri Chatterbox
- `exaggeration` (0-1): intensitate emoțională — mapat din `voice_style`
- `cfg_weight` (0-1): fidelitate față de vocea de referință — mapat din `voice_similarity`
