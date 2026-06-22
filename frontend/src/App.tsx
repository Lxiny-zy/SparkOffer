import { lazy, Suspense, ReactNode, useState, useEffect, useCallback } from "react";
import { BrowserRouter, Routes, Route, Navigate, useNavigate } from "react-router-dom";
import { Loader2, AlertTriangle } from "lucide-react";
import { Toaster } from "sonner";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import { onSessionExpired, resetSessionExpired } from "./api/client";
import Sidebar from "./components/Sidebar";
import FloatingAssistant from "./components/FloatingAssistant";
import ErrorBoundary from "./components/ErrorBoundary";
import PointerFX from "./components/PointerFX";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Home from "./pages/Home";

// Lazy-loaded pages — only fetched when the route is visited
const Interview = lazy(() => import("./pages/Interview"));
const Review = lazy(() => import("./pages/Review"));
const History = lazy(() => import("./pages/History"));
const Profile = lazy(() => import("./pages/Profile"));
const TopicDetail = lazy(() => import("./pages/TopicDetail"));
const Knowledge = lazy(() => import("./pages/Knowledge"));
const KnowledgeTraining = lazy(() => import("./pages/KnowledgeTraining"));
const Graph = lazy(() => import("./pages/Graph"));
const RecordingAnalysis = lazy(() => import("./pages/RecordingAnalysis"));
const JobPrep = lazy(() => import("./pages/JobPrep"));
const Favorites = lazy(() => import("./pages/Favorites"));
const AlgorithmSolver = lazy(() => import("./pages/AlgorithmSolver"));
const AlgorithmCollection = lazy(() => import("./pages/AlgorithmCollection"));
const Settings = lazy(() => import("./pages/Settings"));
const QAArena = lazy(() => import("./pages/QAArena"));
const RAGDashboard = lazy(() => import("./pages/RAGDashboard"));
const NotFound = lazy(() => import("./pages/NotFound"));

function AppBootScreen() {
  return (
    <div className="sig-root min-h-screen flex items-center justify-center">
      <div className="flex flex-col items-center gap-3 text-[color:var(--sig-dim)]">
        <Loader2 className="w-6 h-6 animate-spin" style={{ color: "var(--sig-accent)" }} />
        <p className="sig-kicker">正在恢复会话…</p>
      </div>
    </div>
  );
}

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { token, loading } = useAuth();
  if (loading) return <AppBootScreen />;
  if (!token) return <Navigate to="/" replace />;
  return children;
}

function PublicHome() {
  const { token, loading } = useAuth();
  if (loading) return <AppBootScreen />;
  if (token)
    return (
      <AppShell>
        <Home />
      </AppShell>
    );
  return <Landing />;
}

function AuthPage() {
  const { token, loading } = useAuth();
  if (loading) return <AppBootScreen />;
  if (token) return <Navigate to="/" replace />;
  return <Login />;
}

function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="sig-root flex flex-col md:flex-row h-screen">
      <Sidebar />
      <main className="flex-1 overflow-hidden flex flex-col relative">
        {/* Sticky wrapper (height 0) keeps the static blueprint grid glued to the
            viewport while content scrolls — no parallax, no particle animation. */}
        <div className="hidden md:block sticky top-0 left-0 h-0 w-full z-0 pointer-events-none">
          <div className="absolute top-0 left-0 right-0 h-screen overflow-hidden">
            <div className="sig-grid absolute inset-0 opacity-70" />
          </div>
        </div>
        <div className="relative z-[1] flex-1 flex flex-col min-h-0">
          <Suspense fallback={
            <div className="flex-1 flex items-center justify-center">
              <Loader2 className="w-6 h-6 animate-spin" style={{ color: "var(--sig-accent)" }} />
            </div>
          }>
            {children}
          </Suspense>
        </div>
      </main>
      <FloatingAssistant />
    </div>
  );
}

function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<PublicHome />} />
      <Route path="/login" element={<AuthPage />} />
      <Route
        path="/*"
        element={
          <ProtectedRoute>
            <AppShell>
              <Routes>
                <Route path="/interview/:sessionId" element={<Interview />} />
                <Route path="/review/:sessionId" element={<Review />} />
                <Route path="/history" element={<History />} />
                <Route path="/profile" element={<Profile />} />
                <Route path="/profile/topic/:topic" element={<TopicDetail />} />
                <Route path="/knowledge" element={<Knowledge />} />
                <Route path="/knowledge-training" element={<KnowledgeTraining />} />
                <Route path="/graph" element={<Graph />} />
                <Route path="/recording" element={<RecordingAnalysis />} />
                <Route path="/job-prep" element={<JobPrep />} />
                <Route path="/favorites" element={<Favorites />} />
                <Route path="/algorithm" element={<AlgorithmSolver />} />
                <Route path="/algorithm/collection" element={<AlgorithmCollection />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/qa-arena" element={<QAArena />} />
                <Route path="/rag-dashboard" element={<RAGDashboard />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </AppShell>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

function SessionExpiredModal() {
  const [show, setShow] = useState(false);
  const { logout } = useAuth();
  const navigate = useNavigate();

  useEffect(() => {
    return onSessionExpired(() => setShow(true));
  }, []);

  const handleReLogin = useCallback(() => {
    setShow(false);
    resetSessionExpired();
    logout();
    navigate("/login", { replace: true });
  }, [logout, navigate]);

  if (!show) return null;

  return (
    <div className="sig-root fixed inset-0 z-[100] flex items-center justify-center p-4" style={{ background: "var(--sig-overlay)" }}>
      <div className="sig-card max-w-sm w-full p-6 animate-fade-in text-center space-y-4">
        <div className="w-12 h-12 rounded-full flex items-center justify-center mx-auto" style={{ background: "color-mix(in srgb, var(--sig-warning) 14%, transparent)" }}>
          <AlertTriangle className="w-6 h-6" style={{ color: "var(--sig-warning)" }} />
        </div>
        <h3 className="text-lg font-semibold">登录已过期</h3>
        <p className="text-sm text-[color:var(--sig-dim)]">你的登录状态已失效，请重新登录。当前页面内容不会丢失。</p>
        <button onClick={handleReLogin} className="sig-btn sig-btn-accent w-full justify-center">
          重新登录
        </button>
      </div>
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <ErrorBoundary>
          <PointerFX />
          <SessionExpiredModal />
          <AppRoutes />
          <Toaster position="top-right" richColors closeButton expand={false} duration={3500} />
        </ErrorBoundary>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
