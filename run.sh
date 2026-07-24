#!/bin/bash
# Pornește serverul TTS în fundal și aplicația Streamlit

echo "Pornesc serverul TTS (XTTS v2)..."
python tts_server.py &
TTS_PID=$!

echo "Aștept serverul TTS să fie gata..."
sleep 10

echo "Pornesc Streamlit..."
streamlit run app.py --server.port $PORT --server.address 0.0.0.0

# La oprire, oprim și serverul TTS
kill $TTS_PID 2>/dev/null
