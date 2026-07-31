import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from '@/context/RouterContext';
import { CharacterCard } from '@/components/CharacterCard';
import { characterTemplates } from '@/lib/templates';
import { getColor } from '@/lib/colors';
import type { Character, Conversation } from '@/types/database';
import { Plus, Sparkles, MessageCircle, Loader2, ArrowRight } from 'lucide-react';

export function HomePage() {
  const { user } = useAuth();
  const { navigate } = useRouter();
  const [characters, setCharacters] = useState<Character[]>([]);
  const [recentConversations, setRecentConversations] = useState<(Conversation & { character?: Character })[]>([]);
  const [loading, setLoading] = useState(true);

  const loadData = useCallback(async () => {
    if (!user) return;
    const [charsRes, convsRes] = await Promise.all([
      supabase
        .from('characters')
        .select('*')
        .eq('user_id', user.id)
        .order('updated_at', { ascending: false }),
      supabase
        .from('conversations')
        .select('*, character:characters(*)')
        .eq('user_id', user.id)
        .order('updated_at', { ascending: false })
        .limit(5),
    ]);
    setCharacters((charsRes.data as Character[]) ?? []);
    setRecentConversations((convsRes.data as (Conversation & { character?: Character })[]) ?? []);
    setLoading(false);
  }, [user]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  const startConversation = async (character: Character) => {
    const { data: existing } = await supabase
      .from('conversations')
      .select('id')
      .eq('user_id', user!.id)
      .eq('character_id', character.id)
      .order('updated_at', { ascending: false })
      .limit(1)
      .maybeSingle();

    if (existing) {
      navigate({ name: 'chat', characterId: character.id, conversationId: existing.id });
    } else {
      const { data: newConv } = await supabase
        .from('conversations')
        .insert({ user_id: user!.id, character_id: character.id, title: character.name })
        .select()
        .single();
      if (newConv) {
        navigate({ name: 'chat', characterId: character.id, conversationId: newConv.id });
      }
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-rose-400" />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      {/* Hero */}
      <div className="relative overflow-hidden rounded-3xl glass-strong p-6 sm:p-10">
        <div className="absolute -top-20 -right-20 w-64 h-64 rounded-full bg-rose-500/10 blur-3xl" />
        <div className="absolute -bottom-20 -left-20 w-64 h-64 rounded-full bg-fuchsia-500/8 blur-3xl" />
        <div className="relative">
          <div className="inline-flex items-center gap-2 chip mb-4">
            <Sparkles className="w-3.5 h-3.5 text-rose-400" />
            Welcome back
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold text-gradient mb-3">
            Bring a character to life.
          </h1>
          <p className="text-zinc-400 max-w-lg mb-6">
            Create AI companions with personality, voice, and a world of their own. Chat with them, hear them speak, and set the mood with ambient soundscapes.
          </p>
          <button onClick={() => navigate({ name: 'character-edit' })} className="btn-primary">
            <Plus className="w-4 h-4" /> Create your first character
          </button>
        </div>
      </div>

      {/* Recent conversations */}
      {recentConversations.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-white flex items-center gap-2">
              <MessageCircle className="w-5 h-5 text-rose-400" />
              Continue chatting
            </h2>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {recentConversations.map((conv) => {
              if (!conv.character) return null;
              const color = getColor(conv.character.avatar_color);
              return (
                <button
                  key={conv.id}
                  onClick={() => navigate({ name: 'chat', characterId: conv.character_id, conversationId: conv.id })}
                  className="group card p-4 text-left hover:border-white/15 transition-all hover:-translate-y-1"
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-11 h-11 rounded-xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-xl shadow-lg`}>
                      {conv.character.avatar_emoji}
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-white truncate">{conv.character.name}</p>
                      <p className="text-xs text-zinc-500">{conv.title}</p>
                    </div>
                    <ArrowRight className="w-4 h-4 text-zinc-600 group-hover:text-rose-400 group-hover:translate-x-0.5 transition-all" />
                  </div>
                </button>
              );
            })}
          </div>
        </section>
      )}

      {/* Your characters */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-white">Your characters</h2>
          <button onClick={() => navigate({ name: 'character-edit' })} className="btn-ghost text-sm">
            <Plus className="w-4 h-4" /> New
          </button>
        </div>

        {characters.length === 0 ? (
          <EmptyState onNew={() => navigate({ name: 'character-edit' })} />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {characters.map((char) => (
              <CharacterCard
                key={char.id}
                character={char}
                onClick={() => navigate({ name: 'character-edit', id: char.id })}
                onChat={() => startConversation(char)}
                onEdit={() => navigate({ name: 'character-edit', id: char.id })}
              />
            ))}
          </div>
        )}
      </section>

      {/* Templates */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-semibold text-white">Start from a template</h2>
            <p className="text-sm text-zinc-500 mt-0.5">Pre-made characters you can customize</p>
          </div>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {characterTemplates.slice(0, 4).map((tpl) => {
            const color = getColor(tpl.avatar_color);
            return (
              <button
                key={tpl.name}
                onClick={() => navigate({ name: 'character-edit' })}
                className="group card p-4 text-left hover:border-white/15 transition-all hover:-translate-y-1"
              >
                <div className={`w-12 h-12 rounded-2xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-2xl shadow-lg mb-3`}>
                  {tpl.avatar_emoji}
                </div>
                <p className="font-medium text-white text-sm">{tpl.name}</p>
                <p className="text-xs text-zinc-500 line-clamp-2 mt-1">{tpl.tagline}</p>
              </button>
            );
          })}
        </div>
      </section>
    </div>
  );
}

function EmptyState({ onNew }: { onNew: () => void }) {
  return (
    <div className="card p-10 text-center">
      <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-rose-500/20 to-pink-600/20 mb-4">
        <Sparkles className="w-8 h-8 text-rose-400" />
      </div>
      <h3 className="text-lg font-semibold text-white mb-2">No characters yet</h3>
      <p className="text-sm text-zinc-400 max-w-sm mx-auto mb-5">
        Create your first AI character. Give them a name, personality, a voice, and a world to live in.
      </p>
      <button onClick={onNew} className="btn-primary">
        <Plus className="w-4 h-4" /> Create a character
      </button>
    </div>
  );
}
