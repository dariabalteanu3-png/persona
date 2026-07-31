import type { Character } from '@/types/database';
import { getColor } from '@/lib/colors';
import { MessageCircle, Heart, MoreVertical, Globe, Lock } from 'lucide-react';

type Props = {
  character: Character;
  onClick?: () => void;
  onChat?: () => void;
  onEdit?: () => void;
  onFavorite?: () => void;
  isFavorite?: boolean;
  showActions?: boolean;
  messageCount?: number;
};

export function CharacterCard({ character, onClick, onChat, onEdit, onFavorite, isFavorite, showActions = true, messageCount }: Props) {
  const color = getColor(character.avatar_color);

  return (
    <div
      onClick={onClick}
      className="group card p-5 cursor-pointer hover:border-white/15 transition-all hover:-translate-y-1 hover:shadow-2xl hover:shadow-black/40 animate-fade-in-up"
    >
      <div className="flex items-start gap-4">
        {/* Avatar */}
        <div className={`shrink-0 w-14 h-14 rounded-2xl bg-gradient-to-br ${color.from} ${color.to} flex items-center justify-center text-2xl shadow-lg`}>
          {character.avatar_emoji}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-white truncate">{character.name}</h3>
            {character.is_public ? (
              <Globe className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
            ) : (
              <Lock className="w-3.5 h-3.5 text-zinc-600 shrink-0" />
            )}
          </div>
          <p className="text-sm text-zinc-400 line-clamp-1 mt-0.5">{character.tagline || character.description}</p>
        </div>

        {showActions && (
          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
            {onFavorite && (
              <button
                onClick={(e) => { e.stopPropagation(); onFavorite(); }}
                className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all ${
                  isFavorite ? 'text-rose-400 bg-rose-500/10' : 'text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10'
                }`}
              >
                <Heart className={`w-4 h-4 ${isFavorite ? 'fill-current' : ''}`} />
              </button>
            )}
            {onChat && (
              <button
                onClick={(e) => { e.stopPropagation(); onChat(); }}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-zinc-400 hover:text-white hover:bg-white/10 transition-all"
                title="Chat"
              >
                <MessageCircle className="w-4 h-4" />
              </button>
            )}
            {onEdit && (
              <button
                onClick={(e) => { e.stopPropagation(); onEdit(); }}
                className="w-8 h-8 rounded-lg flex items-center justify-center text-zinc-400 hover:text-white hover:bg-white/10 transition-all"
                title="Edit"
              >
                <MoreVertical className="w-4 h-4" />
              </button>
            )}
          </div>
        )}
      </div>

      {character.tags && character.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-3">
          {character.tags.slice(0, 3).map((tag) => (
            <span key={tag} className="chip">{tag}</span>
          ))}
        </div>
      )}

      {typeof messageCount === 'number' && messageCount > 0 && (
        <div className="flex items-center gap-1.5 mt-3 text-xs text-zinc-500">
          <MessageCircle className="w-3.5 h-3.5" />
          {messageCount} messages
        </div>
      )}
    </div>
  );
}
