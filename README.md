---
title: Persona
emoji: 🎭
colorFrom: pink
colorTo: purple
sdk: streamlit
pinned: false
---

# Persona — AI Companion App

Aplicație de companie AI cu voci generate (Edge-TTS pentru cloud, XTTS-v2 pentru server propriu).

**Caracteristici:**
- Voci neuronale românești (Edge-TTS) sau clonare vocală (XTTS-v2)
- Suport oficial pentru caracterele românești (ă, â, î, ș, ț, Ă, Â, Î, Ș, Ț)
- Bibliotecă de 70+ sunete ambientale (ploaie, mare, oraș, fermă, etc.)
- Sistem de sunete ambientale în conversații
- 100% gratuit, open-source

---

## 🚀 Deploy pe Streamlit Cloud

**Funcționează direct pe Streamlit Cloud!**

### 1. Fork sau clonează acest repository

### 2. Creează `secrets.toml` în `.streamlit/secrets.toml`

```toml
# Folosește Edge-TTS (gratuit, română) - funcționează pe cloud!
USE_EDGE_TTS = "1"
DEFAULT_EDGE_VOICE = "AlinaNeural"  # Feminin (default)
# DEFAULT_EDGE_VOICE = "EmilNeural"  # Masculin

# Adaugă DATABASE_URL pentru date persistente (opțional)
# DATABASE_URL = "postgresql://..."
```

### 3. Deploy pe Streamlit Cloud

1. Mergi la [share.streamlit.io](https://share.streamlit.io)
2. Conectează repository-ul GitHub
3. Alege branch-ul `main`
4. Deploy!

---

## 🖥️ Deploy pe Server Propriu (CU GPU)

Pentru clonare vocală reală cu XTTS-v2:

```bash
# Setează variabilele
export USE_EDGE_TTS="0"
export FORCE_CLOUD="0"
export DATABASE_URL="postgresql://..."

# Pornește aplicația
cd /workspace/project/persona
uv pip install -r requirements.txt
streamlit run app.py --server.port 8501
```

---

## 📚 Voci Disponibile

### Edge-TTS (Cloud)
| Voce | Gen | Descriere |
|------|-----|-----------|
| AlinaNeural | F | Voce feminin românesc |
| EmilNeural | M | Voce masculin român |

### XTTS-v2 (Server propriu)
- Poți încărca propria mostră audio pentru clonare

---

## 🔊 Sunete Ambientale

Biblioteca include 70+ sunete în 14 categorii:
- Natură și vreme (ploaie, furtună, ninsoare, vânt)
- Apă (mare, lac, râu, fântâni)
- Transport (tren, metrou, autobuz, trafic)
- Animale (câini, pisici, păsări, ferme)
- Casă (bucătărie, cafea, TV, aspirator)
- și multe altele...
