"""Generare vocala cu Microsoft Edge TTS (gratuit, română).

Suporta:
- Microsoft Edge TTS (gratuit, voci neuronale românești) - RECOMANDAT pentru cloud
- Piper TTS (local, offline, română)
- XTTS-v2 pentru clonare voce (dacă e disponibil local)
- gTTS fallback (Google TTS)
"""

import asyncio
import base64
import hashlib
import io
import logging
import os
import re
import time
import wave
from pathlib import Path

import numpy as np

from dotenv import load_dotenv

# ==== CONFIGURAȚIE CLOUD vs LOCAL ====
# Setează FORCE_CLOUD=1 sau USE_EDGE_TTS=1 pentru deployment pe Streamlit Cloud
# (Edge-TTS funcționează pe cloud, XTTS-v2 NU)
_FORCE_CLOUD = os.environ.get("FORCE_CLOUD", "0") == "1"
_USE_EDGE_TTS = os.environ.get("USE_EDGE_TTS", "0") == "1"
_USE_CLOUD_TTS = _FORCE_CLOUD or _USE_EDGE_TTS

# Voci Edge-TTS disponibile pentru română
EDGE_TTS_VOICES = {
    "AlinaNeural": "ro-RO-AlinaNeural",  # Feminin
    "EmilNeural": "ro-RO-EmilNeural",    # Masculin
}
_DEFAULT_EDGE_VOICE = os.environ.get("DEFAULT_EDGE_VOICE", "AlinaNeural")

# Edge TTS - Microsoft (gratuit, română)
try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False
    print("⚠️ edge-tts nu e instalat.")

# Piper TTS - local, offline, română (descărcat automat)
_PIPER_DIR = os.environ.get("PIPER_DIR", "/tmp/piper")
_PIPER_MODEL_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx"
_PIPER_CONFIG_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main/ro/ro_RO/mihai/medium/ro_RO-mihai-medium.onnx.json"
_PIPER_PATH = os.path.join(_PIPER_DIR, "romanian_medium.onnx")
_PIPER_CONFIG = os.path.join(_PIPER_DIR, "romanian_medium.onnx.json")

def _ensure_piper_model():
    """Descărcă modelul Piper la nevoie."""
    if _os.path.exists(_PIPER_PATH) and os.path.exists(_PIPER_CONFIG):
        return True
    
    print(f"📥 Descarc model Piper românesc (60 MB)...")
    os.makedirs(_PIPER_DIR, exist_ok=True)
    
    try:
        import urllib.request
        # Descarcăm modelul
        urllib.request.urlretrieve(_PIPER_MODEL_URL, _PIPER_PATH)
        print(f"✅ Model descărcat: {_PIPER_PATH}")
        # Descarcăm config
        urllib.request.urlretrieve(_PIPER_CONFIG_URL, _PIPER_CONFIG)
        print(f"✅ Config descărcat: {_PIPER_CONFIG}")
        return True
    except Exception as e:
        print(f"⚠️ Nu am putut descărca modelul Piper: {e}")
        return False

# Inițializăm Piper
_PIPER_AVAILABLE = os.path.exists(_PIPER_PATH) and os.path.exists(_PIPER_CONFIG)

# gTTS fallback
try:
    from gtts import gTTS as _gTTS
    _GTTS_AVAILABLE = True
except ImportError:
    _GTTS_AVAILABLE = False
    print("⚠️ gTTS nu e instalat.")

load_dotenv(Path(__file__).parent / ".env")
_log = logging.getLogger("voice")


class VoiceGenerationError(RuntimeError):
    """Eroare user-facing de la serviciul de generare vocala."""


# ════════════════════════════════════════════════════
#  Configurare
# ════════════════════════════════════════════════════

XTTS_MODEL_REPO = "eduardem/xtts-v2-romanian-v2"
XTTS_MODEL_DIR = os.environ.get("XTTS_MODEL_DIR", "/tmp/xtts_v2_romanian_model")

_engines = {}  # model instance cache
_engine_available = None  # cache engine availability
_voice_samples = {}  # voice_id -> sample_bytes
_model_loading = False


# ════════════════════════════════════════════════════
#  Normalizare text pentru TTS
# ════════════════════════════════════════════════════

_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\uFE0F\u2764]"
)

# Normalizare cedilla pentru romana
_CEDILLA_TO_COMMA = str.maketrans({
    "\u015f": "\u0219",  # s -> s (lowercase)
    "\u0163": "\u021b",  # t -> t (lowercase)
    "\u015e": "\u0218",  # S -> S (uppercase)
    "\u0162": "\u021a",  # T -> T (uppercase)
})


def _normalize_romanian(text: str) -> str:
    """Normalizeaza caracterele cedilla pentru XTTS-v2."""
    return str(text).translate(_CEDILLA_TO_COMMA)


def _expressify(text):
    """Curata markup-ul si normalizeaza textul romanesc pentru TTS."""
    text = str(text or "")
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", " ", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"#+\s*", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = text.replace("&", " si ")
    text = text.replace("%", " la suta")
    text = re.sub(r"\.{3}", "... ", text)
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text or "..."


# ════════════════════════════════════════════════════
#  Motor XTTS-v2 Romanian v2
# ════════════════════════════════════════════════════

def _check_xtts():
    """Verifica daca XTTS-v2 poate fi importat."""
    global _engine_available
    if _engine_available is not None:
        return _engine_available
    
    try:
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts
        _engine_available = True
        print("🔊 Motor XTTS-v2 disponibil")
        return True
    except ImportError as e:
        _engine_available = False
        print(f"⚠️ Motor XTTS-v2 nu e disponibil: {e}")
        return False


