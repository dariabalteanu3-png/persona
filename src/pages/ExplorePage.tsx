import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from '@/context/RouterContext';
import { CharacterCard } from '@/components/CharacterCard';
import { characterTemplates, characterCategories } from '@/lib/templates';
import { getColor } from '@/lib/colors';
import type { Character, Favorite } from '@/types/database';
import { Loader2, Search, Compass, Plus, Sparkles } from 'lucide-react';

export function ExplorePage() {
  const { user } = useAuth();
  const { navigate } = useRouter();
  const [publicChars, setPublicChars] = useState<Character[]>([]);
  const [favorites, setFavorites] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState<string>('all');

  const loadData = useCallback(async () => {
    if (!user) return;
    const [charsRes, favRes] = await Promise.all([
      supabase
        .from('characters')
        .select('*')
        .or('is_public.eq.true,is_template.eq.true')
        .neq('user_id', user.id)
        .order('created_at', { ascending: false }),
      supabase.from('favorites').select('character_id').eq('user_id', user.id),
    ]);
    setPublicChars((charsRes.data as Character[]) ?? []);
    const favSet = new Set(((favRes.data as Favorite[]) ?? []).map((f) => f.character_id));
    setFavorites(favSet);
    setLoading(false);
  }, [user]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const toggleFavorite = async (charId: string) => {
    if (favorites.has(charId)) {
      setFavorites((s) => { const n = new Set(s); n.delete(charId); return n; });
      await supabase.from('favorites').delete().eq('user_id', user!.id).eq('character_id', charId);
    } else {
      setFavorites((s) => new Set(s).add(charId));
      await supabase.from('favorites').insert({ user_id: user!.id, character_id: charId });
    }
  };

  const startChat = async (character: Character) => {
    // For public/template characters not owned by user, create a copy then start
    let charId = character.id;
    if (character.user_id !== user!.id) {
      // create conversation with reference to the public character
    }
    const { data: existing } = await supabase
      .from('conversations')
      .select('id')
      .eq('user_id', user!.id)
      .eq('character_id', charId)
      .order('updated_at', { ascending: false })
      .limit(1)
      .maybeSingle();

    if (existing) {
      navigate({ name: 'chat', characterId: charId, conversationId: existing.id });
    } else {
      const { data: newConv } = await supabase
        .from('conversations')
        .insert({ user_id: user!.id, character_id: charId, title: character.name })
        .select()
        .single();
      if (newConv) navigate({ name: 'chat', characterId: charId, conversationId: newConv.id });
    }
  };

  const useTemplate = async (templateIdx: number) => {
    const tpl = characterTemplates[templateIdx];
    const { data: newChar } = await supabase
      .from('characters')
      .insert({
        user_id: user!.id,
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
        category: tpl.category,
        tags: tpl.tags,
        is_template: false,
        is_public: false,
      })
      .select()
      .single();
    if (newChar) {
      navigate({ name: 'character-edit', id: (newChar as Character).id });
    }
  };

  const filteredPublic = publicChars.filter((c) => {
    const matchesSearch =
      !search ||
      c.name.toLowerCase().includes(search.toLowerCase()) ||
      c.tagline.toLowerCase().includes(search.toLowerCase()) ||
      c.tags.some((t) => t.toLowerCase().includes(search.toLowerCase()));
    const matchesCategory = category === 'all' || c.category === category;
    return matchesSearch && matchesCategory;
  });

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-rose-500/20 to-pink-600/20 flex items-center justify-center">
          <Compass className="w-5 h-5 text-rose-400" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white">Explore</h1>
          <p className="text-sm text-zinc-400">Discover public characters and ready-made templates</p>
        </div>
      </div>

      {/* Templates */}
      <section>
        <h2 className="text-lg font-semibold text-white mb-4 flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-amber-400" />
          Featured templates
        </h2>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {characterTemplates.map((tpl, i) => {
            const color = getColor(tpl.avatar_color);
            return (
              <div key={tpl.name} className="group card p-5 hover:border-white/15 transition-all hover:-translate-y-1">
                <div className="flex items-start gap-4">
                  <div className={`shrink-0 w-14 h-14 rounded-2xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-2xl shadow-lg`}>
                    {tpl.avatar_emoji}
                  </div>
                  <div className="flex-1 min-w-0">
                    <h3 className="font-semibold text-white">{tpl.name}</h3>
                    <p className="text-sm text-zinc-400">{tpl.tagline}</p>
                  </div>
                </div>
                <p className="text-sm text-zinc-500 mt-3 line-clamp-3">{tpl.description}</p>
                <div className="flex flex-wrap gap-1.5 mt-3">
                  {tpl.tags.map((tag) => (
                    <span key={tag} className="chip">{tag}</span>
                  ))}
                </div>
                <button
                  onClick={() => useTemplate(i)}
                  className="btn-outline w-full mt-4 group-hover:border-rose-500/40 group-hover:text-rose-300"
                >
                  <Plus className="w-4 h-4" /> Use this template
                </button>
              </div>
            );
          })}
        </div>
      </section>

      {/* Public characters */}
      <section>
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
          <h2 className="text-lg font-semibold text-white">Community characters</h2>
          <div className="relative flex-1 max-w-xs">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search characters..."
              className="input-field pl-10 py-2 text-sm"
            />
          </div>
        </div>

        {/* Category filter */}
        <div className="flex gap-2 mb-4 overflow-x-auto scrollbar-none">
          <button
            onClick={() => setCategory('all')}
            className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-all ${
              category === 'all' ? 'bg-white/10 text-white border border-white/15' : 'text-zinc-400 hover:text-white hover:bg-white/5'
            }`}
          >
            All
          </button>
          {characterCategories.map((cat) => (
            <button
              key={cat.id}
              onClick={() => setCategory(cat.id)}
              className={`shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-all whitespace-nowrap ${
                category === cat.id ? 'bg-white/10 text-white border border-white/15' : 'text-zinc-400 hover:text-white hover:bg-white/5'
              }`}
            >
              {cat.icon} {cat.name}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-5 h-5 animate-spin text-rose-400" />
          </div>
        ) : filteredPublic.length === 0 ? (
          <div className="card p-10 text-center">
            <p className="text-zinc-400">No public characters found{search ? ` for "${search}"` : ' yet'}.</p>
            <p className="text-sm text-zinc-600 mt-1">Be the first to share one!</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {filteredPublic.map((char) => (
              <CharacterCard
                key={char.id}
                character={char}
                onChat={() => startChat(char)}
                onFavorite={() => toggleFavorite(char.id)}
                isFavorite={favorites.has(char.id)}
                onEdit={undefined}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
