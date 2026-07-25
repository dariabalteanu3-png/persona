FROM python:3.11-slim

WORKDIR /app

# Dependențe sistem pentru audio (Chatterbox TTS)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x start.sh

EXPOSE 7860

CMD ["bash", "start.sh"]
