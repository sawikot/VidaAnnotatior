import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card } from "../components/primitives";
import { ConfigEditor, EditConfigModal } from "../features/projects/EditConfigModal";
import { gridSummary } from "../features/projects/configDraft";
import { EditProjectModal } from "../features/projects/EditProjectModal";
import { getConfigUsage, getProject, listConfigs, lockConfig, updateProject } from "../services/api";
import { useUiStore } from "../stores/uiStore";
import type { ConfigUsage, ConfigVersion, ProjectDetail } from "../types/api";

/**
 * Everything about a project in one place: its details, and every setting of its configuration (patch
 * grid, tissue detection, classes, tools, QC) edited in place. The versions a configuration went through
 * are listed underneath for traceability.
 */
export function ProjectSettingsPage() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const pushToast = useUiStore((s) => s.pushToast);

  const [project, setProject] = useState<ProjectDetail | null>(null);
  const [configs, setConfigs] = useState<ConfigVersion[]>([]);
  const [usage, setUsage] = useState<Record<number, ConfigUsage>>({});
  const [editingId, setEditingId] = useState<number | null>(null);
  const [forkFrom, setForkFrom] = useState<ConfigVersion | null>(null);
  const [detailsOpen, setDetailsOpen] = useState(false);

  const refresh = useCallback(async () => {
    const [p, c] = await Promise.all([getProject(pid), listConfigs(pid)]);
    setProject(p);
    setConfigs(c);
    setEditingId((id) => (id !== null && c.some((x) => x.id === id) ? id : p.active_config_version_id));
    const entries = await Promise.all(c.map((cfg) => getConfigUsage(cfg.id).then((u) => [cfg.id, u] as const).catch(() => null)));
    setUsage(Object.fromEntries(entries.filter((e): e is [number, ConfigUsage] => e !== null)));
  }, [pid]);

  useEffect(() => {
    refresh().catch(() => pushToast("Failed to load the project settings", "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pid]);

  async function act(work: () => Promise<unknown>, done: string, failed: string) {
    try {
      await work();
      pushToast(done, "success");
      await refresh();
    } catch (e) {
      pushToast(e instanceof Error ? e.message : failed, "error");
    }
  }

  if (!project) return <div className="p-space-xl text-center text-on-surface-variant">Loading...</div>;
  const isImage = project.project_type === "image";
  const editing = configs.find((c) => c.id === editingId) ?? null;
  const labels = configs.map((c) => c.version_label);

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
          <Button icon="edit" onClick={() => setDetailsOpen(true)}>
            Edit details
          </Button>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-space-md text-body-md">
          <Kv label="Name" value={project.name} />
          <Kv label="Cancer type" value={project.organ ?? "--"} />
          <Kv label="Team" value={project.team || "--"} />
          <Kv label="Status" value={project.status} />
        </div>
        {project.description && <p className="text-body-md text-on-surface-variant">{project.description}</p>}
      </Card>

      <Card className="p-space-md flex flex-col gap-space-md">
        <div className="flex items-center justify-between flex-wrap gap-space-sm">
          <div>
            <h2 className="font-headline-sm text-headline-sm">Configuration</h2>
            {editing && !isImage && <div className="text-body-sm text-on-surface-variant">Patch grid: {gridSummary(editing)}</div>}
          </div>
          {configs.length > 1 && (
            <label className="flex items-center gap-space-sm text-label-md text-on-surface-variant">
              Editing
              <select className="input !w-auto" value={editingId ?? ""} onChange={(e) => setEditingId(Number(e.target.value))}>
                {configs.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.version_label}
                    {c.id === project.active_config_version_id ? " (used for new slides)" : ""}
                    {c.status === "locked" ? " - locked" : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
        </div>
        {editing && (
          <ConfigEditor
            key={editing.id}
            config={editing}
            mode="edit"
            projectType={project.project_type}
            existingLabels={labels}
            onSaved={({ kind, config }) => {
              if (kind === "forked") setEditingId(config.id);
              refresh();
            }}
          />
        )}
      </Card>

      <Card className="p-space-md flex flex-col gap-space-sm">
        <div className="flex items-center justify-between flex-wrap gap-space-sm">
          <h2 className="font-headline-sm text-headline-sm">Version history</h2>
          {!isImage && editing && (
            <Button icon="call_split" onClick={() => setForkFrom(editing)}>
              Save a copy as a new version
            </Button>
          )}
        </div>
        <p className="text-body-sm text-on-surface-variant">
          Every configuration version this project has had. Locking one freezes its patch grid for reproducibility; a
          slide can be switched to another version on Slide Processing.
        </p>
        <div className="overflow-x-auto">
          <table className="w-full text-body-sm">
            <thead className="text-label-md text-on-surface-variant text-left">
              <tr>
                <th className="py-1 pr-space-md">Version</th>
                {!isImage && <th className="py-1 pr-space-md">Patch grid</th>}
                <th className="py-1 pr-space-md text-right">Slides</th>
                <th className="py-1 pr-space-md text-right">Patches</th>
                <th className="py-1 pr-space-md text-right">Annotations</th>
                <th className="py-1" />
              </tr>
            </thead>
            <tbody>
              {configs.map((c) => {
                const u = usage[c.id];
                const isDefault = c.id === project.active_config_version_id;
                return (
                  <tr key={c.id} className="border-t border-outline-variant">
                    <td className="py-1.5 pr-space-md">
                      <span className="font-mono">{c.version_label}</span>
                      {isDefault && <span className="ml-1.5 px-1.5 rounded-full bg-primary-fixed text-label-sm">new slides</span>}
                      {c.status === "locked" && <MaterialIcon name="lock" className="!text-[14px] ml-1 text-on-surface-variant align-middle" />}
                      {c.title && <div className="text-on-surface-variant">{c.title}</div>}
                    </td>
                    {!isImage && <td className="py-1.5 pr-space-md">{gridSummary(c)}</td>}
                    <td className="py-1.5 pr-space-md text-right font-mono">{u?.slide_count ?? "-"}</td>
                    <td className="py-1.5 pr-space-md text-right font-mono">{u?.patch_count.toLocaleString() ?? "-"}</td>
                    <td className="py-1.5 pr-space-md text-right font-mono">{u?.annotation_count.toLocaleString() ?? "-"}</td>
                    <td className="py-1.5 text-right whitespace-nowrap">
                      <button className="text-primary hover:underline mr-space-sm" onClick={() => setEditingId(c.id)}>
                        Edit
                      </button>
                      {!isDefault && (
                        <button
                          className="text-primary hover:underline mr-space-sm"
                          onClick={() => act(() => updateProject(pid, { active_config_version_id: c.id }), `New slides will start on ${c.version_label}`, "Failed to change the default")}
                        >
                          Use for new slides
                        </button>
                      )}
                      {c.status !== "locked" && !isImage && (
                        <button
                          className="text-on-surface-variant hover:underline"
                          onClick={() => act(() => lockConfig(c.id), `${c.version_label} locked`, "Failed to lock the version")}
                        >
                          Lock
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <EditConfigModal
        open={!!forkFrom}
        onClose={() => setForkFrom(null)}
        config={forkFrom}
        mode="fork"
        projectType={project.project_type}
        existingLabels={labels}
        onSaved={({ config }) => {
          setForkFrom(null);
          setEditingId(config.id);
          refresh();
        }}
      />
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
