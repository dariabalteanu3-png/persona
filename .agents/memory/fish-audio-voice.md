---
name: Voice architecture (Fish Audio primary)
description: Generarea vocală live — fish-audio-sdk cu fallback Chatterbox/F5-TTS via HF Spaces; ambient local DSP.
---

# Voice — arhitectura live

## Regulă
Vocea personajelor folosește **Fish Audio** (fish-audio-sdk, clonare zero-shot, model `s2.1-pro-free`) ca metodă **principală**. Dacă Fish Audio e indisponibil sau lipsește cheia, se trece la **Chatterbox** (HF Space) și apoi la **E2-F5-TTS** (HF Space `mrfakename/E2-F5-TTS`) ca rezerve. Nu există motor TTS local PyTorch/F5-TTS în voice.py — requirements.txt nu mai conține torch/f5-tts.

**De ce:** Fish Audio oferă tier gratuit, suport română și clonare zero-shot dintr-o mostră scurtă, fără GPU local. Fallback-urile HF Spaces păstrează funcționalitatea când cota gratuită Fish Audio e epuizată.

## Cum funcționează
- `voice.py` — `_call_primary()` încearcă `fish_audio_available()` (necesită `FISH_AUDIO_API_KEY` sau alias `FISH_API_KEY`), apoi `_fish_generate()` → `_fish_generate_sdk()` (msgpack/streaming) cu fallback REST JSON `/v1/tts`.
- La eșec → `_call_chatterbox_space()` (gradio_client către `ResembleAI/Chatterbox`, token opțional `HF_TOKEN`), apoi spațiile din `FALLBACK_VOICE_SPACES` (implicit `mrfakename/E2-F5-TTS`).
- `text_to_speech(text, voice_id)` și `text_to_speech_from_sample(...)` sunt entrypoint-urile folosite de app.py; returnează bytes WAV (cu reparare header WAV la streaming).
- Sunetele ambientale sunt **preseturi DSP generate local** (`_ambient_wav`, ~414 preseturi, 946+ sunete) — fără API extern pentru ambianță.

## Parametrii importanți
- `FISH_AUDIO_MODEL` (default `s2.1-pro-free`), `FISH_AUDIO_BASE_URL` (default `https://api.fish.audio`)
- `HF_TOKEN` opțional — crește rate-limits pe HF Spaces
- `CHATTERBOX_SPACE`, `FALLBACK_VOICE_SPACES` configurabile

## Fișiere/chei
- `_FISH_API_KEY` se citește din env; NU se pune în cod/GitHub.
- app.py aduce Streamlit Cloud secrets în `os.environ` la pornire.
