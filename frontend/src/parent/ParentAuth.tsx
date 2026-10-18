import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { api, refreshSession, setAccessToken, setAuthRealm, setSessionExpiredHandler } from '../api/client';

interface ParentAuthState {
  signedIn: boolean;
  loading: boolean;
  acceptToken: (accessToken: string) => void;
  signOut: () => Promise<void>;
}

const ParentAuthContext = createContext<ParentAuthState | null>(null);

/** The parent's session: access token in memory, refresh token in an httpOnly cookie. */
export function ParentAuthProvider({ children }: { children: ReactNode }) {
  const [signedIn, setSignedIn] = useState(false);
  const [loading, setLoading] = useState(true);
  const queryClient = useQueryClient();

  const signOutLocally = useCallback(() => {
    setAccessToken(null);
    setSignedIn(false);
    queryClient.clear();
  }, [queryClient]);

  useEffect(() => {
    setAuthRealm('parent');
    setSessionExpiredHandler(signOutLocally);
    refreshSession<{ access_token: string }>()
      .then((session) => setSignedIn(!!session))
      .finally(() => setLoading(false));
  }, [signOutLocally]);

  const acceptToken = useCallback((token: string) => {
    setAccessToken(token);
    setSignedIn(true);
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.POST('/api/v1/parent/auth/logout');
    } finally {
      signOutLocally();
    }
  }, [signOutLocally]);

  const value = useMemo(() => ({ signedIn, loading, acceptToken, signOut }), [signedIn, loading, acceptToken, signOut]);
  return <ParentAuthContext.Provider value={value}>{children}</ParentAuthContext.Provider>;
}

export function useParentAuth(): ParentAuthState {
  const context = useContext(ParentAuthContext);
  if (!context) throw new Error('useParentAuth must be used inside ParentAuthProvider');
  return context;
}
