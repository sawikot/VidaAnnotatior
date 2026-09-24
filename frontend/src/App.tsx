import { BrowserRouter, Navigate, Route, Routes, useParams } from "react-router-dom";
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

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
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
          <Route path="*" element={<Navigate to="/projects" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
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
