import { getAmbientSound, type AmbientSound } from '@/lib/ambient';

type ActiveTrack = {
  sound: AmbientSound;
  gain: GainNode;
  source: AudioNode;
  lfo?: OscillatorNode;
  lfoGain?: GainNode;
};

class AudioEngine {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private active: Map<string, ActiveTrack> = new Map();

  private ensureContext(): AudioContext {
    if (!this.ctx) {
      this.ctx = new AudioContext();
      this.masterGain = this.ctx.createGain();
      this.masterGain.gain.value = 0.8;
      this.masterGain.connect(this.ctx.destination);
    }
    if (this.ctx.state === 'suspended') {
      this.ctx.resume();
    }
    return this.ctx;
  }

  async play(soundId: string, volume: number = 0.5): Promise<void> {
    if (this.active.has(soundId)) {
      this.setVolume(soundId, volume);
      return;
    }
    const sound = getAmbientSound(soundId);
    if (!sound) return;
    const ctx = this.ensureContext();
    const gain = ctx.createGain();
    gain.gain.value = 0;
    gain.connect(this.masterGain!);

    let source: AudioNode;
    let lfo: OscillatorNode | undefined;
    let lfoGain: GainNode | undefined;

    if (sound.type === 'rain') {
      // Rain: filtered noise with faster modulation for a pattering feel
      const bufferSize = 2 * ctx.sampleRate;
      const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        data[i] = (Math.random() * 2 - 1) * 0.8;
      }
      const noise = ctx.createBufferSource();
      noise.buffer = buffer;
      noise.loop = true;
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = sound.filter ?? 1200;
      noise.connect(filter);
      filter.connect(gain);
      noise.start();
      source = noise;

      lfo = ctx.createOscillator();
      lfo.frequency.value = sound.lfoRate ?? 0.5;
      lfoGain = ctx.createGain();
      lfoGain.gain.value = sound.lfoDepth ?? 0.15;
      lfo.connect(lfoGain);
      lfoGain.connect(gain.gain);
      lfo.start();
    } else if (sound.type === 'wind') {
      // Wind: noise with a resonant bandpass that sweeps, giving a howling feel
      const bufferSize = 2 * ctx.sampleRate;
      const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        data[i] = Math.random() * 2 - 1;
      }
      const noise = ctx.createBufferSource();
      noise.buffer = buffer;
      noise.loop = true;
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.frequency.value = sound.filter ?? 600;
      filter.Q.value = sound.filterQ ?? 1.5;
      noise.connect(filter);
      filter.connect(gain);
      noise.start();
      source = noise;

      // sweep the filter frequency for a howling effect
      lfo = ctx.createOscillator();
      lfo.frequency.value = sound.lfoRate ?? 0.15;
      lfoGain = ctx.createGain();
      lfoGain.gain.value = (sound.lfoDepth ?? 0.2) * (sound.filter ?? 600);
      lfo.connect(lfoGain);
      lfoGain.connect(filter.frequency);
      lfo.start();
    } else if (sound.type === 'drones') {
      // Drone: layered oscillators for a rich sustained tone
      const osc1 = ctx.createOscillator();
      osc1.type = 'sine';
      osc1.frequency.value = sound.freq ?? 110;
      const osc2 = ctx.createOscillator();
      osc2.type = 'triangle';
      osc2.frequency.value = (sound.freq ?? 110) * 1.005; // slight detune
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = sound.filter ?? 800;
      osc1.connect(filter);
      osc2.connect(filter);
      filter.connect(gain);
      osc1.start();
      osc2.start();
      source = osc1;

      lfo = ctx.createOscillator();
      lfo.frequency.value = sound.lfoRate ?? 0.05;
      lfoGain = ctx.createGain();
      lfoGain.gain.value = (sound.lfoDepth ?? 0.1) * (sound.freq ?? 110);
      lfo.connect(lfoGain);
      lfoGain.connect(osc2.frequency);
      lfo.start();
    } else if (sound.type === 'noise') {
      const bufferSize = 2 * ctx.sampleRate;
      const buffer = ctx.createBuffer(1, bufferSize, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < bufferSize; i++) {
        data[i] = Math.random() * 2 - 1;
      }
      const noise = ctx.createBufferSource();
      noise.buffer = buffer;
      noise.loop = true;
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = sound.filter ?? 1000;
      noise.connect(filter);
      filter.connect(gain);
      noise.start();
      source = noise;

      // gentle amplitude modulation for organic feel
      lfo = ctx.createOscillator();
      lfo.frequency.value = 0.15 + Math.random() * 0.2;
      lfoGain = ctx.createGain();
      lfoGain.gain.value = 0.15;
      lfo.connect(lfoGain);
      lfoGain.connect(gain.gain);
      lfo.start();
    } else if (sound.type === 'osc') {
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = sound.freq ?? 60;
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.value = 200;
      osc.connect(filter);
      filter.connect(gain);
      osc.start();
      source = osc;

      lfo = ctx.createOscillator();
      lfo.frequency.value = 0.08;
      lfoGain = ctx.createGain();
      lfoGain.gain.value = sound.freq ? sound.freq * 0.3 : 18;
      lfo.connect(lfoGain);
      lfoGain.connect(osc.frequency);
      lfo.start();
    } else {
      // chime — random bell-like tones
      const osc = ctx.createOscillator();
      osc.type = 'sine';
      osc.frequency.value = sound.freq ?? 880;
      const filter = ctx.createBiquadFilter();
      filter.type = 'bandpass';
      filter.frequency.value = sound.freq ?? 880;
      filter.Q.value = 2;
      osc.connect(filter);
      filter.connect(gain);
      osc.start();
      source = osc;

      // randomize frequency for chirp effect
      lfo = ctx.createOscillator();
      lfo.frequency.value = 0.3 + Math.random() * 0.5;
      lfoGain = ctx.createGain();
      lfoGain.gain.value = (sound.freq ?? 880) * 0.4;
      lfo.connect(lfoGain);
      lfoGain.connect(osc.frequency);
      lfo.start();
    }

    // fade in
    gain.gain.linearRampToValueAtTime(volume, ctx.currentTime + 0.8);

    this.active.set(soundId, { sound, gain, source, lfo, lfoGain });
  }

  stop(soundId: string): void {
    const track = this.active.get(soundId);
    if (!track || !this.ctx) return;
    const now = this.ctx.currentTime;
    track.gain.gain.cancelScheduledValues(now);
    track.gain.gain.setValueAtTime(track.gain.gain.value, now);
    track.gain.gain.linearRampToValueAtTime(0, now + 0.4);
    const source = track.source as AudioNode & { stop?: () => void };
    const lfo = track.lfo as (OscillatorNode & { stop?: () => void }) | undefined;
    setTimeout(() => {
      try {
        source.stop?.();
      } catch {
        // already stopped
      }
      try {
        lfo?.stop();
      } catch {
        // already stopped
      }
    }, 500);
    this.active.delete(soundId);
  }

  setVolume(soundId: string, volume: number): void {
    const track = this.active.get(soundId);
    if (!track || !this.ctx) return;
    track.gain.gain.linearRampToValueAtTime(volume, this.ctx.currentTime + 0.2);
  }

  stopAll(): void {
    for (const id of Array.from(this.active.keys())) {
      this.stop(id);
    }
  }

  isPlaying(soundId: string): boolean {
    return this.active.has(soundId);
  }

  getActiveIds(): string[] {
    return Array.from(this.active.keys());
  }
}

export const audioEngine = new AudioEngine();