def _ensure_xtts_model():
    """Descarca modelul XTTS-v2 Romanian v2 (daca nu e deja) si il incarca."""
    global _model_loading
    
    if "xtts" in _engines:
        return _engines["xtts"]
    
    if _model_loading:
        # Asteapta daca alt fir incarca deja modelul
        while _model_loading:
            time.sleep(0.5)
        return _engines.get("xtts")
    
    _model_loading = True
    
    try:
        print(f"⏳ Se descarca modelul XTTS-v2 Romanian v2 ({XTTS_MODEL_REPO})...")
        start = time.time()

        from huggingface_hub import snapshot_download
        from TTS.tts.configs.xtts_config import XttsConfig
        from TTS.tts.models.xtts import Xtts

        # Descarca model (doar prima data, cache in /tmp)
        os.makedirs(XTTS_MODEL_DIR, exist_ok=True)
        snapshot_download(
            repo_id=XTTS_MODEL_REPO,
            local_dir=XTTS_MODEL_DIR,
            local_dir_use_symlinks=False,
        )

        # Incarca
        config = XttsConfig()
        config.load_json(os.path.join(XTTS_MODEL_DIR, "config.json"))

        model = Xtts(config)
        model.load_checkpoint(
            config,
            checkpoint_path=os.path.join(XTTS_MODEL_DIR, "model.pth"),
            use_deepspeed=False,
        )
        
        # Incearca GPU daca e disponibil
        try:
            import torch
            if torch.cuda.is_available():
                model = model.to("cuda")
                print("🚀 XTTS-v2 foloseste GPU")
            else:
                model = model.to("cpu")
                print("💻 XTTS-v2 foloseste CPU")
        except:
            model = model.to("cpu")
            print("💻 XTTS-v2 foloseste CPU")
        
        model.eval()

        elapsed = time.time() - start
        print(f"✅ Model XTTS-v2 Romanian v2 incarcat in {elapsed:.1f}s")
        _engines["xtts"] = model
        return model
        
    finally:
        _model_loading = False


def _xtts_generate(text, sample_bytes, similarity_boost=0.75, style=0.0):
    """Genereaza WAV cu XTTS-v2 Romanian v2."""
    import torch

    model = _ensure_xtts_model()

    # Salveaza mostra temporar
    ref_path = "/tmp/_xtts_ref.wav"
    with open(ref_path, "wb") as f:
        f.write(sample_bytes)

    # Normalizeaza textul pentru romana
    text = _normalize_romanian(text)
    
    # Mareste limita de caractere pentru romana
    if hasattr(model, 'tokenizer') and hasattr(model.tokenizer, 'char_limits'):
        model.tokenizer.char_limits["ro"] = 250

    # Obtine conditionare
    gpt_cond, speaker_emb = model.get_conditioning_latents(
        audio_path=ref_path,
        gpt_cond_len=3,
        max_ref_length=60,
    )

    # Calculeaza max_new_tokens pentru a preveni hallucination
    word_count = len(text.split())
    max_gen_tokens = max(min(word_count * 50, 500), 150)

    # Genereaza
    temperature = max(0.1, 1.0 - float(similarity_boost) * 0.6)
    top_p = max(0.5, float(similarity_boost))
    length_penalty = 1.0 + float(style) * 0.3

    outputs = model.inference(
        text=text,
        language="ro",
        gpt_cond_latent=gpt_cond,
        speaker_embedding=speaker_emb,
        temperature=temperature,
        length_penalty=length_penalty,
        repetition_penalty=2.0,
        top_k=50,
        top_p=top_p,
        enable_text_splitting=True,
        max_new_tokens=max_gen_tokens,
    )

    wav = outputs.get("wav") if isinstance(outputs, dict) else outputs
    if torch.is_tensor(wav):
        wav_np = wav.cpu().numpy()
    else:
        wav_np = np.array(wav, dtype=np.float32)
    if wav_np.ndim > 1:
        wav_np = wav_np.squeeze()

    # Elimina tacerile de la final
    wav_np = _trim_trailing_silence(wav_np, sr=24000)

    output_sr = 24000
    wav_int = (np.clip(wav_np, -1.0, 1.0) * 32767).astype("<i2")

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(output_sr)
        wf.writeframes(wav_int.tobytes())

    return buf.getvalue()


def _trim_trailing_silence(wav_np, sr=24000, threshold_db=-40, window_ms=25, margin_ms=50):
    """Elimina tacerile de la finalul audio-ului."""
    window = int(sr * window_ms / 1000)
    margin = int(sr * margin_ms / 1000)
    threshold = 10 ** (threshold_db / 20)
    
    for i in range(len(wav_np) - window, 0, -window):
        rms = np.sqrt(np.mean(wav_np[i:i+window] ** 2))
        if rms > threshold:
            end = min(i + window + margin, len(wav_np))
            return wav_np[:end]
    
    return wav_np


# ════════════════════════════════════════════════════
#  API PUBLIC TTS
# ════════════════════════════════════════════════════

def text_to_speech(
    text,
    voice_id,
    stability=0.5,
    similarity_boost=0.75,
    style=0.0,
    expressive=True,
    tone=None,
):
    """Genereaza WAV cu vocea clonata.

    Ordinea preferata:
    - FORCE_CLOUD=1 / USE_EDGE_TTS=1 -> Edge-TTS direct (cloud)
    - Altfel -> XTTS-v2 cu clonare (local/server propriu)

    Args:
        text: Textul de generat
        voice_id: ID-ul vocii (asociat cu mostra salvata)
        stability: Stabilitate (0-1)
        similarity_boost: Similaritate (0-1) - cat de aproape de mostra
        style: Stil (0-1) - expresivitate
        expressive: Daca textul trebuie procesat
        tone: Tonul vocii (optional) - "AlinaNeural" sau "EmilNeural" pentru Edge-TTS

    Returns:
        bytes: Audio WAV
    """
    spoken = _expressify(str(text) if expressive else (text or "..."))

    if not spoken or spoken == "...":
        return _generate_silence(duration=0.5)

    # MOD CLOUD: Edge-TTS direct (functioneaza pe Streamlit Cloud)
    if _USE_CLOUD_TTS and _EDGE_TTS_AVAILABLE:
        print(f"[CLOUD] Edge-TTS: {len(spoken)} caractere...")
        try:
            edge_voice = EDGE_TTS_VOICES.get(tone or _DEFAULT_EDGE_VOICE, EDGE_TTS_VOICES[_DEFAULT_EDGE_VOICE])
            return _edge_tts_generate(spoken, voice=edge_voice)
        except Exception as edge_err:
            print(f"Edge-TTS a esuat: {edge_err}")

    # MOD LOCAL: XTTS-v2 cu clonare voce
    sample_bytes = _voice_samples.get(voice_id)
    if not sample_bytes:
        # Fallback la Edge-TTS daca nu avem mostra
        if _EDGE_TTS_AVAILABLE:
            print("Nicio mostra - folosesc Edge-TTS...")
            edge_voice = EDGE_TTS_VOICES.get(tone or _DEFAULT_EDGE_VOICE, EDGE_TTS_VOICES[_DEFAULT_EDGE_VOICE])
            return _edge_tts_generate(spoken, voice=edge_voice)
        raise VoiceGenerationError(
            "Vocea nu are o mostra salvata. "
            "Incarca o mostra audio pentru acest personaj."
        )

    try:
        print(f"Generare XTTS-v2 (romana, clonare voce): {len(spoken)} caractere...")
        wav = _xtts_generate(
            spoken,
            sample_bytes,
            similarity_boost=similarity_boost,
            style=style,
        )
        print(f"Voce clonata generata: {len(wav)} bytes")
        return wav

    except Exception as exc:
        error_msg = str(exc)
        print(f"XTTS a esuat: {error_msg}")

        # Fallback 1: Piper TTS (local, offline, romana)
        if _PIPER_AVAILABLE:
            print("Folosesc Piper TTS (local, offline)...")
            try:
                return _piper_generate(spoken)
            except Exception as piper_err:
                print(f"Piper a esuat: {piper_err}")

        # Fallback 2: Edge TTS (gratuit, Microsoft, romana)
        if _EDGE_TTS_AVAILABLE:
            print("Folosesc Edge TTS (Microsoft, gratuit)...")
            try:
                edge_voice = EDGE_TTS_VOICES.get(tone or _DEFAULT_EDGE_VOICE, EDGE_TTS_VOICES[_DEFAULT_EDGE_VOICE])
                return _edge_tts_generate(spoken, voice=edge_voice)
            except Exception as edge_err:
                print(f"Edge TTS a esuat: {edge_err}")

        # Fallback 3: gTTS (Google TTS)
        if _GTTS_AVAILABLE:
            print("Folosesc gTTS fallback (Google TTS)...")
            try:
                return _gtts_generate(spoken)
            except Exception as gtts_err:
                print(f"gTTS fallback si el a esuat: {gtts_err}")

        raise VoiceGenerationError(
            f"Nici XTTS nici TTS-urile gratuite nu functioneaza."
        ) from exc


