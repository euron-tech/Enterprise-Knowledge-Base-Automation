/**
 * Session state.
 *
 * The token lives in memory only and is deliberately lost on refresh — a token in
 * localStorage is readable by any injected script. The identity comes from /me,
 * i.e. from the server's database, so the UI can only ever reflect the grants the
 * server will actually enforce. Hiding admin nav from a non-admin is a courtesy;
 * the API denies them regardless.
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import * as api from "../api/client";

interface AuthState {
  identity: api.Identity | null;
  busy: boolean;
  error: string | null;
  signIn: (email: string, password: string) => Promise<boolean>;
  signOut: () => void;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [identity, setIdentity] = useState<api.Identity | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const signIn = useCallback(async (email: string, password: string) => {
    setBusy(true);
    setError(null);
    try {
      await api.login(email, password);
      setIdentity(await api.me());
      return true;
    } catch (err) {
      // The server never says whether the account exists; neither do we.
      setError(err instanceof Error ? err.message : "Sign in failed");
      api.setAccessToken(null);
      setIdentity(null);
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const signOut = useCallback(() => {
    api.setAccessToken(null);
    setIdentity(null);
    setError(null);
  }, []);

  const value = useMemo(
    () => ({ identity, busy, error, signIn, signOut }),
    [identity, busy, error, signIn, signOut],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
