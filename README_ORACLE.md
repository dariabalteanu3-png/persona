# GiftHub Persona — Oracle Cloud Free Tier Setup

## Ce este:
Aplicația GiftHub Persona rulează pe un VM Oracle Cloud Free Tier (ARM 4 CPU, 24 GB RAM).
Include Streamlit + Chatterbox XTTS v2 (server local pentru clonare de voce).

## Ce ai nevoie:
- Cont Oracle Cloud (gratuit - https://www.oracle.com/cloud/free/)
- SSH key

## Pași simpli:

### Pasul 1: Creează VM pe Oracle Cloud
1. Mergi la https://cloud.oracle.com
2. Login cu contul tău
3. Click "Create Instance"
4. Alege:
   - Shape: **VM.Standard.A1.Flex** (gratuit)
   - Platform: **ARM**
   - OCPU: 4
   - Memory: 24 GB
   - Image: **Ubuntu 22.04**
5. Click "Create"
6. Așteaptă până e gata (2-3 minute)
7. Copiează IP-ul VM-ului

### Pasul 2: Conectează-te pe SSH
Pe telefon, deschide terminal:
```
ssh ubuntu@IP_OF_TA_VM
```

### Pasul 3: Rulează setup-ul
```
curl -o setup_oracle.sh https://raw.githubusercontent.com/dariabalteanu3-png/persona/main/setup_oracle.sh
chmod +x setup_oracle.sh
bash setup_oracle.sh
```

### Pasul 4: Porneste aplicația
```
source venv/bin/activate
bash run.sh
```

Sau ca service permanent:
```
cp oracle.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable persona
sudo systemctl start persona
```

### Pasul 5: Accesează aplicația
Deschide browserul și mergi la:
```
http://IP_OF_TA_VM:8080
```

Sau portul tău:
```
http://IP_OF_TA_VM:{PORT}
```

## Ce ai:
- ✅ Aplicație Streamlit (GiftHub)
- ✅ Server TTS XTTS v2 (Clonare voce)
- ✅ Voci românești cu emoții
- ✅ Gratuit permanent (Oracle Cloud Free Tier)
- ✅ 4 CPU ARM, 24 GB RAM
- ✅ Nelimitat - fără expirere

## Întreținere:
```bash
# Verifică starea aplicației
sudo systemctl status persona

# Restartează
sudo systemctl restart persona

# Vezi log-uri
sudo journalctl -u persona -f
```
