import { useCallback, useEffect, useState } from "react";
import { MembersPanel } from "../features/projects/MembersPanel";
import { useCan } from "../stores/authStore";
import { Link, useParams } from "react-router-dom";
import { Button, Card } from "../components/primitives";
import { ConfigEditor } from "../features/projects/ConfigEditor";
import { EditProjectModal } from "../features/projects/EditProjectModal";
import { PatchSizesPanel } from "../features/grids/PatchSizesPanel";
import { getProject } from "../services/api";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";
import type { ProjectDetail } from "../types/api";

/**
 * Everything about a project in one place: its details, and every setting of its one configuration
 * (patch grid, tissue detection, classes, tools, QC), edited in place.
 */
export function ProjectSettingsPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const pushToast = useUiStore((s) => s.pushToast);
  const setActiveProject = useContextStore((s) => s.setActiveProject);
  const { manage: canManage, user } = useCan();

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const refresh = useCallback(async () => {
    const p = await getProject(pid);
    setProject(p);
    setActiveProject(p); // the header shows the patch size
  }, [pid, setActiveProject]);

  useEffect(() => {
    refresh().catch(() => pushToast("Failed to load the project settings", "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  if (!project) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;
  const isImage = project.project_type === "image";
  const config = project.active_config;

  return (
    <div className="max-w-5xl mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      <div>
        <div className="text-label-sm text-on-surface-variant mb-1">
          <Link to="/projects" className="hover:underline">
            Projects
          </Link>{" "}
          &rsaquo; <Link to={`/projects/${pid}`} className="hover:underline">{project.slug}</Link> &rsaquo; Settings
        </div>
        <h1 className="font-headline-lg text-headline-lg">Project settings</h1>
      </div>

      <Card className="p-space-md flex flex-col gap-space-sm">
        <div className="flex items-center justify-between">
          <h2 className="font-headline-sm text-headline-sm">Project details</h2>
          {canManage && (
            <Button icon="edit" onClick={() => setDetailsOpen(true)}>
              Edit details
            </Button>
          )}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md text-body-md">
          <Kv label="Name" value={project.name} />
          <Kv label="Cancer type" value={project.organ ?? "--"} />
          <Kv label="Team" value={project.team || "--"} />
          <Kv label="Status" value={project.status} />
        </div>
        {project.description && <p className="text-body-md text-on-surface-variant">{project.description}</p>}
      </Card>

      <MembersPanel projectId={pid} canEdit={canManage} currentUserId={user?.id} />

      {!canManage && (
        <p className="text-body-sm text-on-surface-variant">Only project managers and administrators can change this project&apos;s settings.</p>
      )}

      {canManage && config && !isImage && <PatchSizesPanel projectId={pid} config={config} onConfigChanged={() => refresh()} />}

      {canManage && config && (
        <Card className="p-space-md flex flex-col gap-space-md">
          <h2 className="font-headline-sm text-headline-sm">Configuration</h2>
          <ConfigEditor key={config.id} config={config} projectType={project.project_type} onSaved={() => refresh()} />
        </Card>
      )}

      <EditProjectModal
        open={detailsOpen}
        onClose={() => setDetailsOpen(false)}
        project={project}
        onSaved={() => {
          setDetailsOpen(false);
          refresh();
        }}
      />
    </div>
  );
}

function Kv({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-label-sm text-on-surface-variant">{label}</div>
      <div>{value}</div>
    </div>
  );
}