def _gtts_generate(text, lang="ro") -> bytes:
    """Generare vocală cu Google TTS (fallback)."""
    from pydub import AudioSegment
    
    # Generăm cu gTTS
    tts = _gTTS(text=text, lang=lang, slow=False)
    mp3_buf = io.BytesIO()
    tts.write_to_fp(mp3_buf)
    mp3_buf.seek(0)
    
    # Convertim MP3 în WAV (gTTS returnează MP3)
    audio = AudioSegment.from_mp3(mp3_buf)
    wav_buf = io.BytesIO()
    audio.export(wav_buf, format="wav")
    wav_buf.seek(0)
    
    return wav_buf.read()



async def _edge_tts_async(text, voice="ro-RO-AlinaNeural") -> bytes:
    """Generare vocală cu Microsoft Edge TTS (asincron)."""
    import tempfile
    
    mp3_path = tempfile.mktemp(suffix=".mp3")
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(mp3_path)
    
    # Convertim MP3 în WAV
    from pydub import AudioSegment
    audio = AudioSegment.from_mp3(mp3_path)
    wav_buf = io.BytesIO()
    audio.export(wav_buf, format="wav")
    wav_buf.seek(0)
    
    # Ștergem fișierul temporar
    try:
        os.remove(mp3_path)
    except:
        pass
    
    return wav_buf.read()


def _edge_tts_generate(text, voice="ro-RO-AlinaNeural") -> bytes:
    """Generare vocală cu Microsoft Edge TTS (sync wrapper)."""
    return asyncio.run(_edge_tts_async(text, voice))


def _piper_generate(text) -> bytes:
    """Generare vocală cu Piper TTS (local, offline, română)."""
    import subprocess
    import tempfile
    
    piper_bin = os.environ.get("PIPER_BIN", "/home/openhands/.local/bin/piper")
    
    # Creăm un fișier temporar pentru output
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        output_path = f.name
    
    try:
        # Normalizăm textul pentru Piper
        text_normalized = text.translate(_CEDILLA_TO_COMMA)
        
        # Generăm cu piper
        result = subprocess.run(
            [piper_bin, "--model", _PIPER_PATH, "--config", _PIPER_CONFIG, 
             "--output_file", output_path],
            input=text_normalized,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"Piper error: {result.stderr}")
        
        # Citim output-ul
        with open(output_path, "rb") as f:
            wav_bytes = f.read()
        
        return wav_bytes
        
    finally:
        try:
            _os_module.remove(output_path)
        except:
            pass


def _generate_silence(duration=1.0, sample_rate=24000) -> bytes:
    """Genereaza tacere WAV."""
    n_samples = int(sample_rate * duration)
    silence = np.zeros(n_samples, dtype=np.int16)
    
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(silence.tobytes())
    
    return buf.getvalue()


def text_to_speech_from_sample(text, sample_bytes, reference_text=None, sample_name="reference.wav"):
    """Genereaza preview direct din mostra audio.
    
    Folosit pentru a testa mostra inainte de salvare.
    """
    spoken = _expressify(str(text) or "...")
    
    try:
        return _xtts_generate(spoken, sample_bytes, similarity_boost=0.7, style=0.3)
    except Exception as exc:
        raise VoiceGenerationError(f"Eroare generare preview: {exc}") from exc



# ════════════════════════════════════════════════════
#  Voice Library - Bibliotecă de voci clonate
# ════════════════════════════════════════════════════

_speaker_cache = {}  # voice_id -> (gpt_cond_latent, speaker_embedding)


def get_speaker_embeddings(voice_id, sample_bytes):
    """Preia sau calculează speaker embeddings pentru o voce.
    
    Folosește cache pentru a evita recalcularea.
    """
    if voice_id in _speaker_cache:
        return _speaker_cache[voice_id]
    
    if not _check_xtts():
        return None, None
    
    try:
        _ensure_xtts_model()
        model = _get_xtts_model()
        
        # salvăm mostra temporar
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            temp_path = f.name
            f.write(sample_bytes)
        
        try:
            gpt_cond, speaker_emb = model.get_conditioning_latents(
                audio_path=temp_path
            )
            # salvăm în cache
            _speaker_cache[voice_id] = (gpt_cond, speaker_emb)
            print(f"✅ Speaker embeddings calculate pentru {voice_id[:16]}...")
            return gpt_cond, speaker_emb
        finally:
            os.remove(temp_path)
    except Exception as e:
        print(f"⚠️ Nu pot calcula speaker embeddings: {e}")
        return None, None


def clear_speaker_cache(voice_id=None):
    """Șterge cache-ul de speaker embeddings."""
    if voice_id:
        _speaker_cache.pop(voice_id, None)
    else:
        _speaker_cache.clear()


