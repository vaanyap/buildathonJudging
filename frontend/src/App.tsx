import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Login from "./pages/Login";
import JudgeDashboard from "./pages/judge/Dashboard";
import ProjectPage from "./pages/judge/ProjectPage";
import CrossTrack from "./pages/organizer/CrossTrack";
import Judges from "./pages/organizer/Judges";
import OrganizerLayout from "./pages/organizer/Layout";
import Monitoring from "./pages/organizer/Monitoring";
import Overview from "./pages/organizer/Overview";
import Projects from "./pages/organizer/Projects";
import Results from "./pages/organizer/Results";
import Schedule from "./pages/organizer/Schedule";
import type { Role } from "./types";

function Home() {
  const { user, loading } = useAuth();
  if (loading) return <p className="page-loading">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return <Navigate to={user.role === "organizer" ? "/organizer" : "/judge"} replace />;
}

function RequireRole({ role, children }: { role: Role; children: React.ReactNode }) {
  const { user, loading } = useAuth();
  if (loading) return <p className="page-loading">Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== role) {
    return <Navigate to={user.role === "organizer" ? "/organizer" : "/judge"} replace />;
  }
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/judge"
        element={
          <RequireRole role="judge">
            <JudgeDashboard />
          </RequireRole>
        }
      />
      <Route
        path="/judge/projects/:projectId"
        element={
          <RequireRole role="judge">
            <ProjectPage />
          </RequireRole>
        }
      />
      <Route
        path="/organizer"
        element={
          <RequireRole role="organizer">
            <OrganizerLayout />
          </RequireRole>
        }
      >
        <Route index element={<Overview />} />
        <Route path="projects" element={<Projects />} />
        <Route path="judges" element={<Judges />} />
        <Route path="schedule" element={<Schedule />} />
        <Route path="progress" element={<Monitoring />} />
        <Route path="results" element={<Results />} />
        <Route path="cross-track" element={<CrossTrack />} />
      </Route>
      <Route path="*" element={<Home />} />
    </Routes>
  );
}
