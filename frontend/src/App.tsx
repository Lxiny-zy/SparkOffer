import { lazy, Suspense, ReactNode } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import { AuthProvider, useAuth } from "./contexts/AuthContext";
import Sidebar from "./components/Sidebar";
import FloatingAssistant from "./components/FloatingAssistant";
import ErrorBoundary from "./components/ErrorBoundary";
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
const Graph = lazy(() => import("./pages/Graph"));
const RecordingAnalysis = lazy(() => import("./pages/RecordingAnalysis"));
const JobPrep = lazy(() => import("./pages/JobPrep"));
const Favorites = lazy(() => import("./pages/Favorites"));
const AlgorithmSolver = lazy(() => import("./pages/AlgorithmSolver"));
const AlgorithmCollection = lazy(() => import("./pages/AlgorithmCollection"));
const Settings = lazy(() => import("./pages/Settings"));
const QAArena = lazy(() => import("./pages/QAArena"));
const NotFound = lazy(() => import("./pages/NotFound"));

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { token, loading } = useAuth();
  if (loading) return null;
  if (!token) return <Navigate to="/" replace />;
  return children;
}

function PublicHome() {
  const { token, loading } = useAuth();
  if (loading) return null;
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
  if (loading) return null;
  if (token) return <Navigate to="/" replace />;
  return <Login />;
}

function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col md:flex-row h-screen bg-bg">
      <Sidebar />
      <main className="flex-1 overflow-y-auto flex flex-col md:m-3 md:ml-0 md:rounded-3xl md:bg-surface">
        <Suspense fallback={
          <div className="flex-1 flex items-center justify-center">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
          </div>
        }>
          {children}
        </Suspense>
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
                <Route path="/graph" element={<Graph />} />
                <Route path="/recording" element={<RecordingAnalysis />} />
                <Route path="/job-prep" element={<JobPrep />} />
                <Route path="/favorites" element={<Favorites />} />
                <Route path="/algorithm" element={<AlgorithmSolver />} />
                <Route path="/algorithm/collection" element={<AlgorithmCollection />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="/qa-arena" element={<QAArena />} />
                <Route path="*" element={<NotFound />} />
              </Routes>
            </AppShell>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}

function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <ErrorBoundary>
          <AppRoutes />
        </ErrorBoundary>
      </BrowserRouter>
    </AuthProvider>
  );
}

export default App;
