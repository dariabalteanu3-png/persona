#!/bin/bash
# Pornește aplicația Streamlit (Chatterbox TTS rulează direct în proces, fără server separat)
streamlit run app.py \
    --server.address 0.0.0.0 \
    --server.port 7860 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
