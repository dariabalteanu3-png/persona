import { useState, useEffect, useRef } from 'react';
import { audioEngine } from '@/lib/audioEngine';
import { ambientCategories, getAmbientSound, totalAmbientCount } from '@/lib/ambient';
import { Volume2, VolumeX, X, Waves, Play, Square } from 'lucide-react';

type MixState = Record<string, { playing: boolean; volume: number }>;

export function AmbientMixer({ compact = false }: { compact?: boolean }) {
  const [mix, setMix] = useState<MixState>({});
  const [open, setOpen] = useState(false);
  const [activeCategory, setActiveCategory] = useState<string>('nature');
  const [masterOn, setMasterOn] = useState(true);
  const [masterVol, setMasterVol] = useState(0.6);
  const prevMixRef = useRef<MixState>({});

  useEffect(() => {
    const activeIds = audioEngine.getActiveIds();
    if (activeIds.length) {
      const restored: MixState = {};
      activeIds.forEach((id) => {
        restored[id] = { playing: true, volume: masterVol };
      });
      setMix(restored);
    }
    return () => {
      // keep audio running across navigation; don't stop on unmount
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleSound = async (soundId: string) => {
    const current = mix[soundId];
    if (current?.playing) {
      audioEngine.stop(soundId);
      setMix((m) => ({ ...m, [soundId]: { ...m[soundId], playing: false } }));
    } else {
      const vol = (mix[soundId]?.volume ?? 0.5) * masterVol;
      await audioEngine.play(soundId, vol);
      setMix((m) => ({ ...m, [soundId]: { playing: true, volume: m[soundId]?.volume ?? 0.5 } }));
    }
  };

  const setSoundVolume = (soundId: string, volume: number) => {
    setMix((m) => ({ ...m, [soundId]: { ...m[soundId], volume } }));
    if (mix[soundId]?.playing) {
      audioEngine.setVolume(soundId, volume * masterVol);
    }
  };

  const stopAll = () => {
    audioEngine.stopAll();
    setMix({});
  };

  const toggleMaster = () => {
    const next = !masterOn;
    setMasterOn(next);
    if (next) {
      Object.entries(mix).forEach(([id, state]) => {
        if (state.playing) audioEngine.setVolume(id, state.volume * masterVol);
      });
    } else {
      Object.entries(mix).forEach(([id, state]) => {
        if (state.playing) audioEngine.setVolume(id, 0);
      });
    }
  };

  const activeCount = Object.values(mix).filter((m) => m.playing).length;

  if (compact) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="relative btn-ghost"
        title="Ambient sounds mixer"
      >
        <Waves className="w-4 h-4" />
        <span className="hidden sm:inline">Sounds</span>
        {activeCount > 0 && (
          <span className="absolute -top-1 -right-1 w-5 h-5 rounded-full bg-rose-500 text-white text-[10px] font-bold flex items-center justify-center animate-scale-in">
            {activeCount}
          </span>
        )}
      </button>
    );
  }

  const currentSounds = ambientCategories.find((c) => c.id === activeCategory)?.sounds ?? [];

  return (
    <>
      {/* Floating button */}
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-40 w-14 h-14 rounded-2xl bg-gradient-to-br from-rose-500 to-pink-600 shadow-xl shadow-rose-500/30 flex items-center justify-center text-white hover:scale-105 active:scale-95 transition-transform"
        title="Ambient sounds"
      >
        <Waves className="w-6 h-6" />
        {activeCount > 0 && (
          <span className="absolute -top-1 -right-1 min-w-5 h-5 px-1 rounded-full bg-white text-rose-600 text-[10px] font-bold flex items-center justify-center animate-scale-in">
            {activeCount}
          </span>
        )}
      </button>

      {/* Panel */}
      {open && (
        <div className="fixed inset-0 z-50 flex items-end sm:items-center justify-center p-0 sm:p-4 animate-fade-in">
          <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={() => setOpen(false)} />
          <div className="relative w-full max-w-3xl max-h-[85vh] glass-strong rounded-t-3xl sm:rounded-3xl overflow-hidden animate-slide-in-right flex flex-col">
            {/* Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-white/[0.06]">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-rose-500/20 to-pink-600/20 flex items-center justify-center">
                  <Waves className="w-5 h-5 text-rose-400" />
                </div>
                <div>
                  <h3 className="font-semibold text-white">Ambient Soundscapes</h3>
                  <p className="text-xs text-zinc-400">{totalAmbientCount} sounds · {activeCount} playing</p>
                </div>
              </div>
              <div className="flex items-center gap-2">
                {activeCount > 0 && (
                  <button onClick={stopAll} className="btn-ghost text-xs">
                    <Square className="w-3.5 h-3.5" /> Stop all
                  </button>
                )}
                <button onClick={() => setOpen(false)} className="btn-ghost">
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Master volume */}
            <div className="px-6 py-3 border-b border-white/[0.06] flex items-center gap-4">
              <button onClick={toggleMaster} className={`w-10 h-10 rounded-xl flex items-center justify-center transition-all ${masterOn ? 'bg-rose-500/20 text-rose-400' : 'bg-white/5 text-zinc-500'}`}>
                {masterOn ? <Volume2 className="w-5 h-5" /> : <VolumeX className="w-5 h-5" />}
              </button>
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={masterVol}
                onChange={(e) => {
                  const v = parseFloat(e.target.value);
                  setMasterVol(v);
                  Object.entries(mix).forEach(([id, state]) => {
                    if (state.playing) audioEngine.setVolume(id, state.volume * v);
                  });
                }}
                className="flex-1 accent-rose-500"
              />
              <span className="text-xs text-zinc-400 w-10 text-right">{Math.round(masterVol * 100)}%</span>
            </div>

            {/* Categories */}
            <div className="px-6 pt-4 pb-2 flex gap-2 overflow-x-auto scrollbar-none">
              {ambientCategories.map((cat) => (
                <button
                  key={cat.id}
                  onClick={() => setActiveCategory(cat.id)}
                  className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-all whitespace-nowrap ${
                    activeCategory === cat.id
                      ? 'bg-white/10 text-white border border-white/15'
                      : 'text-zinc-400 hover:text-white hover:bg-white/5'
                  }`}
                >
                  {cat.icon} {cat.name}
                </button>
              ))}
            </div>

            {/* Sounds grid */}
            <div className="flex-1 overflow-y-auto px-6 py-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {currentSounds.map((sound) => {
                  const state = mix[sound.id];
                  const isPlaying = state?.playing;
                  return (
                    <div
                      key={sound.id}
                      className={`rounded-2xl border p-4 transition-all cursor-pointer ${
                        isPlaying
                          ? 'bg-rose-500/10 border-rose-500/30'
                          : 'bg-white/[0.03] border-white/[0.06] hover:bg-white/[0.05]'
                      }`}
                      onClick={() => toggleSound(sound.id)}
                    >
                      <div className="flex items-center justify-between mb-2">
                        <div className="flex items-center gap-3">
                          <div className={`w-10 h-10 rounded-xl flex items-center justify-center text-lg transition-all ${isPlaying ? 'bg-rose-500/20 scale-110' : 'bg-white/5'}`}>
                            {sound.icon}
                          </div>
                          <div>
                            <p className="text-sm font-medium text-white">{sound.name}</p>
                            <p className="text-xs text-zinc-500">{sound.description}</p>
                          </div>
                        </div>
                        <button className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all ${isPlaying ? 'bg-rose-500 text-white' : 'bg-white/5 text-zinc-400'}`}>
                          {isPlaying ? <Square className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5 ml-0.5" />}
                        </button>
                      </div>
                      {isPlaying && (
                        <div className="mt-2 animate-fade-in" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="range"
                            min={0}
                            max={1}
                            step={0.05}
                            value={state?.volume ?? 0.5}
                            onChange={(e) => setSoundVolume(sound.id, parseFloat(e.target.value))}
                            className="w-full accent-rose-500 h-1.5"
                          />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
