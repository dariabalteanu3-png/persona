FROM python:3.11-slim

WORKDIR /app

# Dependențe sistem pentru audio
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Instalare dependențe de bază
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instalare TTS (Coqui) pentru XTTS-v2 Romanian v2
RUN pip install --no-cache-dir TTS==0.22.0

# Descărcare model XTTS-v2 Romanian v2 la build (cache în imagine)
RUN python3 -c "from huggingface_hub import snapshot_download; snapshot_download('eduardem/xtts-v2-romanian-v2', local_dir='/app/models/xtts-v2-romanian-v2', local_dir_use_symlinks=False)" 2>&1 || echo "⚠️ Modelul se va descărca la prima rulare"

COPY . .

# Expunere port HF Spaces
EXPOSE 7860

# Variabile de mediu
ENV XTTS_MODEL_DIR=/app/models/xtts-v2-romanian-v2

CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "7860", "--server.headless", "true", "--server.enableCORS", "false", "--server.enableXsrfProtection", "false"]
