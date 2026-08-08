---
title: Persona
emoji: 🎭
colorFrom: pink
colorTo: purple
sdk: streamlit
pinned: false
---

# Persona — AI Companion App

Aplicație de companie AI cu **clonare vocală în limba română** (Fish Audio).

**Caracteristici principale:**
- 🎤 **Clonare vocală cu Fish Audio** (model `s2.1-pro-free`) — creează voci unice din mostre audio, în română
- 🌍 **Suport complet pentru limba română**
- 🎵 **150+ sunete ambientale** — ploaie, mare, oraș, fermă, sport, sărbători
- 🔊 **Mixaj voce + ambient** — vocile personajelor se aud natural
- 💾 **Date persistente** — PostgreSQL pentru salvare personaje și conversații
- 🔁 **Fallback automat** — Chatterbox/F5-TTS ca rezervă când Fish Audio nu e disponibil
- 100% gratuit și open-source

---

## 🎤 Clonare Vocală — Ghid Rapid

### 1. Creează un personaj
### 2. Încarcă o mostră audio (10-30 secunde, wav/mp3/ogg)
### 3. Dă un nume vocii (ex: "Vocea Mariei")
### 4. Salvează personajul — Fish Audio va clona vocea!

**Recomandări pentru mostra audio:**
- Voce clară, fără zgomot de fundal
- 10-30 secunde
- Română sau altă limbă
- Fără muzică în fundal

---

## 🔑 Configurare Fish Audio (metoda principală de clonare a vocii)

1. Obține o cheie gratuită de la https://fish.audio (Settings → API Keys).
2. În **Streamlit Cloud → Settings → Secrets**, adaugă secretul cu numele exact:
   `FISH_AUDIO_API_KEY` = valoarea cheii (codul acceptă și `FISH_API_KEY` ca alias).
3. Codul folosește implicit modelul `s2.1-pro-free` (opțional, se poate schimba prin `FISH_AUDIO_MODEL`).

Vezi exemplul complet în `.streamlit_secrets.toml.example`.

---

## 🖥️ Deploy

```bash
# Instalează dependențele
pip install -r requirements.txt

# Setează variabilele de mediu
export DATABASE_URL="postgresql://..."  # opțional
export FISH_AUDIO_API_KEY="..."  # metoda principală de clonare a vocii
export HF_TOKEN="hf_..."  # opțional — pentru rezerva Chatterbox

# Pornește aplicația
streamlit run app.py --server.port 8501
```

---

## 📚 Voci (Clonare)

Poți crea voci nelimitate din mostre audio:

| Tip | Descriere |
|-----|-----------|
| Voce proprie | Încarcă o mostră cu vocea ta |
| Voce de altcineva | Cu acordul persoanei |
| Personaj fictiv | Folosește o voce din film/joc |
| Celebritate | Cu atenție la drepturi de autor |

**Vocea se salvează cu personajul** (asociată prin `voice_id`) și poate fi reutilizată oricând.

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

### Streamlit Cloud:
- Python 3.9+
- Cheie Fish Audio (`FISH_AUDIO_API_KEY`) — pentru clonarea vocii în română
- PostgreSQL (opțional dar recomandat)
