import { useEffect } from "react";
import { BrowserRouter, Navigate, Outlet, Route, Routes, useLocation, useParams } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ProjectManagerPage } from "./pages/ProjectManagerPage";
import { NewProjectWizardPage } from "./pages/NewProjectWizardPage";
import { ProjectDashboardPage } from "./pages/ProjectDashboardPage";
import { SlideProcessingPage } from "./pages/SlideProcessingPage";
import { MainWorkspacePage } from "./pages/MainWorkspacePage";
import { PatchGalleryPage } from "./pages/PatchGalleryPage";
import { FullOverviewPage } from "./pages/FullOverviewPage";
import { ProjectSettingsPage } from "./pages/ProjectSettingsPage";
import { ProjectExportPage } from "./pages/ProjectExportPage";
import { ImageAnnotateRedirect } from "./pages/ImageAnnotateRedirect";
import { ImageGalleryPage } from "./pages/ImageGalleryPage";
import { LoginPage, SetPasswordPage, SetupPage } from "./pages/AuthPages";
import { UsersPage } from "./pages/UsersPage";
import { VersionPage } from "./pages/VersionPage";
import { useAuthStore } from "./stores/authStore";

export default function App() {
  const load = useAuthStore((s) => s.load);
  useEffect(() => {
    load();
  }, [load]);

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/setup" element={<SetupPage />} />
        <Route path="/set-password" element={<SetPasswordPage />} />
        <Route element={<RequireSignIn />}>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/projects" replace />} />
          <Route path="/projects" element={<ProjectManagerPage />} />
          <Route path="/projects/new" element={<NewProjectWizardPage />} />
          <Route path="/projects/:projectId/wizard" element={<NewProjectWizardPage />} />
          <Route path="/projects/:projectId" element={<ProjectDashboardPage />} />
          <Route path="/projects/:projectId/settings" element={<ProjectSettingsPage />} />
          <Route path="/projects/:projectId/versions" element={<SettingsRedirect />} />
          <Route path="/projects/:projectId/images" element={<ImageGalleryPage />} />
          <Route path="/projects/:projectId/annotate" element={<ImageAnnotateRedirect />} />
          <Route path="/projects/:projectId/slides/:slideId/processing" element={<SlideProcessingPage />} />
          <Route path="/projects/:projectId/slides/:slideId/workspace" element={<MainWorkspacePage />} />
          <Route path="/projects/:projectId/slides/:slideId/gallery" element={<PatchGalleryPage />} />
          <Route path="/projects/:projectId/slides/:slideId/overview" element={<FullOverviewPage />} />
          <Route path="/projects/:projectId/export" element={<ProjectExportPage />} />
          <Route path="/projects/:projectId/slides/:slideId/export" element={<ExportRedirect />} />
          <Route path="/admin/users" element={<UsersPage />} />
          <Route path="/admin/version" element={<VersionPage />} />
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Route>
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

/** The app itself needs a signed-in person; everyone else is sent to sign in (and back afterwards). */
function RequireSignIn() {
  const state = useAuthStore((s) => s.state);
  const location = useLocation();
  if (state === "loading") return <div className="min-h-screen bg-[#0f172a]" />;
  if (state === "setup") return <Navigate to="/setup" replace />;
  if (state === "signed-out") return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  return <Outlet />;
}

/** Export belongs to the project now (every slide, pick which); the old per-slide address opens it. */
function ExportRedirect() {
  const { projectId } = useParams();
  return <Navigate to={`/projects/${projectId}/export`} replace />;
}

/** The old Configuration Versions address, kept working for bookmarks. */
function SettingsRedirect() {
  const { projectId } = useParams();
  return <Navigate to={`/projects/${projectId}/settings`} replace />;
}
