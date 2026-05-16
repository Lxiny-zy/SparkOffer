import { createContext, useContext, useState, useEffect, useMemo, useCallback, ReactNode } from "react";
import { User } from "../types/api";

interface AuthContextValue {
  user: User | null;
  token: string | null;
  loading: boolean;
  login: (tokenStr: string, userData: User) => void;
  logout: () => void;
  updateUser: (userData: User) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(() => localStorage.getItem("token"));
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (token) {
      // 5秒超时：防止后端繁忙时 loading 永远不结束导致黑屏
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 5000);

      fetch("/api/profile", {
        headers: { Authorization: `Bearer ${token}` },
        signal: controller.signal,
      })
        .then((res) => {
          clearTimeout(timer);
          if (res.ok) {
            const stored = localStorage.getItem("user");
            if (stored) setUser(JSON.parse(stored));
            return;
          }

          if (res.status === 401) {
            logout();
            return;
          }

          // 5xx 或其他错误：保留本地会话，不登出
          const stored = localStorage.getItem("user");
          if (stored) setUser(JSON.parse(stored));
          console.warn("Auth bootstrap: backend temporarily unavailable, keeping local session:", res.status);
        })
        .catch((err) => {
          clearTimeout(timer);
          // AbortError（超时）或网络错误：保留本地会话
          const stored = localStorage.getItem("user");
          if (stored) setUser(JSON.parse(stored));
          if (err.name === "AbortError") {
            console.warn("Auth bootstrap timed out (5s), keeping local session");
          } else {
            console.warn("Auth bootstrap request failed, keeping local session:", err);
          }
        })
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = useCallback((tokenStr: string, userData: User) => {
    localStorage.setItem("token", tokenStr);
    localStorage.setItem("user", JSON.stringify(userData));
    setToken(tokenStr);
    setUser(userData);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    setToken(null);
    setUser(null);
  }, []);

  const updateUser = useCallback((userData: User) => {
    localStorage.setItem("user", JSON.stringify(userData));
    setUser(userData);
  }, []);

  const value = useMemo(
    () => ({ user, token, loading, login, logout, updateUser }),
    [user, token, loading, login, logout, updateUser]
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
