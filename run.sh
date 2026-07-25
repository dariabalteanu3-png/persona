#!/bin/bash
# Pornește serverul TTS (XTTS v2) în fundal și aplicația Streamlit
# Rulează pe Oracle Cloud Free Tier (ARM 4CPU, 24GB RAM)

echo "===== Pornesc serverul TTS (XTTS v2) ====="
python tts_server.py &
TTS_PID=$!

echo "Aștept serverul TTS să fie gata (30 secunde)..."
sleep 30

echo "===== Pornesc Streamlit ====="
streamlit run app.py --server.port $PORT --server.address 0.0.0.0 &
STREAMLIT_PID=$!

echo "Aplicația este gata!"
echo "TTS PID: $TTS_PID"
echo "Streamlit PID: $STREAMLIT_PID"

# Așteaptă ambele procese
wait
