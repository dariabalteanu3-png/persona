export type VoiceSettings = {
  voice_name: string;
  voice_lang: string;
  voice_pitch: number;
  voice_rate: number;
  voice_warmth: number;
};

let cachedVoices: SpeechSynthesisVoice[] = [];

export function loadVoices(): Promise<SpeechSynthesisVoice[]> {
  return new Promise((resolve) => {
    const existing = window.speechSynthesis.getVoices();
    if (existing.length) {
      cachedVoices = existing;
      resolve(existing);
      return;
    }
    let resolved = false;
    const handler = () => {
      if (resolved) return;
      resolved = true;
      cachedVoices = window.speechSynthesis.getVoices();
      resolve(cachedVoices);
    };
    window.speechSynthesis.onvoiceschanged = handler;
    // fallback timeout
    setTimeout(handler, 1000);
  });
}

export function getVoicesForLang(lang: string): SpeechSynthesisVoice[] {
  const voices = cachedVoices.length ? cachedVoices : window.speechSynthesis.getVoices();
  const baseLang = lang.split('-')[0];
  const matches = voices.filter((v) => v.lang.toLowerCase().startsWith(baseLang.toLowerCase()));
  return matches.length ? matches : voices;
}

export function pickVoice(settings: VoiceSettings): SpeechSynthesisVoice | null {
  const all = getVoicesForLang(settings.voice_lang);
  if (!all.length) return null;
  if (settings.voice_name) {
    const named = all.find((v) => v.name === settings.voice_name);
    if (named) return named;
  }
  // pick based on warmth: higher warmth → prefer female/local voices heuristic
  return all[Math.floor(settings.voice_warmth * all.length) % all.length] ?? all[0];
}

export function speak(text: string, settings: VoiceSettings): void {
  if (!('speechSynthesis' in window)) return;
  window.speechSynthesis.cancel();
  // strip emoji and markdown for cleaner speech
  const clean = text
    .replace(/[\u{1F000}-\u{1FFFF}\u{2600}-\u{27BF}]/gu, '')
    .replace(/[*_~`#>]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (!clean) return;
  const utter = new SpeechSynthesisUtterance(clean);
  const voice = pickVoice(settings);
  if (voice) utter.voice = voice;
  utter.lang = settings.voice_lang || voice?.lang || 'en-US';
  utter.pitch = settings.voice_pitch;
  utter.rate = settings.voice_rate;
  window.speechSynthesis.speak(utter);
}

export function stopSpeaking(): void {
  if ('speechSynthesis' in window) window.speechSynthesis.cancel();
}
