import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import { useRouter } from '@/context/RouterContext';
import { getColor } from '@/lib/colors';
import type { Memory, Character } from '@/types/database';
import { Heart, Trash2, Loader2, Bookmark } from 'lucide-react';

export function MemoriesPage() {
  const { user } = useAuth();
  const { navigate } = useRouter();
  const [memories, setMemories] = useState<(Memory & { character?: Character })[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    if (!user) return;
    const { data } = await supabase
      .from('memories')
      .select('*, character:characters(*)')
      .eq('user_id', user.id)
      .order('created_at', { ascending: false });
    setMemories((data as (Memory & { character?: Character })[]) ?? []);
    setLoading(false);
  }, [user]);

  useEffect(() => {
    load();
  }, [load]);

  const remove = async (id: string) => {
    setMemories((m) => m.filter((mem) => mem.id !== id));
    await supabase.from('memories').delete().eq('id', id);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-rose-400" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-rose-500/20 to-pink-600/20 flex items-center justify-center">
          <Heart className="w-5 h-5 text-rose-400" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white">My Memories</h1>
          <p className="text-sm text-zinc-400">Moments you've saved from your conversations</p>
        </div>
      </div>

      {memories.length === 0 ? (
        <div className="card p-12 text-center">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-2xl bg-gradient-to-br from-rose-500/15 to-pink-600/15 mb-4">
            <Bookmark className="w-8 h-8 text-rose-400" />
          </div>
          <h3 className="text-lg font-semibold text-white mb-2">No memories saved yet</h3>
          <p className="text-sm text-zinc-400 max-w-sm mx-auto mb-5">
            During a conversation, hover over a character's reply and tap "Save" to keep a memorable moment here.
          </p>
          <button onClick={() => navigate({ name: 'home' })} className="btn-primary">
            Start a conversation
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {memories.map((mem) => {
            const color = mem.character ? getColor(mem.character.avatar_color) : getColor('rose');
            return (
              <div key={mem.id} className="group card p-5 animate-fade-in-up">
                <div className="flex items-start gap-3">
                  {mem.character && (
                    <button
                      onClick={() => navigate({ name: 'character-edit', id: mem.character!.id })}
                      className={`shrink-0 w-10 h-10 rounded-xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-lg shadow-md hover:scale-105 transition-transform`}
                    >
                      {mem.character.avatar_emoji}
                    </button>
                  )}
                  <div className="flex-1 min-w-0">
                    {mem.character && (
                      <p className="text-xs font-medium text-zinc-400 mb-1">{mem.character.name}</p>
                    )}
                    <p className="text-sm text-zinc-100 leading-relaxed line-clamp-4">{mem.content}</p>
                    <p className="text-xs text-zinc-600 mt-2">
                      {new Date(mem.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}
                    </p>
                  </div>
                  <button
                    onClick={() => remove(mem.id)}
                    className="shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-zinc-600 hover:text-rose-400 hover:bg-rose-500/10 transition-all opacity-0 group-hover:opacity-100"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
