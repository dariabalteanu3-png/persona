"""Generează tonuri ORIGINALE (fără drepturi de autor) pentru temele de sunet.
Sintetizate cu numpy → WAV (redate în orice browser). Rulează o singură dată:
    python _gen_sounds.py
Produce, în assets/, sunete DISTINCTE pentru două teme:
  - iPhone  = cristalin, luminos, clopoței (sine + octavă)
  - Samsung = cald, marimba, note mai joase (tri)
Fiecare temă: _notification / _send / _ringtone.
"""
import os
import wave
import numpy as np

SR = 44100
ASSETS = os.path.join(os.path.dirname(__file__), "assets")

NOTE = {  # frecvențe (Hz)
    "A4": 440.0, "C5": 523.25, "D5": 587.33, "E5": 659.25, "F5": 698.46,
    "G5": 783.99, "A5": 880.0, "B5": 987.77, "C6": 1046.5, "D6": 1174.66,
    "E6": 1318.5, "G6": 1568.0,
}


def _osc(freq, t, kind="sine"):
    if kind == "square":
        return np.sign(np.sin(2 * np.pi * freq * t)) * 0.6
    if kind == "tri":
        return 2 * np.abs(2 * (freq * t - np.floor(freq * t + 0.5))) - 1
    return np.sin(2 * np.pi * freq * t)


def note(freq, dur, kind="sine", decay=6.0, vol=0.55, attack=0.005):
    n = int(SR * dur)
    t = np.linspace(0, dur, n, False)
    y = _osc(freq, t, kind)
    # octavă blândă pentru căldură/strălucire pe clopoței/marimba
    if kind in ("sine", "tri"):
        y = y + 0.25 * _osc(freq * 2, t, "sine")
    env = np.exp(-decay * t)
    a = int(SR * attack)
    if a > 0:
        env[:a] *= np.linspace(0, 1, a)
    return y * env * vol


def sweep(f0, f1, dur, kind="sine", vol=0.5, decay=8.0):
    n = int(SR * dur)
    t = np.linspace(0, dur, n, False)
    freqs = np.linspace(f0, f1, n)
    phase = 2 * np.pi * np.cumsum(freqs) / SR
    y = np.sin(phase) if kind == "sine" else np.sign(np.sin(phase)) * 0.6
    return y * np.exp(-decay * t) * vol


def sil(dur):
    return np.zeros(int(SR * dur))


def save(name, parts):
    y = np.concatenate(parts) if isinstance(parts, list) else parts
    y = np.clip(y, -1, 1)
    pcm = (y * 32767).astype("<i2")
    with wave.open(os.path.join(ASSETS, name), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print("wrote", name, f"{len(y) / SR:.2f}s")


def ring(seqn, gap=0.10, reps=3, tail=0.35, rep_gap=0.28):
    block = []
    for f, d, kind in seqn:
        block.append(note(NOTE[f], d, kind))
        block.append(sil(gap))
    one = np.concatenate(block)
    out = []
    for _ in range(reps):
        out.append(one)
        out.append(sil(rep_gap))
    out.append(sil(tail))
    return out


# ============================================================
# iPhone = CRISTALIN, luminos, clopoței (sine + octavă), note înalte
# ============================================================
save("iphone_notification.wav", [
    note(NOTE["C6"], 0.26, "sine", 5.0),
    note(NOTE["E6"], 0.55, "sine", 4.0),
])
save("iphone_send.wav", [
    note(NOTE["E6"], 0.10, "sine", 14.0, vol=0.42),
])
save("iphone_ringtone.wav", ring([
    ("C6", 0.16, "sine"), ("E6", 0.16, "sine"), ("G6", 0.30, "sine"),
], reps=3))

# ============================================================
# Samsung = CALD, marimba (tri), note mai joase și rotunde
# ============================================================
save("samsung_notification.wav", [
    note(NOTE["E5"], 0.16, "tri", 8.0),
    note(NOTE["B5"], 0.45, "tri", 5.0),
])
save("samsung_send.wav", [
    note(NOTE["B5"], 0.09, "tri", 16.0, vol=0.45),
])
save("samsung_ringtone.wav", ring([
    ("E5", 0.15, "tri"), ("G5", 0.15, "tri"), ("B5", 0.15, "tri"), ("D6", 0.28, "tri"),
], reps=3))

print("DONE")
