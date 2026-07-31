import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from '@/context/RouterContext';
import { colorPresets, getColor } from '@/lib/colors';
import { characterTemplates, characterCategories } from '@/lib/templates';
import { ambientCategories, allAmbientSounds, getAmbientSound, totalAmbientCount } from '@/lib/ambient';
import { loadVoices, getVoicesForLang } from '@/lib/voice';
import type { Character } from '@/types/database';
import { ArrowLeft, Save, Trash2, Loader2, Eye, Volume2, Sparkles, Wand2, Search, X } from 'lucide-react';

type FormState = {
  name: string;
  tagline: string;
  personality: string;
  scenario: string;
  greeting: string;
  description: string;
  avatar_emoji: string;
  avatar_color: string;
  voice_lang: string;
  voice_pitch: number;
  voice_rate: number;
  voice_warmth: number;
  ambient_mood: string;
  is_public: boolean;
  category: string;
  tags: string;
};

const EMOJI_CHOICES = ['🎭', '🌙', '🚀', '🧘', '🕵️', '🐕', '🤖', '👵', '🏄', '🦊', '🐱', '🐉', '🧙', '👽', '👻', '🎨', '📚', '🎸', '🌸', '⚡', '🔥', '❄️', '🌊', '⭐'];

const emptyForm: FormState = {
  name: '',
  tagline: '',
  personality: '',
  scenario: '',
  greeting: '',
  description: '',
  avatar_emoji: '🎭',
  avatar_color: 'rose',
  voice_lang: 'en-US',
  voice_pitch: 1.0,
  voice_rate: 1.0,
  voice_warmth: 0.5,
  ambient_mood: 'none',
  is_public: false,
  category: 'personal',
  tags: '',
};

