import { AuthProvider, useAuth } from '@/context/AuthContext';
import { RouterProvider, useRouter } from '@/context/RouterContext';
import { AuthPage } from '@/pages/AuthPage';
import { AppShell } from '@/components/AppShell';
import { HomePage } from '@/pages/HomePage';
import { ExplorePage } from '@/pages/ExplorePage';
import { CharacterEditor } from '@/pages/CharacterEditor';
import { ChatPage } from '@/pages/ChatPage';
import { MemoriesPage } from '@/pages/MemoriesPage';
import { ProfilePage } from '@/pages/ProfilePage';
import { Loader2 } from 'lucide-react';

function AppRoutes() {
  const { route } = useRouter();

  switch (route.name) {
    case 'home':
      return <HomePage />;
    case 'explore':
      return <ExplorePage />;
    case 'sounds':
      return <HomePage />;
    case 'memories':
      return <MemoriesPage />;
    case 'profile':
      return <ProfilePage />;
    case 'character-edit':
      return <CharacterEditor characterId={route.id} />;
    case 'chat':
      return <ChatPage characterId={route.characterId} conversationId={route.conversationId} />;
    default:
      return <HomePage />;
  }
}

function AppContent() {
  const { user, loading } = useAuth();
  const { route } = useRouter();

  if (loading) {
    return (
      <div className="min-h-screen bg-[#0a0a0f] flex items-center justify-center">
        <Loader2 className="w-8 h-8 animate-spin text-rose-400" />
      </div>
    );
  }

  if (!user) {
    return <AuthPage />;
  }

  return (
    <AppShell>
      <AppRoutes key={route.name === 'chat' ? `chat-${route.characterId}` : route.name === 'character-edit' ? `edit-${route.id ?? 'new'}` : route.name} />
    </AppShell>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <RouterProvider>
        <AppContent />
      </RouterProvider>
    </AuthProvider>
  );
}
