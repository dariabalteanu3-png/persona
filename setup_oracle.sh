#!/bin/bash
# Script de setup pe Oracle Cloud Free Tier VM
# Rulează pe VM-ul tău ARM 4CPU / 24GB RAM

echo "=== Oracle Cloud Setup for GiftHub Persona ==="
echo ""

# Actualizează sistema
echo "[1/7] Actualizez sistemul..."
sudo apt-get update && sudo apt-get upgrade -y

# Instalează Python și pip
echo "[2/7] Instalez Python..."
sudo apt-get install -y python3 python3-pip python3-venv git ffmpeg libsndfile1

# Clonează repo-ul (dacă nu este deja)
echo "[3/7] Verific / clonez repo-ul..."
cd /home/ubuntu || cd /root
if [ ! -d "persona" ]; then
    git clone https://github.com/dariabalteanu3-png/persona.git
fi
cd persona

# Creează virtual environment
echo "[4/7] Creez mediu virtual..."
python3 -m venv venv
source venv/bin/activate

# Instalează dependințele
echo "[5/7] Instalez pachetele (poate dura 10-15 minute)..."
pip install --no-cache-dir -r requirements.txt

# Verifică că modelul Chatterbox poate fi descărcat
echo "[6/7] Verific Chatterbox TTS..."
python -c "from chatterbox.tts import ChatterboxTTS; print('Chatterbox gata!')" 2>&1 || echo "Chatterbox nu e instalat corect"

# Porneste aplicația
echo "[7/7] Pornește aplicația!"
echo ""
echo "Rulează:"
echo "  source venv/bin/activate"
echo "  bash run.sh"
echo ""
echo "Sau ca service permanent (systemd):"
echo "  sudo cp /home/ubuntu/persona/oracle.service /etc/systemd/system/"
echo "  sudo systemctl daemon-reload"
echo "  sudo systemctl enable persona"
echo "  sudo systemctl start persona"