def generate_with_voice_library(text, voice_id, sample_bytes, **kwargs):
    """Generează audio folosind o voce din biblioteca de voci.
    
    Folosește speaker embeddings salvate dacă sunt disponibile.
    """
    spoken = _expressify(str(text))
    if not spoken:
        return _generate_silence(duration=0.5)
    
    if not _check_xtts():
        raise VoiceGenerationError("XTTS-v2 nu este disponibil.")
    
    try:
        _ensure_xtts_model()
        model = _get_xtts_model()
        
        # Verificăm dacă avem speaker embeddings în cache
        gpt_cond, speaker_emb = get_speaker_embeddings(voice_id, sample_bytes)
        
        if gpt_cond is None:
            # Fallback: generăm fără speaker embeddings (folosește modelul default)
            print("⚠️ Folosesc XTTS fără speaker embeddings...")
            return _xtts_generate_fallback(spoken, **kwargs)
        
        print(f"🔊 Generare XTTS-v2 cu voce clonata: {len(spoken)} caractere...")
        wav = model.inference(
            text=spoken,
            language="ro",
            gpt_cond_latent=gpt_cond,
            speaker_embedding=speaker_emb,
            temperature=kwargs.get("temperature", 0.3),
            top_p=kwargs.get("top_p", 0.7),
            top_k=kwargs.get("top_k", 30),
            length_penalty=kwargs.get("length_penalty", 0.8),
            repetition_penalty=kwargs.get("repetition_penalty", 10.0),
            enable_text_splitting=True,
            max_new_tokens=kwargs.get("max_new_tokens", 500),
        )
        return _mel_to_wav(wav)
        
    except Exception as e:
        print(f"❌ XTTS error: {e}")
        raise



# ════════════════════════════════════════════════════
#  Gestionare mostre de voce
# ════════════════════════════════════════════════════

def _decode_sample(sample_b64):
    """Decodeaza mostra audio din base64."""
    if not sample_b64:
        return None
    if sample_b64.startswith("data:"):
        sample_b64 = sample_b64.split(",", 1)[-1]
    try:
        return base64.b64decode(sample_b64)
    except Exception as exc:
        raise VoiceGenerationError("Mostra audio este invalida.") from exc


def voice_id_for_sample(sample_bytes):
    """Genereaza un ID unic pentru mostra de voce."""
    if not sample_bytes:
        return None
    return "v:" + hashlib.sha256(sample_bytes).hexdigest()[:24]


def register_character_voice(char):
    """Inregistreaza mostra de voce pentru un character.
    
    Stocheaza mostra in memorie pentru utilizare la generarea audio.
    
    Args:
        char: Dict cu campurile 'voice_id' si optional 'voice_sample_b64'
    """
    voice_id = char.get("voice_id")
    sample_b64 = char.get("voice_sample_b64")
    
    if voice_id and sample_b64:
        sample_bytes = _decode_sample(sample_b64)
        if sample_bytes:
            _voice_samples[voice_id] = sample_bytes
            print(f"🔊 Mostra de voce inregistrata pentru {voice_id[:20]}...")
            
            # Verifica daca modelul XTTS e disponibil
            if _check_xtts():
                try:
                    _ensure_xtts_model()
                    print("✅ Model XTTS-v2 gata pentru generare")
                except Exception as e:
                    print(f"⚠️ Model XTTS-v2 nu poate fi incarcat: {e}")


def forget_registered_voices(voice_ids=None):
    """Sterge mostrele de voce din memorie."""
    if voice_ids is None:
        _voice_samples.clear()
        return
    for voice_id in voice_ids:
        _voice_samples.pop(voice_id, None)


# ════════════════════════════════════════════════════
#  Acces la lista de voci disponibile
# ════════════════════════════════════════════════════

def get_available_voices():
    """Returneaza informatii despre modelul XTTS-v2 disponibil."""
    return {
        "xtts-v2-romanian": {
            "name": "XTTS-v2 Romanian v2",
            "description": "Clonare vocala cu model finetuned pentru romana",
            "features": ["voice cloning", "romanian", "emotional expression"]
        }
    }


def get_default_voice():
    """Returneaza tipul de voce implicit."""
    return "xtts-v2-cloned"


# ════════════════════════════════════════════════════
#  SINTEZA AMBIENTALA DSP
# ════════════════════════════════════════════════════

