import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

type Route =
  | { name: 'home' }
  | { name: 'explore' }
  | { name: 'sounds' }
  | { name: 'memories' }
  | { name: 'profile' }
  | { name: 'character-edit'; id?: string }
  | { name: 'chat'; characterId: string; conversationId?: string };

type RouterContextValue = {
  route: Route;
  navigate: (route: Route) => void;
};

const RouterContext = createContext<RouterContextValue | undefined>(undefined);

export function RouterProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<Route>({ name: 'home' });

  const navigate = useCallback((r: Route) => {
    setRoute(r);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, []);

  return (
    <RouterContext.Provider value={{ route, navigate }}>
      {children}
    </RouterContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useRouter() {
  const ctx = useContext(RouterContext);
  if (!ctx) throw new Error('useRouter must be used within RouterProvider');
  return ctx;
}
