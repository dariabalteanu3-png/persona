import { useAuth } from '@/context/AuthContext';
import { useRouter } from '@/context/RouterContext';
import { AmbientMixer } from '@/components/AmbientMixer';
import { Sparkles, Home, Compass, Heart, User, LogOut, Plus } from 'lucide-react';

export function AppShell({ children }: { children: React.ReactNode }) {
  const { user, profile, signOut } = useAuth();
  const { route, navigate } = useRouter();

  const navItems = [
    { id: 'home', label: 'My Characters', icon: Home },
    { id: 'explore', label: 'Explore', icon: Compass },
    { id: 'memories', label: 'Memories', icon: Heart },
    { id: 'profile', label: 'Profile', icon: User },
  ] as const;

  const isActive = (id: string) => {
    if (id === 'home') return route.name === 'home' || route.name === 'character-edit' || route.name === 'chat';
    return route.name === id;
  };

  return (
    <div className="min-h-screen bg-[#0a0a0f] bg-mesh">
      {/* Top bar */}
      <header className="sticky top-0 z-30 glass border-b border-white/[0.06]">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
          <button onClick={() => navigate({ name: 'home' })} className="flex items-center gap-2.5 group">
            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-rose-500 to-pink-600 flex items-center justify-center shadow-lg shadow-rose-500/20 group-hover:scale-105 transition-transform">
              <Sparkles className="w-5 h-5 text-white" />
            </div>
            <span className="font-bold text-lg text-gradient hidden sm:block">Persona</span>
          </button>

          <div className="flex items-center gap-2">
            <AmbientMixer compact />
            <div className="w-px h-6 bg-white/10 hidden sm:block" />
            <button
              onClick={() => navigate({ name: 'profile' })}
              className="flex items-center gap-2 rounded-xl pl-1.5 pr-3 py-1.5 hover:bg-white/5 transition-all"
            >
              <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-rose-500/30 to-pink-600/30 flex items-center justify-center text-sm">
                {profile?.avatar_emoji ?? '🎭'}
              </div>
              <span className="text-sm text-zinc-300 hidden sm:block max-w-[120px] truncate">
                {profile?.display_name || user?.email?.split('@')[0] || 'You'}
              </span>
            </button>
            <button onClick={signOut} className="btn-ghost" title="Sign out">
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 py-6 flex gap-6">
        {/* Sidebar — desktop */}
        <aside className="hidden lg:block w-56 shrink-0">
          <div className="sticky top-24 space-y-1">
            <button
              onClick={() => navigate({ name: 'character-edit' })}
              className="btn-primary w-full mb-3"
            >
              <Plus className="w-4 h-4" /> New Character
            </button>
            {navItems.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.id}
                  onClick={() => navigate({ name: item.id })}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all ${
                    isActive(item.id)
                      ? 'bg-white/[0.06] text-white'
                      : 'text-zinc-400 hover:text-white hover:bg-white/[0.03]'
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  {item.label}
                </button>
              );
            })}
          </div>
        </aside>

        {/* Main content */}
        <main className="flex-1 min-w-0 pb-24">
          {children}
        </main>
      </div>

      {/* Bottom nav — mobile */}
      <nav className="lg:hidden fixed bottom-0 inset-x-0 z-30 glass border-t border-white/[0.06]">
        <div className="flex items-center justify-around px-2 py-1.5">
          {navItems.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                onClick={() => navigate({ name: item.id })}
                className={`flex flex-col items-center gap-0.5 px-3 py-2 rounded-xl transition-all ${
                  isActive(item.id) ? 'text-rose-400' : 'text-zinc-500'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="text-[10px] font-medium">{item.label.split(' ')[0]}</span>
              </button>
            );
          })}
          <button
            onClick={() => navigate({ name: 'character-edit' })}
            className="flex flex-col items-center gap-0.5 px-3 py-2 rounded-xl text-rose-400"
          >
            <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-rose-500 to-pink-600 flex items-center justify-center">
              <Plus className="w-5 h-5 text-white" />
            </div>
          </button>
        </div>
      </nav>

      {/* Floating ambient mixer button */}
      <AmbientMixer />
    </div>
  );
}