export function CharacterEditor({ characterId }: { characterId?: string }) {
  const { user } = useAuth();
  const { navigate } = useRouter();
  const [form, setForm] = useState<FormState>(emptyForm);
  const [loading, setLoading] = useState(!!characterId);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [testingVoice, setTestingVoice] = useState(false);
  const [ambientSearch, setAmbientSearch] = useState('');
  const [ambientCat, setAmbientCat] = useState('all');

  const filteredAmbient = ambientCat === 'all' && !ambientSearch
    ? ambientCategories.flatMap((c) => c.sounds)
    : ambientSearch
      ? allAmbientSounds.filter((s) =>
          s.name.toLowerCase().includes(ambientSearch.toLowerCase()) ||
          s.description.toLowerCase().includes(ambientSearch.toLowerCase()) ||
          s.category.toLowerCase().includes(ambientSearch.toLowerCase())
        )
      : (ambientCategories.find((c) => c.id === ambientCat)?.sounds ?? []);

  useEffect(() => {
    loadVoices().then(setVoices);
  }, []);

  const loadCharacter = useCallback(async () => {
    if (!characterId) { setLoading(false); return; }
    const { data } = await supabase
      .from('characters')
      .select('*')
      .eq('id', characterId)
      .maybeSingle();
    if (data) {
      const c = data as Character;
      setForm({
        name: c.name,
        tagline: c.tagline,
        personality: c.personality,
        scenario: c.scenario,
        greeting: c.greeting,
        description: c.description,
        avatar_emoji: c.avatar_emoji,
        avatar_color: c.avatar_color,
        voice_lang: c.voice_lang,
        voice_pitch: c.voice_pitch,
        voice_rate: c.voice_rate,
        voice_warmth: c.voice_warmth,
        ambient_mood: c.ambient_mood,
        is_public: c.is_public,
        category: c.category,
        tags: c.tags.join(', '),
      });
    }
    setLoading(false);
  }, [characterId]);

  useEffect(() => {
    loadCharacter();
  }, [loadCharacter]);

  const update = (field: keyof FormState, value: string | number | boolean) => {
    setForm((f) => ({ ...f, [field]: value }));
  };

  const testVoice = () => {
    setTestingVoice(true);
    const utter = new SpeechSynthesisUtterance(
      form.greeting || `Hello, I am ${form.name || 'your character'}. This is how I'll sound.`
    );
    const voiceList = getVoicesForLang(form.voice_lang);
    if (voiceList.length) {
      const idx = Math.floor(form.voice_warmth * voiceList.length) % voiceList.length;
      utter.voice = voiceList[idx];
    }
    utter.lang = form.voice_lang;
    utter.pitch = form.voice_pitch;
    utter.rate = form.voice_rate;
    utter.onend = () => setTestingVoice(false);
    utter.onerror = () => setTestingVoice(false);
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(utter);
  };

  const applyTemplate = (idx: number) => {
    const tpl = characterTemplates[idx];
    setForm({
      name: tpl.name,
      tagline: tpl.tagline,
      personality: tpl.personality,
      scenario: tpl.scenario,
      greeting: tpl.greeting,
      description: tpl.description,
      avatar_emoji: tpl.avatar_emoji,
      avatar_color: tpl.avatar_color,
      voice_lang: tpl.voice_lang,
      voice_pitch: tpl.voice_pitch,
      voice_rate: tpl.voice_rate,
      voice_warmth: tpl.voice_warmth,
      ambient_mood: tpl.ambient_mood,
      is_public: false,
      category: tpl.category,
      tags: tpl.tags.join(', '),
    });
  };

  const save = async () => {
    if (!user) return;
    if (!form.name.trim()) { setError('Please give your character a name.'); return; }
    setSaving(true);
    setError(null);

    const payload = {
      user_id: user.id,
      name: form.name.trim(),
      tagline: form.tagline.trim(),
      personality: form.personality.trim(),
      scenario: form.scenario.trim(),
      greeting: form.greeting.trim(),
      description: form.description.trim(),
      avatar_emoji: form.avatar_emoji,
      avatar_color: form.avatar_color,
      voice_lang: form.voice_lang,
      voice_pitch: form.voice_pitch,
      voice_rate: form.voice_rate,
      voice_warmth: form.voice_warmth,
      ambient_mood: form.ambient_mood,
      is_public: form.is_public,
      category: form.category,
      tags: form.tags.split(',').map((t) => t.trim()).filter(Boolean),
    };

    if (characterId) {
      const { error: err } = await supabase
        .from('characters')
        .update({ ...payload, updated_at: new Date().toISOString() })
        .eq('id', characterId);
      if (err) setError(err.message);
      else navigate({ name: 'home' });
    } else {
      const { data, error: err } = await supabase
        .from('characters')
        .insert(payload)
        .select()
        .single();
      if (err) setError(err.message);
      else if (data) navigate({ name: 'chat', characterId: (data as Character).id });
    }
    setSaving(false);
  };

  const remove = async () => {
    if (!characterId) return;
    if (!confirm(`Delete "${form.name}"? This will also delete all conversations with this character. This cannot be undone.`)) return;
    setSaving(true);
    const { error: err } = await supabase.from('characters').delete().eq('id', characterId);
    if (err) { setError(err.message); setSaving(false); }
    else navigate({ name: 'home' });
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-rose-400" />
      </div>
    );
  }

  const color = getColor(form.avatar_color);

  return (
    <div className="max-w-3xl mx-auto space-y-6 pb-12">
      {/* Header */}
      <div className="flex items-center justify-between">
        <button onClick={() => navigate({ name: 'home' })} className="btn-ghost">
          <ArrowLeft className="w-4 h-4" /> Back
        </button>
        <div className="flex items-center gap-2">
          {characterId && (
            <button onClick={remove} disabled={saving} className="btn-ghost text-rose-400 hover:bg-rose-500/10">
              <Trash2 className="w-4 h-4" /> Delete
            </button>
          )}
          <button onClick={save} disabled={saving} className="btn-primary">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            {characterId ? 'Save changes' : 'Create & chat'}
          </button>
        </div>
      </div>

      {/* Templates */}
      {!characterId && (
        <div className="card p-4">
          <div className="flex items-center gap-2 mb-3">
            <Wand2 className="w-4 h-4 text-amber-400" />
            <h3 className="text-sm font-semibold text-white">Quick start — pick a template</h3>
          </div>
          <div className="flex gap-2 overflow-x-auto scrollbar-none pb-1">
            {characterTemplates.map((tpl, i) => {
              const tc = getColor(tpl.avatar_color);
              return (
                <button
                  key={tpl.name}
                  onClick={() => applyTemplate(i)}
                  className="shrink-0 flex items-center gap-2 px-3 py-2 rounded-xl bg-white/[0.03] border border-white/[0.06] hover:bg-white/[0.06] hover:border-white/15 transition-all"
                >
                  <span className={`w-7 h-7 rounded-lg bg-gradient-to-br ${tc.from} ${tc.to} flex items-center justify-center text-sm`}>
                    {tpl.avatar_emoji}
                  </span>
                  <span className="text-sm text-zinc-300 whitespace-nowrap">{tpl.name}</span>
                </button>
              );
            })}
          </div>
        </div>
      )}

      {error && (
        <div className="rounded-xl bg-rose-500/10 border border-rose-500/20 px-4 py-3 text-sm text-rose-300">
          {error}
        </div>
      )}

      {/* Preview card */}
      <div className="card p-5 flex items-center gap-4">
        <div className={`w-16 h-16 rounded-2xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-3xl shadow-lg`}>
          {form.avatar_emoji}
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-xl font-bold text-white truncate">{form.name || 'Untitled character'}</h2>
          <p className="text-sm text-zinc-400 truncate">{form.tagline || 'Add a tagline...'}</p>
        </div>
        <button
          onClick={testVoice}
          disabled={testingVoice}
          className="btn-outline"
          title="Preview voice"
        >
          {testingVoice ? <Loader2 className="w-4 h-4 animate-spin" /> : <Volume2 className="w-4 h-4" />}
          <span className="hidden sm:inline">Test voice</span>
        </button>
      </div>

      {/* Basic info */}
      <Section title="Identity" icon={<Sparkles className="w-4 h-4 text-rose-400" />}>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label-text">Name *</label>
            <input className="input-field" value={form.name} onChange={(e) => update('name', e.target.value)} placeholder="e.g. Luna" />
          </div>
          <div>
            <label className="label-text">Tagline</label>
            <input className="input-field" value={form.tagline} onChange={(e) => update('tagline', e.target.value)} placeholder="A short one-liner" />
          </div>
        </div>

        <div>
          <label className="label-text">Description</label>
          <textarea className="input-field min-h-[70px] resize-y" value={form.description} onChange={(e) => update('description', e.target.value)} placeholder="Who is this character? A brief summary." />
        </div>

        {/* Avatar emoji picker */}
        <div>
          <label className="label-text">Avatar</label>
          <div className="flex flex-wrap gap-2">
            {EMOJI_CHOICES.map((emoji) => (
              <button
                key={emoji}
                onClick={() => update('avatar_emoji', emoji)}
                className={`w-10 h-10 rounded-xl flex items-center justify-center text-lg transition-all ${
                  form.avatar_emoji === emoji ? 'bg-white/15 ring-2 ring-rose-500/50 scale-105' : 'bg-white/[0.04] hover:bg-white/[0.08]'
                }`}
              >
                {emoji}
              </button>
            ))}
          </div>
        </div>

        {/* Color picker */}
        <div>
          <label className="label-text">Theme color</label>
          <div className="flex flex-wrap gap-2">
            {colorPresets.map((preset) => (
              <button
                key={preset.id}
                onClick={() => update('avatar_color', preset.id)}
                className={`w-10 h-10 rounded-xl bg-gradient-to-br ${preset.from} ${preset.to} transition-all ${
                  form.avatar_color === preset.id ? 'ring-2 ring-white scale-105' : 'hover:scale-105'
                }`}
                title={preset.name}
              />
            ))}
          </div>
        </div>

        {/* Category + tags */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label-text">Category</label>
            <select className="input-field" value={form.category} onChange={(e) => update('category', e.target.value)}>
              {characterCategories.map((cat) => (
                <option key={cat.id} value={cat.id} className="bg-zinc-900">{cat.icon} {cat.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="label-text">Tags (comma separated)</label>
            <input className="input-field" value={form.tags} onChange={(e) => update('tags', e.target.value)} placeholder="comfort, night, wise" />
          </div>
        </div>

        {/* Public toggle */}
        <label className="flex items-center gap-3 cursor-pointer p-3 rounded-xl bg-white/[0.03] border border-white/[0.06] hover:bg-white/[0.05] transition-all">
          <button
            type="button"
            onClick={() => update('is_public', !form.is_public)}
            className={`relative w-11 h-6 rounded-full transition-all ${form.is_public ? 'bg-rose-500' : 'bg-white/10'}`}
          >
            <span className={`absolute top-0.5 w-5 h-5 rounded-full bg-white transition-all ${form.is_public ? 'left-[22px]' : 'left-0.5'}`} />
          </button>
          <div>
            <p className="text-sm font-medium text-white">Share publicly</p>
            <p className="text-xs text-zinc-500">Others can discover and chat with this character</p>
          </div>
        </label>
      </Section>

      {/* Personality */}
      <Section title="Personality" icon={<Eye className="w-4 h-4 text-amber-400" />}>
        <div>
          <label className="label-text">Personality</label>
          <textarea className="input-field min-h-[100px] resize-y" value={form.personality} onChange={(e) => update('personality', e.target.value)} placeholder="Describe how they think, speak, and behave. e.g. 'Warm, empathetic, endlessly curious. Speaks with a soft, poetic cadence.'" />
        </div>
        <div>
          <label className="label-text">Scenario / Setting</label>
          <textarea className="input-field min-h-[80px] resize-y" value={form.scenario} onChange={(e) => update('scenario', e.target.value)} placeholder="Where are you meeting them? e.g. 'A cozy stargazing night on a hilltop.'" />
        </div>
        <div>
          <label className="label-text">Greeting</label>
          <textarea className="input-field min-h-[70px] resize-y" value={form.greeting} onChange={(e) => update('greeting', e.target.value)} placeholder="The first thing they say when you start a conversation." />
        </div>
      </Section>

      {/* Voice */}
      <Section title="Voice" icon={<Volume2 className="w-4 h-4 text-sky-400" />}>
        <p className="text-xs text-zinc-500 -mt-2 mb-4">Characters speak using your browser's built-in voices. Adjust the settings to shape how they sound.</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <label className="label-text">Language</label>
            <select className="input-field" value={form.voice_lang} onChange={(e) => update('voice_lang', e.target.value)}>
              <option value="en-US" className="bg-zinc-900">English (US)</option>
              <option value="en-GB" className="bg-zinc-900">English (UK)</option>
              <option value="ro-RO" className="bg-zinc-900">Romanian</option>
              <option value="es-ES" className="bg-zinc-900">Spanish</option>
              <option value="fr-FR" className="bg-zinc-900">French</option>
              <option value="de-DE" className="bg-zinc-900">German</option>
              <option value="it-IT" className="bg-zinc-900">Italian</option>
              <option value="ja-JP" className="bg-zinc-900">Japanese</option>
              <option value="pt-BR" className="bg-zinc-900">Portuguese (BR)</option>
              <option value="ru-RU" className="bg-zinc-900">Russian</option>
            </select>
          </div>
          <div className="flex items-end">
            <p className="text-xs text-zinc-500">
              {voices.filter((v) => v.lang.startsWith(form.voice_lang.split('-')[0])).length} voice(s) available for this language
            </p>
          </div>
        </div>

        <Slider label="Pitch" value={form.voice_pitch} min={0.5} max={2} step={0.05} onChange={(v) => update('voice_pitch', v)} format={(v) => v.toFixed(2)} />
        <Slider label="Speed" value={form.voice_rate} min={0.5} max={2} step={0.05} onChange={(v) => update('voice_rate', v)} format={(v) => `${v.toFixed(2)}x`} />
        <Slider label="Warmth" value={form.voice_warmth} min={0} max={1} step={0.05} onChange={(v) => update('voice_warmth', v)} format={(v) => v < 0.33 ? 'Cool' : v < 0.66 ? 'Warm' : 'Hot'} />
      </Section>

      {/* Ambient mood */}
      <Section title="Ambient Mood" icon={<span className="text-base">🌊</span>}>
        <p className="text-xs text-zinc-500 -mt-2 mb-4">Pick a background sound that plays when you chat with this character. {totalAmbientCount} sounds available.</p>

        {/* Search */}
        <div className="relative mb-3">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <input
            type="text"
            value={ambientSearch}
            onChange={(e) => { setAmbientSearch(e.target.value); setAmbientCat(e.target.value ? 'all' : ambientCat); }}
            placeholder="Search sounds..."
            className="input-field pl-10 py-2 text-sm"
          />
        </div>

        {/* Category tabs */}
        <div className="flex gap-1.5 mb-3 overflow-x-auto scrollbar-none pb-1">
          <button
            onClick={() => setAmbientCat('all')}
            className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-all ${ambientCat === 'all' ? 'bg-white/10 text-white border border-white/15' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}
          >
            All
          </button>
          {ambientCategories.map((cat) => (
            <button
              key={cat.id}
              onClick={() => { setAmbientCat(cat.id); setAmbientSearch(''); }}
              className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-all whitespace-nowrap ${ambientCat === cat.id ? 'bg-white/10 text-white border border-white/15' : 'text-zinc-400 hover:text-white hover:bg-white/5'}`}
            >
              {cat.icon} {cat.name}
            </button>
          ))}
        </div>

        {/* Selected sound preview */}
        {form.ambient_mood !== 'none' && (() => {
          const sel = getAmbientSound(form.ambient_mood);
          if (!sel) return null;
          return (
            <div className="flex items-center gap-2 mb-3 px-3 py-2 rounded-xl bg-rose-500/10 border border-rose-500/20">
              <span className="text-lg">{sel.icon}</span>
              <span className="text-sm text-white font-medium">{sel.name}</span>
              <span className="text-xs text-zinc-500 ml-auto">{sel.description}</span>
              <button
                onClick={() => update('ambient_mood', 'none')}
                className="w-6 h-6 rounded-lg flex items-center justify-center text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          );
        })()}

        {/* Sound grid */}
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 max-h-64 overflow-y-auto pr-1">
          <button
            onClick={() => update('ambient_mood', 'none')}
            className={`px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${form.ambient_mood === 'none' ? 'bg-white/10 text-white border border-white/15' : 'bg-white/[0.03] text-zinc-400 border border-transparent hover:bg-white/[0.06]'}`}
          >
            🔇 None
          </button>
          {filteredAmbient.map((sound) => (
            <button
              key={sound.id}
              onClick={() => update('ambient_mood', sound.id)}
              className={`px-3 py-2.5 rounded-xl text-sm font-medium transition-all flex items-center gap-1.5 ${form.ambient_mood === sound.id ? 'bg-rose-500/15 text-rose-300 border border-rose-500/30' : 'bg-white/[0.03] text-zinc-400 border border-transparent hover:bg-white/[0.06]'}`}
            >
              <span className="shrink-0">{sound.icon}</span>
              <span className="truncate">{sound.name}</span>
            </button>
          ))}
        </div>
      </Section>
    </div>
  );
}

function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="card p-6 space-y-4">
      <div className="flex items-center gap-2">
        {icon}
        <h3 className="font-semibold text-white">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function Slider({ label, value, min, max, step, onChange, format }: {
  label: string; value: number; min: number; max: number; step: number;
  onChange: (v: number) => void; format: (v: number) => string;
}) {
  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <label className="label-text mb-0">{label}</label>
        <span className="text-xs text-zinc-400 font-medium">{format(value)}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full accent-rose-500"
      />
    </div>
  );
}
