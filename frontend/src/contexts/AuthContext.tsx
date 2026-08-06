import { createContext, useContext, useState, useEffect, useMemo, useCallback, ReactNode } from "react";
import { User } from "../types/api";

interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (tokenStr: string, userData: User) => void;
  replaceToken: (tokenStr: string) => void;
  logout: () => void;
  updateUser: (userData: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function readStoredToken(): string | null {
  try {
    return localStorage.getItem("token");
  } catch {
    return null;
  }
}

function readStoredUser(): User | null {
  try {
    const stored = localStorage.getItem("user");
    if (!stored) return null;
    const parsed: unknown = JSON.parse(stored);
    return parsed && typeof parsed === "object" ? parsed as User : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(readStoredToken);
  const [user, setUser] = useState<User | null>(() => token ? readStoredUser() : null);
  const [loading, setLoading] = useState(() => Boolean(token));

  const logout = useCallback(() => {
    try {
      localStorage.removeItem("token");
      localStorage.removeItem("user");
    } catch {
      // In-memory state must still be cleared if storage is unavailable.
    }
    setToken(null);
    setUser(null);
    setLoading(false);
  }, []);

  useEffect(() => {
    if (!token) return;

    let active = true;
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 5000);
    const restoreLocalUser = () => {
      const stored = readStoredUser();
      if (active && stored) setUser(stored);
    };

    fetch("/api/auth/me", {
      headers: { Authorization: `Bearer ${token}` },
      signal: controller.signal,
    })
      .then(async (res) => {
        window.clearTimeout(timer);
        if (!active) return;
        if (res.ok) {
          const fresh: User = await res.json();
          if (!active) return;
          try { localStorage.setItem("user", JSON.stringify(fresh)); } catch { /* storage unavailable */ }
          setUser(fresh);
          return;
        }

        if (res.status === 401) {
          logout();
          return;
        }

        restoreLocalUser();
        console.warn("Auth bootstrap: backend temporarily unavailable, keeping local session:", res.status);
      })
      .catch((err: unknown) => {
        if (!active) return;
        window.clearTimeout(timer);
        restoreLocalUser();
        if (err instanceof DOMException && err.name === "AbortError") {
          console.warn("Auth bootstrap timed out (5s), keeping local session");
        } else {
          console.warn("Auth bootstrap request failed, keeping local session:", err);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [logout, token]);

  const login = useCallback((tokenStr: string, userData: User) => {
    try {
      localStorage.setItem("token", tokenStr);
      localStorage.setItem("user", JSON.stringify(userData));
    } catch {
      // In-memory authentication still works when storage is unavailable.
    }
    setToken(tokenStr);
    setUser(userData);
  }, []);

  const replaceToken = useCallback((tokenStr: string) => {
    try { localStorage.setItem("token", tokenStr); } catch { /* storage unavailable */ }
    setToken(tokenStr);
  }, []);

  const updateUser = useCallback((userData: User) => {
    try { localStorage.setItem("user", JSON.stringify(userData)); } catch { /* storage unavailable */ }
    setUser(userData);
  }, []);

  const value = useMemo(
    () => ({ user, token, loading, login, replaceToken, logout, updateUser }),
    [user, token, loading, login, replaceToken, logout, updateUser]
  );

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
