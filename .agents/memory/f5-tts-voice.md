---
name: F5-TTS voice integration
description: Durable constraints for the free Hugging Face voice-generation path.
---

The app uses the public `mrfakename/E2-F5-TTS` Hugging Face Space through
`gradio_client`. A character's reference audio and exact transcript are persisted
with the character and registered lazily before generation.

**Why:** The project intentionally removed paid voice subscriptions and must keep
voice generation free and keyless. The public Space can queue requests or become
temporarily busy, so callers must surface a retryable error rather than silently
switching providers.

**How to apply:** Keep the Space endpoint inputs in sync with its live Gradio API:
reference audio, reference text, generation text, and the remove-silence flag.
Generated speech is WAV; preserve WAV MIME types in UI playback and downloads.

Ambient sound is separate from voice generation: the app now uses locally
synthesized WAV presets, so ambient playback does not require an external audio
provider. User voice deletion clears only voice fields from owned characters and
preserves characters, conversations, and messages.