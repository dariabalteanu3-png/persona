#!/bin/bash
# Pornește serverul Chatterbox TTS în fundal (port 5001, intern)
python tts_server.py &

# Pornește aplicația Streamlit pe portul 7860 (standard HF Spaces)
streamlit run app.py \
    --server.address 0.0.0.0 \
    --server.port 7860 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
