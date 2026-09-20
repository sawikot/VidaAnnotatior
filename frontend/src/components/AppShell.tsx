import { useEffect } from "react";
import { Outlet, useParams } from "react-router-dom";
import { getProject } from "../services/api";
import { useContextStore } from "../stores/contextStore";
import { IconRail } from "./IconRail";
import { TopHeader } from "./TopHeader";
import { ToastHost } from "./ToastHost";

export function AppShell() {
  const { projectId } = useParams();
  const setActiveProject = useContextStore((s) => s.setActiveProject);
  const activeId = useContextStore((s) => s.activeProject?.id);

  // Every project page needs to know the project (its type decides which navigation to show),
  // not just the dashboard, so load it here whenever the route names a different project.
  useEffect(() => {
    if (!projectId) {
      setActiveProject(null);
      return;
    }
    const id = Number(projectId);
    if (activeId === id) return;
    setActiveProject(null);
    let cancelled = false;
    getProject(id)
      .then((p) => !cancelled && setActiveProject(p))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  return (
    <div className="min-h-screen bg-surface">
      <IconRail />
      <TopHeader />
      <div className="pl-14">
        <main className="w-full pt-14 bg-surface min-h-screen">
          <Outlet />
        </main>
      </div>
      <ToastHost />
    </div>
  );
}
