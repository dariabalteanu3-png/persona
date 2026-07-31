import { useState, useEffect, useCallback } from 'react';
import { supabase } from '@/lib/supabase';
import { useAuth } from '@/context/AuthContext';
import type { Character, Conversation } from '@/types/database';
import { User, Loader2, Save, Check, Mail, Calendar, MessageCircle, Users, Globe } from 'lucide-react';

const PROFILE_EMOJIS = ['🎭', '🦊', '🐱', '🦉', '🐺', '🦁', '🐼', '🐸', '🦋', '🐙', '🌟', '🌙', '☀️', '⚡', '🔥', '🌸'];

export function ProfilePage() {
  const { user, profile, refreshProfile } = useAuth();
  const [displayName, setDisplayName] = useState('');
  const [avatarEmoji, setAvatarEmoji] = useState('🎭');
  const [bio, setBio] = useState('');
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [stats, setStats] = useState({ characters: 0, conversations: 0, publicChars: 0 });
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (profile) {
      setDisplayName(profile.display_name);
      setAvatarEmoji(profile.avatar_emoji);
      setBio(profile.bio);
    }
  }, [profile]);

  const loadStats = useCallback(async () => {
    if (!user) return;
    const [charsRes, convsRes, pubRes] = await Promise.all([
      supabase.from('characters').select('id', { count: 'exact', head: true }).eq('user_id', user.id),
      supabase.from('conversations').select('id', { count: 'exact', head: true }).eq('user_id', user.id),
      supabase.from('characters').select('id', { count: 'exact', head: true }).eq('user_id', user.id).eq('is_public', true),
    ]);
    setStats({
      characters: charsRes.count ?? 0,
      conversations: convsRes.count ?? 0,
      publicChars: pubRes.count ?? 0,
    });
    setLoading(false);
  }, [user]);

  useEffect(() => {
    loadStats();
  }, [loadStats]);

  const save = async () => {
    if (!user) return;
    setSaving(true);
    await supabase.from('profiles').upsert({
      id: user.id,
      display_name: displayName.trim() || 'Friend',
      avatar_emoji: avatarEmoji,
      bio: bio.trim(),
    });
    await refreshProfile();
    setSaving(false);
    setSaved(true);
    setTimeout(() => setSaved(false), 2000);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="w-6 h-6 animate-spin text-rose-400" />
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-rose-500/20 to-pink-600/20 flex items-center justify-center">
          <User className="w-5 h-5 text-rose-400" />
        </div>
        <div>
          <h1 className="text-2xl font-bold text-white">Your Profile</h1>
          <p className="text-sm text-zinc-400">Manage your account and see your stats</p>
        </div>
      </div>

      {/* Profile card */}
      <div className="card p-6">
        <div className="flex items-center gap-4 mb-6">
          <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-rose-500/30 to-pink-600/30 flex items-center justify-center text-4xl shadow-lg">
            {avatarEmoji}
          </div>
          <div className="flex-1">
            <h2 className="text-xl font-bold text-white">{displayName || 'Friend'}</h2>
            <p className="text-sm text-zinc-400 flex items-center gap-1.5 mt-1">
              <Mail className="w-3.5 h-3.5" /> {user?.email}
            </p>
            <p className="text-xs text-zinc-600 flex items-center gap-1.5 mt-1">
              <Calendar className="w-3 h-3" /> Joined {new Date(user?.created_at ?? Date.now()).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}
            </p>
          </div>
        </div>

        {/* Stats */}
        <div className="grid grid-cols-3 gap-3 mb-6">
          <StatCard icon={<Users className="w-4 h-4" />} label="Characters" value={stats.characters} color="text-rose-400" />
          <StatCard icon={<MessageCircle className="w-4 h-4" />} label="Conversations" value={stats.conversations} color="text-sky-400" />
          <StatCard icon={<Globe className="w-4 h-4" />} label="Public" value={stats.publicChars} color="text-emerald-400" />
        </div>

        {/* Edit form */}
        <div className="space-y-4">
          <div>
            <label className="label-text">Display name</label>
            <input className="input-field" value={displayName} onChange={(e) => setDisplayName(e.target.value)} placeholder="Your name" />
          </div>

          <div>
            <label className="label-text">Avatar emoji</label>
            <div className="flex flex-wrap gap-2">
              {PROFILE_EMOJIS.map((emoji) => (
                <button
                  key={emoji}
                  onClick={() => setAvatarEmoji(emoji)}
                  className={`w-10 h-10 rounded-xl flex items-center justify-center text-lg transition-all ${
                    avatarEmoji === emoji ? 'bg-white/15 ring-2 ring-rose-500/50 scale-105' : 'bg-white/[0.04] hover:bg-white/[0.08]'
                  }`}
                >
                  {emoji}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="label-text">Bio</label>
            <textarea className="input-field min-h-[80px] resize-y" value={bio} onChange={(e) => setBio(e.target.value)} placeholder="Tell us about yourself..." />
          </div>

          <button onClick={save} disabled={saving} className="btn-primary">
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : saved ? <Check className="w-4 h-4" /> : <Save className="w-4 h-4" />}
            {saved ? 'Saved!' : 'Save profile'}
          </button>
        </div>
      </div>
    </div>
  );
}

function StatCard({ icon, label, value, color }: { icon: React.ReactNode; label: string; value: number; color: string }) {
  return (
    <div className="rounded-2xl bg-white/[0.03] border border-white/[0.06] p-4 text-center">
      <div className={`inline-flex items-center justify-center w-8 h-8 rounded-lg bg-white/5 mb-2 ${color}`}>
        {icon}
      </div>
      <p className="text-2xl font-bold text-white">{value}</p>
      <p className="text-xs text-zinc-500">{label}</p>
    </div>
  );
}
