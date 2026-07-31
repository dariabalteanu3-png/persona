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

# Instalare F5-TTS pentru voice cloning (PyTorch)
RUN pip install --no-cache-dir f5-tts torch torchaudio

COPY . .

# Expunere port HF Spaces
EXPOSE 7860

# Variabile de mediu
ENV F5_MODEL_DIR=/app/models/f5-tts

CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "7860", "--server.headless", "true", "--server.enableCORS", "false", "--server.enableXsrfProtection", "false"]
