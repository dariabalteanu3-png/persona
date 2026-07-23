# Persona

Persona is a Romanian Streamlit app for creating AI characters, chatting with them,
and optionally generating speech from a user's reference recording.

## Run

The app runs through the `Start application` workflow:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 5000 --server.headless true
```

## Voice generation

Character speech uses the public Hugging Face `mrfakename/E2-F5-TTS` Space through
`gradio_client`. No ElevenLabs or Replicate subscription/API key is required.
When creating a character, upload a short reference recording and enter the exact
words spoken in it. Generated speech is returned and downloaded as WAV.

Background ambience is now generated locally as WAV presets inside the app
(rain, storm, ocean, forest, fire, café, wind, crickets, city, snow, room,
countryside, river, and train). Selecting and playing these sounds does not call
an external audio provider or require a subscription.

From the profile settings, “Șterge vocile mele” removes the saved reference
samples and voice settings from the user's characters while preserving the
characters, conversations, and messages. The action requires an explicit
confirmation phrase.

## Project preferences

- Keep the existing Streamlit/Python structure.
- Prefer free/open-source services for voice generation.
- Keep user-facing text in Romanian.