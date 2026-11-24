import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { api, ApiError, setCsrfToken, type Me } from "./api";

interface AuthValue {
  me: Me | null;
  loading: boolean;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthValue | null>(null);

async function fetchMe(): Promise<Me | null> {
  try {
    const me = await api.me();
    setCsrfToken(me.csrf_token);
    return me;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      setCsrfToken(null);
      return null;
    }
    throw e;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({ queryKey: ["me"], queryFn: fetchMe, staleTime: 60_000 });

  const signIn = useCallback(
    async (email: string, password: string) => {
      const me = await api.login(email, password);
      setCsrfToken(me.csrf_token);
      queryClient.setQueryData(["me"], me);
    },
    [queryClient],
  );

  const signOut = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setCsrfToken(null);
      queryClient.clear();
      queryClient.setQueryData(["me"], null);
    }
  }, [queryClient]);

  const value = useMemo(
    () => ({ me: data ?? null, loading: isPending, signIn, signOut }),
    [data, isPending, signIn, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth hors de AuthProvider");
  return ctx;
}
