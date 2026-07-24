FROM python:3.12-slim

# Instalează ffmpeg și libsndfile pentru audio
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiază fișierele proiectului
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Deschide porturile
EXPOSE 8080 5001

# Rulează aplicația
CMD ["bash", "run.sh"]
