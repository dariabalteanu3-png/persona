#!/bin/bash
# Pornește aplicația Streamlit cu Python 3.11 (XTTS-v2)

# Activează virtual environment cu Python 3.11
if [ -d ".venv" ]; then
    source .venv/bin/activate
else
    # Creează virtual environment dacă nu există
    uv venv --python 3.11
    source .venv/bin/activate
    uv pip install -r requirements.txt
fi

# Pornește aplicația Streamlit
streamlit run app.py \
    --server.address 0.0.0.0 \
    --server.port 7860 \
    --server.headless true \
    --server.enableCORS false \
    --server.enableXsrfProtection false
