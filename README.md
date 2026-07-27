---
title: Persona
emoji: 🎭
colorFrom: pink
colorTo: purple
sdk: streamlit
pinned: false
---

# Persona — AI Companion App

Aplicație de companie AI cu **clonare vocală** din mostre audio personale.

**Caracteristici principale:**
- 🎤 **Clonare vocală cu XTTS-v2** — creează voci unice din mostre audio
- 🌍 **Suport complet pentru limba română** — caracterele românești (ă, â, î, ș, ț)
- 🎵 **150+ sunete ambientale** — ploaie, mare, oraș, fermă, sport, sărbători, etc.
- 🔊 **Mixaj voce + ambient** — vocile personajelor se aud natural în contexte diferite
- ☁️ **Deploy flexibil** — server propriu (GPU) sau Streamlit Cloud (Edge-TTS fallback)
- 💾 **Date persistente** — PostgreSQL pentru salvare personaje și conversații
- 100% gratuit și open-source

---

## 🎤 Clonare Vocală — Ghid Rapid

### 1. Creează un personaj
### 2. Încarcă o mostră audio (10-30 secunde, wav/mp3/ogg)
### 3. Dă un nume vocii (ex: "Vocea Mariei")
### 4. Salvează personajul — XTTS-v2 va clona vocea!

**Recomandări pentru mostra audio:**
- Voce clară, fără zgomot de fundal
- 10-30 secunde
- Română sau altă limbă
- Fără muzică în fundal

---

## 🖥️ Deploy pe Server Propriu (XTTS-v2 + GPU)

**Necesar:** GPU NVIDIA cu minim 6GB VRAM

```bash
# Instalează dependențele
pip install -r requirements.txt

# Setează variabilele de mediu
export DATABASE_URL="postgresql://..."  # opțional
export HF_TOKEN="hf_..."  # pentru descărcare model
export USE_EDGE_TTS="0"  # FORȚEAZĂ XTTS-v2

# Pornește aplicația
streamlit run app.py --server.port 8501
```

XTTS-v2 Romanian v2 va fi descărcat automat de pe HuggingFace.

---

## ☁️ Deploy pe Streamlit Cloud

**Funcționează direct, dar fără clonare vocală** (XTTS necesită GPU).

### 1. Fork acest repository

### 2. Creează `.streamlit/secrets.toml`:

```toml
# Database pentru persistență (opțional dar recomandat)
DATABASE_URL = "postgresql://user:pass@host:5432/dbname"

# Edge-TTS fallback (gratuit, română, fără clonare)
USE_EDGE_TTS = "1"
DEFAULT_EDGE_VOICE = "AlinaNeural"  # sau "EmilNeural"
```

### 3. Deploy
1. Mergi la [share.streamlit.io](https://share.streamlit.io)
2. Conectează repository-ul GitHub
3. Alege branch-ul `main`
4. Deploy!

---

## 📚 Voci XTTS-v2 (Clonare)

Poți crea voci nelimitate din mostre audio:

| Tip | Descriere |
|-----|-----------|
| Voce proprie | Încarcă o mostră cu vocea ta |
| Voce de altcineva | Cu acordul persoanei |
| Personaj fictiv | Folosește o voce din film/joc |
| Celebritate | Cu atenție la drepturi de autor |

**Vocea se salvează cu personajul** și poate fi reutilizată oricând.

---

## 🔊 Biblioteca de Sunete Ambientale — 150+ sunete

### Categorii principale:

| Categorie | Exemple |
|-----------|---------|
| 🌧️ Natură și vreme | Ploaie, furtună, ninsoare, vânt |
| 🌊 Apă și mare | Mare, lac, râu, cascadă |
| 🌲 Pădure | Pădure, păsări, greieri |
| 🐄 Fermă | Vaci, oi, găini, cai |
| 🏙️ Oraș | Trafic, metrou, sirene |
| 🚂 Transport | Tren, autobuz, avion |
| ☕ Cafenele | Cafenea, brutărie, restaurant |
| 🏠 Casă | Bucătărie, TV, aspirator |
| 👶 Copii | Bebeluși, joacă, carusel |
| 🏥 Medical | Spital, dentist |
| ⚽ Sport | Stadion, sală de sport |
| 🎄 Sărbători | Crăciun, Anul Nou, Halloween |
| 🎵 Muzică | Concert, karaoke, jazz |
| 🌌 Spațiu | Nave spațiale, UFO |

---

## 🔧 Cerințe Tehnice

### Server propriu (XTTS-v2):
- Python 3.9-3.11
- GPU NVIDIA CUDA (6GB+ VRAM)
- 8GB+ RAM
- PostgreSQL (opțional)

### Streamlit Cloud:
- Python 3.9+
- Fără GPU (folosește Edge-TTS)
- PostgreSQL (opțional dar recomandat)