def _ambient_wav(preset, duration=12.0, sample_rate=22050):
    """DSP-based ambient synthesis using numpy."""
    try:
        import numpy as np
    except ImportError:
        output = io.BytesIO()
        with wave.open(output, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * int(sample_rate * duration))
        return output.getvalue()

    sr = int(sample_rate)
    dur = max(2.0, min(float(duration), 30.0))
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False)
    rng = np.random.default_rng()

    def wn(size=n):
        return rng.uniform(-1.0, 1.0, size)

    def fband(sig, lo=0, hi=None):
        S = np.fft.rfft(sig)
        f = np.fft.rfftfreq(len(sig), 1 / sr)
        if lo > 0: S[f < lo] = 0
        if hi: S[f > hi] = 0
        return np.fft.irfft(S, len(sig))

    def pink(lo=20, hi=8000, size=n):
        f = np.fft.rfftfreq(size, 1 / sr)
        with np.errstate(divide="ignore", invalid="ignore"):
            mag = np.where(f > 0, 1.0 / np.sqrt(np.maximum(f, 0.1)), 0)
        mag[f < lo] = 0
        if hi: mag[f > hi] = 0
        ph = rng.uniform(0, 2 * np.pi, len(f))
        return np.fft.irfft(mag * np.exp(1j * ph), size)

    def am(rate, depth=0.5, dc=1.0):
        ph = rng.uniform(0, 2 * np.pi)
        return dc + depth * np.sin(2 * np.pi * rate * t + ph)

    def sine(freq, amp=1.0):
        return amp * np.sin(2 * np.pi * freq * t + rng.uniform(0, 2 * np.pi))

    def norm(sig, pk=0.88):
        m = np.max(np.abs(sig))
        return sig * (pk / m) if m > 1e-9 else sig

    def footsteps(rate, lo=300, hi=4000, amp=0.6):
        out = np.zeros(n)
        step = max(1, int(sr / rate))
        spread = max(1, step // 4)
        pos = int(rng.integers(0, step))
        while pos < n:
            blen = min(int(rng.uniform(0.04, 0.18) * sr), n - pos)
            if blen > 0:
                burst = fband(rng.uniform(-1, 1, blen), lo, hi)
                env = np.exp(-np.linspace(0, 6, blen))
                out[pos:pos + blen] += burst * env * float(rng.uniform(0.5, 1.0)) * amp
            delta = int(rng.integers(-spread, spread + 1))
            pos += step + delta
        return out

    def birds(nb=8, lo_f=1500, hi_f=5000):
        out = np.zeros(n)
        for _ in range(int(rng.integers(nb // 2, nb + 1))):
            pos = int(rng.integers(0, n))
            clen = min(int(rng.uniform(0.05, 0.3) * sr), n - pos)
            if clen > 0:
                freq = float(rng.uniform(lo_f, hi_f))
                tl = np.linspace(0, clen / sr, clen)
                env = np.sin(np.pi * tl / (clen / sr))
                tone = np.sin(2 * np.pi * freq * tl + float(rng.uniform(0, 2 * np.pi)))
                tone *= 1 + 0.3 * np.sin(2 * np.pi * float(rng.uniform(5, 20)) * tl)
                out[pos:pos + clen] += tone * env * float(rng.uniform(0.15, 0.5))
        return out

    if preset == "rain":
        base = pink(100, 8000) * am(rng.uniform(0.05, 0.15), 0.1, 0.9) * 0.55
        drops = footsteps(float(rng.uniform(10, 18)), lo=1500, hi=6000, amp=0.22)
        sig = base + drops
    elif preset == "storm":
        rain = pink(100, 8000) * am(rng.uniform(0.1, 0.3), 0.25, 0.75) * 0.60
        rumble = pink(18, 160) * am(rng.uniform(0.04, 0.1), 0.45, 0.55) * 0.48
        thunder = np.zeros(n)
        for _ in range(int(rng.integers(2, 6))):
            pos = int(rng.integers(int(0.05 * n), n))
            tlen = min(int(rng.uniform(0.8, 2.8) * sr), n - pos)
            if tlen > 0:
                boom = pink(18, 450, tlen)
                env = np.concatenate([np.linspace(0, 1, max(1, tlen // 8)), np.exp(-np.linspace(0, 5, tlen - tlen // 8))])[:tlen]
                thunder[pos:pos + tlen] += boom * env * float(rng.uniform(0.55, 1.0))
        sig = rain + rumble + thunder * 0.90
    elif preset == "ocean":
        base = pink(55, 4500)
        w1 = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.05, 0.10)) * t + float(rng.uniform(0, np.pi)))) ** 0.55
        w2 = np.abs(np.sin(2 * np.pi * float(rng.uniform(0.07, 0.14)) * t + float(rng.uniform(0, np.pi)))) ** 0.55
        sig = base * (0.5 * w1 + 0.4 * w2) * 0.88
    elif preset == "fire":
        base = pink(55, 2800) * am(rng.uniform(1.5, 3.5), 0.3, 0.7) * 0.30
        crackle = footsteps(float(rng.uniform(14, 26)), lo=500, hi=5500, amp=0.45)
        pops = np.zeros(n)
        for _ in range(int(rng.integers(3, 10))):
            p = int(rng.integers(0, n))
            plen = min(int(0.055 * sr), n - p)
            if plen > 0:
                pops[p:p + plen] = rng.uniform(-1, 1, plen) * np.exp(-np.linspace(0, 8, plen))
        sig = base + crackle * 0.38 + fband(pops, 250, 6500) * 0.52
    elif preset == "wind":
        w1 = pink(140, 3800) * am(rng.uniform(0.07, 0.18), 0.55, 0.45) * 0.65
        w2 = pink(550, 5500) * am(rng.uniform(0.11, 0.28), 0.50, 0.50) * 0.28
        sig = w1 + w2
    elif preset == "blizzard":
        base = pink(180, 6500) * am(rng.uniform(0.15, 0.40), 0.65, 0.35) * 0.72
        whistle = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            fc = float(rng.uniform(900, 3200))
            chunk = fband(pink(fc - 180, fc + 180), fc - 180, fc + 180)
            whistle += chunk * am(rng.uniform(0.2, 0.5), 0.70, 0.30) * 0.30
        sig = base + whistle
    elif preset == "crickets":
        sig = np.zeros(n)
        for _ in range(int(rng.integers(3, 8))):
            freq = float(rng.uniform(2100, 3100))
            rate = float(rng.uniform(3.5, 5.5))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 14
            sig += chirp * sine(freq, 0.28)
    elif preset == "river":
        base = pink(90, 5000) * am(rng.uniform(0.12, 0.28), 0.20, 0.80) * 0.55
        gurgle = footsteps(float(rng.uniform(12, 24)), lo=380, hi=3200, amp=0.18)
        sig = base + gurgle * 0.28
    elif preset == "train":
        rumble = pink(28, 320) * am(rng.uniform(0.7, 1.3), 0.15, 0.85) * 0.44
        joints = footsteps(float(rng.uniform(3.5, 5.5)), lo=55, hi=420, amp=0.72)
        hiss = pink(1100, 8500) * 0.14
        sig = rumble + joints * 0.50 + hiss
    elif preset == "forest":
        leaves = pink(650, 6500) * am(rng.uniform(0.08, 0.22), 0.40, 0.60) * 0.30
        wind_low = pink(70, 650) * am(rng.uniform(0.04, 0.10), 0.30, 0.70) * 0.14
        sig = leaves + wind_low + birds(nb=10) * 0.40
    elif preset == "forest_walk":
        leaves = pink(650, 6500) * am(rng.uniform(0.08, 0.22), 0.35, 0.65) * 0.24
        steps = footsteps(float(rng.uniform(1.2, 1.8)), lo=220, hi=5500, amp=0.58)
        twigs = np.zeros(n)
        for _ in range(int(rng.integers(2, 7))):
            p = int(rng.integers(0, n))
            tlen = min(int(0.07 * sr), n - p)
            if tlen > 0:
                snap = fband(rng.uniform(-1, 1, tlen), 700, 7500)
                twigs[p:p + tlen] += snap * np.exp(-np.linspace(0, 12, tlen)) * float(rng.uniform(0.4, 0.85))
        sig = leaves + birds(nb=8) * 0.38 + steps * 0.50 + twigs
    elif preset == "cafe":
        murmur = fband(pink(140, 3200), 170, 2600) * am(rng.uniform(0.04, 0.12), 0.15, 0.85) * 0.37
        machine = np.zeros(n)
        mpos = int(sr * float(rng.uniform(2, 6)))
        while mpos < n:
            mlen = min(int(rng.uniform(0.9, 2.6 * sr)), n - mpos)
            if mlen > 0:
                hiss = fband(rng.uniform(-1, 1, mlen), 1800, 9000)
                menv = np.sin(np.pi * np.linspace(0, 1, mlen)) ** 0.5
                machine[mpos:mpos + mlen] += hiss * menv * float(rng.uniform(0.14, 0.28))
            mpos += int(sr * float(rng.uniform(8, 17)))
        clinks = footsteps(float(rng.uniform(0.14, 0.40)), lo=2200, hi=9500, amp=0.32)
        sig = murmur + machine * 0.38 + clinks * 0.22
    elif preset == "city":
        traffic = fband(pink(45, 1600), 55, 1300) * am(rng.uniform(0.04, 0.12), 0.20, 0.80) * 0.45
        hum = fband(pink(48, 130), 52, 120) * 0.17
        horns = np.zeros(n)
        for _ in range(int(rng.integers(1, 5))):
            p = int(rng.integers(0, n))
            hlen = min(int(rng.uniform(0.3, 2.0 * sr)), n - p)
            if hlen > 0:
                freq = float(rng.uniform(300, 750))
                env = np.sin(np.pi * np.linspace(0, 1, hlen)) ** 0.28
                tl = np.linspace(0, hlen / sr, hlen)
                horns[p:p + hlen] += np.sin(2 * np.pi * freq * tl) * env * float(rng.uniform(0.22, 0.55))
        sig = traffic + hum + horns * 0.38
    elif preset == "countryside":
        wind = pink(70, 2200) * am(rng.uniform(0.04, 0.10), 0.30, 0.70) * 0.18
        brd = birds(nb=14, lo_f=1100, hi_f=5800) * 0.52
        crk = np.zeros(n)
        for _ in range(int(rng.integers(2, 5))):
            freq = float(rng.uniform(2200, 2900))
            rate = float(rng.uniform(3.0, 5.2))
            ph = float(rng.uniform(0, 2 * np.pi))
            chirp = np.maximum(0.0, np.sin(2 * np.pi * rate * t + ph)) ** 16
            crk += chirp * sine(freq, 0.17)
        sig = wind + brd + crk * 0.38
    elif preset == "snow":
        faint = pink(70, 2000) * am(rng.uniform(0.03, 0.07), 0.40, 0.60) * 0.085
        sig = faint
    elif preset == "snow_walk":
        base = pink(55, 1300) * 0.07
        steps = fband(footsteps(float(rng.uniform(0.7, 1.1)), lo=100, hi=2800, amp=0.52), 80, 3200)
        sig = base + steps * 0.65
    elif preset == "station":
        crowd = fband(pink(180, 3200), 190, 2600) * am(rng.uniform(0.04, 0.10), 0.20, 0.80) * 0.31
        trains = np.zeros(n)
        for _ in range(int(rng.integers(1, 4))):
            p = int(rng.integers(0, int(0.7 * n)))
            tlen = min(int(rng.uniform(3, 9 * sr)), n - p)
            if tlen > 0:
                rumble = pink(28, 550, tlen)
                third = tlen // 3
                env = np.concatenate([np.linspace(0, 1, third), np.ones(third), np.linspace(1, 0, tlen - 2 * third)])[:tlen]
                trains[p:p + tlen] += rumble * env * float(rng.uniform(0.24, 0.56))
        pa = np.zeros(n)
        for _ in range(int(rng.integers(1, 3))):
            p = int(rng.integers(int(0.1 * n), int(0.75 * n)))
            alen = min(int(rng.uniform(3, 9 * sr)), n - p)
            if alen > 0:
                pa_noise = fband(pink(280, 3500, alen), 280, 3500)
                syl_env = np.zeros(alen)
                sp = 0
                while sp < alen:
                    sdur = int(rng.uniform(0.05, 0.19 * sr))
                    se = min(sp + sdur, alen)
                    syl_env[sp:se] = float(rng.uniform(0.28, 1.0))
                    sp += sdur + int(rng.uniform(0.02, 0.11 * sr))
                frame = np.sin(np.pi * np.linspace(0, 1, alen)) ** 0.28
                pa[p:p + alen] += pa_noise * syl_env * frame * float(rng.uniform(0.17, 0.38))
        sig = crowd + trains * 0.50 + pa * 0.55
    elif preset == "heels_parquet":
        base = pink(90, 2200) * 0.048
        clicks = np.zeros(n)
        step_n = max(1, int(sr / float(rng.uniform(1.5, 2.2))))
        spread = max(1, step_n // 6)
        pos = int(rng.integers(0, step_n // 2))
        while pos < n:
            clen = min(int(rng.uniform(0.008, 0.032 * sr)), n - pos)
            if clen > 0:
                click = fband(rng.uniform(-1, 1, clen), 1100, 9500)
                clicks[pos:pos + clen] += click * np.exp(-np.linspace(0, 18, clen)) * float(rng.uniform(0.5, 1.0))
                if float(rng.random()) < 0.62:
                    cp = pos + clen
                    crk_len = min(int(rng.uniform(0.06, 0.28 * sr)), n - cp)
                    if crk_len > 0:
                        crk_f = float(rng.uniform(190, 620))
                        crk = fband(rng.uniform(-1, 1, crk_len), crk_f - 80, crk_f + 240)
                        clicks[cp:cp + crk_len] += crk * np.exp(-np.linspace(0, 9, crk_len)) * float(rng.uniform(0.24, 0.56))
            pos += step_n + int(rng.integers(-spread, spread + 1))
        sig = base + clicks * 0.75
    else:
        sig = pink(70, 3200) * 0.052 + sine(50, 0.016) + sine(100, 0.009)

    sig = norm(sig)
    fade = int(0.04 * sr)
    if n > 2 * fade:
        sig[:fade] *= np.linspace(0, 1, fade)
        sig[-fade:] *= np.linspace(1, 0, fade)

    pcm = (np.clip(sig, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    return output.getvalue()


def sound_effect(prompt, duration=6.0, prompt_influence=0.45):
    """Returneaza un sunet ambient sintetizat local."""
    text = str(prompt or "").lower()
    presets = (
        # Natură și vreme
        ("storm", ("tunet", "furtun", "thunder", "storm", "lightning", "fulger", "grindina", "fulgere", "trăznete")),
        ("blizzard", ("crivat", "viscol", "blizzard", "howling wind", "strong wind", "vant puternic", "vânt puternic", "rafale")),
        ("rain_heavy", ("ploaie torențial", "ploaie abundent", "furtună ploaie", "showers heavy")),
        ("rain", ("ploaie", "rain", "drizzle", "shower", "picaturi", "picături", "ploaie ușoar", "ploaie moderată")),
        ("rain_window", ("ploaie geam", "ploaie pe geam", "picaturi geam", "rain on window", "ploaie pe acoperiș")),
        ("thunder_distant", ("tunete îndepărtat", "tunet departe", "thunder distant")),
        ("thunder_close", ("tunete apropiat", "tunet aproape", "tunete apropiate", "thunder close")),
        ("snow", ("ninso", "zapad", "snow", "iarna linist", "fulgi", "ninsoare", "zăpadă")),
        ("snow_walk", ("pasi zapada", "walking snow", "snow crunch", "footsteps snow", "snow underfoot", "zapada pasi", "pași zăpadă", "mers prin zăpadă")),
        ("wind", ("vant", "wind", "breeze", "adiere", "suflare", "vânt", "adieri")),
        ("wind_strong", ("vant puternic", "vânt puternic", "wind strong", "furtună vânt")),
        
        # Apă și natură
        ("ocean", ("mare", "val", "ocean", "wave", "beach", "litoral", "coasta", "valuri", "plajă", "delfini", "dolphin")),
        ("ocean_storm", ("mare agitat", "furtună mare", "ocean storm", "valuri mari", "valuri puternice")),
        ("river", ("rau", "river", "parau", "brook", "stream", "cascada", "waterfall", "râu", "pârâu", "cascadă")),
        ("fountain", ("fântân", "fountain", "artezian", "izvor", "spring", "apă curgând", "jet apă")),
        ("lake", ("lac", "lake", "lebede", "swan", "stuf", "trestie", "pont", " pontoane")),
        ("rainforest", ("pădure tropical", "jungle", "rainforest", "tropice")),
        
        # Oraș și transport
        ("city", ("oras", "city", "trafic", "traffic", "strada", "street", "urban", "bulevard", "oraș", "intersecție", "aglomerat")),
        ("city_heavy", ("trafic intens", "ambuteiaj", "mult", "many cars", "heavy traffic", "ore vârf", "claxoane")),
        ("train", ("tren", "train", "railroad", "railway", "sine", "vagon", "tren în mers", "tren în tunel")),
        ("station", ("gara", "station", "peron", "aeroport", "airport", "terminal", "announcement", "anunt", "metrou", "autogara", "gară", "stație")),
        ("station_train_coming", ("tren sosire", "tren care vine", "tren intrare", "tren plecare", "train arriving")),
        ("metro", ("metrou", "metro", "subway", "tramvai", "tram")),
        ("bus", ("autobuz", "bus", "troleibuz", "trolleybus", "taxi")),
        ("cars", ("mașin", "masin", "cars", "automobil", "motor", "vehicul")),
        ("sirens", ("siren", "politi", "ambulanta", "pompieri", "sirenă", "mașină poliție")),
        ("airport", ("avion", "airport", "decolare", "aterizare", "poartă", "îmbarcare")),
        
        # Animale
        ("crickets", ("greier", "cricket", "noapte linistita", "quiet night", "seara camp", "noapte", "insecte")),
        ("birds_morning", ("păsări dimineață", "birds morning", "cânt păsări", "păsări cântă", "birds chirping")),
        ("birds", ("păsări", "birds", "pasari", "ciocănit", "pădure păsări")),
        ("birds_lake", ("păsări lac", "rațe", "lebede", "broaște", "lake birds", "pescăruși")),
        ("farm", ("fermă", "farm", "găini", "cocoș", "vacă", "oi", "capre", "animal", "grajd", "curte")),
        ("dogs", ("câine", "caine", "dog", "câini", "dogs", "lătrat", "latrat")),
        ("cats", ("pisică", "pisica", "cat", "pisici", "cats", "tors", "miorcăit")),
        ("squirrels", ("veveri", "squirrel", "veveriță", "frunze", "nuci")),
        
        # Interioare
        ("cafe", ("cafenea", "cafe", "coffee shop", "restaurant", "bistro", "bar", "ceainarie", "clopoțel", "vânzător")),
        ("restaurant", ("restaurant", "restaurant aglomerat", "restaurant quiet")),
        ("bakery", ("brutărie", "bakery", "cuptor", "pâine", "covrig", "croasant", "foieta")),
        ("store", ("magazin", "store", "supermarket", "cumpărături", "cărucior", "produse")),
        ("library", ("bibliotecă", "library", "liniște", "lectură", "书馆")),
        ("office", ("birou", "office", "tastatură", "imprimantă", "telefon")),
        ("hospital", ("spital", "hospital", "cabinet medical", "clinică")),
        ("school", ("școală", "school", "universitate", "curs", "studenți")),
        
        # Activități
        ("kitchen", ("bucătărie", "kitchen", "mixer", "blender", "tigaie", "fierbător", "espressor", "café")),
        ("cooking", ("gătit", "gatit", "cooking", "prăjit", "fiert", "cuptor", " Tigaie", "capac", "scântei")),
        ("typing", ("tastatură", "typing", "keyboard", "calculator", "computer", "click", "mouse")),
        ("vacuum", ("aspirator", "vacuum", "curățenie", "mop", "găleată")),
        ("washing", ("mașină spălat", "washing machine", "usucator", "spălat rufe")),
        ("tv", ("televizor", "tv", "television", "telecomandă", "știri", "emisiune")),
        ("radio", ("radio", "radio on", "muzică radio")),
        
        # Food sounds
        ("chips", ("chips", "ronțăit", "chipsuri", "alune", "porumb", "popcorn", "covrig", "biscuiți")),
        ("eating", ("mestec", "mancat", "eating", "înghițit", "gheață", "băut")),
        ("drinking", ("băut", "drinking", "cafea", "apă", "suc", "halba", "pahar")),
        ("coffee_machine", ("espreso", "espresso", "café", "aparat cafea", "cafea prepar")),
        
        # Birou și casă
        ("room", ("cameră", "room", "casă", "house", "home", "interior", "liniștit")),
        ("heartbeat", ("bătăi inimă", "heartbeat", "inimă", "emoții")),
        ("clock", ("ceas", "clock", "ticăit", "tac", "timp")),
        
        # Scări și mișcare
        ("stairs", ("scar", "stairs", "scări", "urcare", "coborâre", "lift", "ascensor")),
        ("footsteps", ("pași", "footsteps", "pas", "mers", "alerg", "alergare")),
        ("footsteps_wood", ("pași parchet", "footsteps wood", "parchet", "lemn", "lemn podea")),
        ("footsteps_tile", ("pași gresie", "gresie", "tile floor", "beton")),
        ("footsteps_outside", ("pași afară", "pietris", "asfalt", "iérbă", "outside")),
        ("heels", ("tocuri", "heels", "pantof cu toc", "toc pantof", "tocuri pe parchet", "tocuri pe gresie")),
        ("heely", ("adidași", "adidasi", "sneakers", "sport", "tenisi")),
        
        # Countryside
        ("countryside", ("tara", "sat", "countryside", "ferma", "cimp", "rural", "birds chirp", "livada", "țară", "mediu rural")),
        ("countryside_morning", ("dimineață țară", "morning countryside", "cocoș", "găini", "soare", "sat dimineață")),
        ("countryside_night", ("noapte sat", "countryside night", "greieri", "linie", "tăcere")),
        ("tractor", ("tractor", "tractor câmp", "tractor drum", "agricol", "combine")),
        ("cart", ("căruță", "caruta", "căruț", "roți lemn", "cal căruță", "trăsură", "clopoței")),
        ("chickens", ("pui", "găini", "chickens", "pio", "rațe", "boboci")),
        ("frogs", ("broaște", "frogs", "bălți", "stuf")),
        
        # Shopping
        ("shopping_mall", ("mall", "centru comercial", "scări rulante", "lift", "aglomerat")),
        ("checkout", ("casă marcat", "checkout", "scanare", "bip", "plată", "card", "numerar", "bancnot")),
        ("shopping_bags", ("sacose", "pungi", "shopping bags", "cumpărături", "mar", "去")),
        
        # Machiaj și baie
        ("makeup", ("machiaj", "makeup", "pensul", "fard", "ruj", "pudră", "oglindă", "cremă")),
        ("bathroom", ("baie", "bathroom", "robinet", "duș", "cadă", "prosoape", "sertar", "chiuvetă")),
        ("water_faucet", ("apă curge", "robinet", "faucet", "duș", "apă rece", "apă caldă")),
        
        # Sezoane
        ("spring", ("primăvară", "spring", "păsări", "natură", "flori")),
        ("summer", ("vară", "summer", "greieri", "căldură", "zgomote vară")),
        ("autumn", ("toamnă", "autumn", "frunze", "vânt", "ploaie", "采集")),
        ("winter", ("iarnă", "winter", "zăpadă", "ger", "vânt rece", "crivăț")),
        
        # Diverse
        ("forest", ("padure", "forest", "frunze", "copac", "woods", "jungle", "livada", "pădure")),
        ("forest_walk", ("pasi padure", "walking forest", "footsteps leaves", "leaves underfoot", "crunch leaves", "rustling underfoot", "mers padure", "fosnet pasi", "frunze", "crengi")),
        ("party", ("petrecere", "party", "muzică", "aplaud", "haha", "petrec", "festival")),
        ("crowd", ("mulțime", "crowd", "oameni", "aglomerat", "galerie", "stadio")),
        ("airplane", ("avion", "airplane", "decolare", "zbor", "motor", "cabină")),
        ("helicopter", ("elicopter", "helicopter", "rotor", "zbor")),
        
        # Noapte
        ("night", ("noapte", "night", "liniște", "tăcere", "stele")),
        ("night_city", ("noapte oras", "night city", "neon", "stradă noapte", "trafic noapte")),
        ("crickets_night", ("greieri noapte", "crickets night", "insecte noapte", "broaște")),
        ("wolf", ("lup", "wolf", "noapte", "pădure noapte", "urlat")),
        ("owl", ("bufniță", "owl", "noapte", "pădure noapte", "huhuz")),
    )
    preset = next(
        (name for name, words in presets if any(word in text for word in words)),
        "room",
    )
    return _ambient_wav(preset, duration=duration)


def mix_voice_ambient(voice_bytes, ambient_bytes, voice_vol=1.0, ambient_vol=0.5):
    """Combină o voce cu un sunet ambiental într-un singur fișier audio.
    
    Args:
        voice_bytes: Audio-ul vocii (bytes)
        ambient_bytes: Audio-ul ambiental (bytes)
        voice_vol: Volumul vocii (0.0 - 1.0)
        ambient_vol: Volumul ambiental (0.0 - 1.0)
    
    Returns:
        bytes: Audio combinat (WAV)
    """
    try:
        import numpy as np
        import soundfile as sf
        import io
        
        # Citește vocea
        voice_data, voice_sr = sf.read(io.BytesIO(voice_bytes))
        if len(voice_data.shape) > 1:
            voice_data = voice_data.mean(axis=1)  # Stereo -> Mono
        
        # Citește ambientul
        ambient_data, ambient_sr = sf.read(io.BytesIO(ambient_bytes))
        if len(ambient_data.shape) > 1:
            ambient_data = ambient_data.mean(axis=1)
        
        # Resample ambientul la rata vocii dacă e necesar
        if ambient_sr != voice_sr:
            ratio = voice_sr / ambient_sr
            new_len = int(len(ambient_data) * ratio)
            ambient_data = np.interp(
                np.linspace(0, len(ambient_data) - 1, new_len),
                np.arange(len(ambient_data)),
                ambient_data
            )
        
        # Extinde ambientul pentru a acoperi toată durata vocii
        voice_len = len(voice_data)
        ambient_len = len(ambient_data)
        
        if ambient_len < voice_len:
            # Bucla ambientul
            repeats = (voice_len // ambient_len) + 2
            ambient_extended = np.tile(ambient_data, repeats)
            ambient_final = ambient_extended[:voice_len]
        else:
            ambient_final = ambient_data[:voice_len]
        
        # Normalizează
        voice_norm = voice_data / (np.max(np.abs(voice_data)) + 1e-9)
        ambient_norm = ambient_final / (np.max(np.abs(ambient_final)) + 1e-9)
        
        # Mix
        mixed = voice_norm * voice_vol + ambient_norm * ambient_vol
        # Limitează la [-1, 1]
        mixed = np.clip(mixed, -1.0, 1.0)
        
        # Salvează
        output = io.BytesIO()
        sf.write(output, mixed.astype(np.float32), voice_sr, format='WAV')
        return output.getvalue()
        
    except ImportError:
        # Fallback: returnează doar vocea dacă nu avem numpy
        return voice_bytes
    except Exception as e:
        print(f"⚠️ Eroare mix: {e}")
        return voice_bytes


def generate_ambient(name_or_category, duration=12.0):
    """Generează un sunet ambiental bazat pe nume sau categorie.
    
    Args:
        name_or_category: Numele sau categoria sunetului
        duration: Durata în secunde
    
    Returns:
        bytes: Audio-ul generat
    """
    text = str(name_or_category or "").lower()
    return sound_effect(text, duration=duration)
