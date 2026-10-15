import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { configureApi, get, refreshSession, signOut as apiSignOut, type Me } from "./api";

export type AppConfig = {
  apiBase: string;
  appName: string;
  organization: string;
  /** From the service's branding settings (GET /api/v1/branding); config.json gives the defaults. */
  logoUrl?: string | null;
  logoAlt?: string;
  supportUrl?: string | null;
  contactEmail?: string | null;
  /** Cloudflare Turnstile's site key, when the service checks "Email me a code" for bots. */
  botCheckSiteKey?: string | null;
};

type AuthState = {
  config: AppConfig;
  me: Me | null;
  loading: boolean;
  setMe: (me: Me | null) => void;
  reloadMe: () => Promise<void>;
  signOut: (everywhere?: boolean) => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ config, children }: { config: AppConfig; children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    configureApi(config.apiBase, () => setMe(null));
    // Restore the session from the refresh cookie, if there is one
    refreshSession().then((user) => {
      setMe(user);
      setLoading(false);
    });
  }, [config.apiBase]);

  const reloadMe = useCallback(async () => {
    setMe(await get<Me>("/me"));
  }, []);

  const signOut = useCallback(async (everywhere = false) => {
    await apiSignOut(everywhere);
    setMe(null);
  }, []);

  const value = useMemo(
    () => ({ config, me, loading, setMe, reloadMe, signOut }),
    [config, me, loading, reloadMe, signOut],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
