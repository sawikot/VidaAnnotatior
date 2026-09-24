import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { MaterialIcon } from "../components/MaterialIcon";
import { Button, Card, ConfirmDeleteModal, StatusPill } from "../components/primitives";
import { deleteProject, listProjects } from "../services/api";
import type { Project } from "../types/api";
import { useUiStore } from "../stores/uiStore";

export function ProjectManagerPage() {
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"grid" | "table">("grid");
  const [search, setSearch] = useState("");
  const [organFilter, setOrganFilter] = useState("All");
  const [statusFilter, setStatusFilter] = useState("All");
  const [openMenuId, setOpenMenuId] = useState<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<Project | null>(null);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    refresh();
  }, []);

  function refresh() {
    setLoading(true);
    listProjects()
      .then(setProjects)
      .catch(() => pushToast("Failed to load projects", "error"))
      .finally(() => setLoading(false));
  }

  const organs = useMemo(
    () => ["All", ...Array.from(new Set(projects.map((p) => p.organ).filter(Boolean) as string[]))],
    [projects],
  );

  const filtered = useMemo(() => {
    return projects.filter((p) => {
      if (search && !`${p.name} ${p.slug}`.toLowerCase().includes(search.toLowerCase())) return false;
      if (organFilter !== "All" && p.organ !== organFilter) return false;
      if (statusFilter !== "All" && p.status !== statusFilter) return false;
      return true;
    });
  }, [projects, search, organFilter, statusFilter]);

  async function handleDeleteConfirmed() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await deleteProject(deleteTarget.id);
      pushToast(`${deleteTarget.name} deleted`, "success");
      setDeleteTarget(null);
      refresh();
    } catch {
      pushToast("Failed to delete project", "error");
    } finally {
      setDeleting(false);
    }
  }

  return (
    <div className="max-w-[1720px] mx-auto px-gutter sm:px-margin py-space-lg flex flex-col gap-space-lg">
      {/* Hero */}
      <div className="relative overflow-hidden rounded-xl bg-surface-container-low p-space-xl">
        <div className="absolute -right-20 -top-24 w-96 h-96 rounded-full bg-primary-fixed-dim/20 blur-3xl" />
        <div className="relative flex flex-col md:flex-row md:items-center gap-space-md justify-between">
          <div>
            <div className="inline-flex items-center gap-1.5 px-space-sm py-1 rounded-full bg-surface-container-lowest text-label-md text-primary mb-space-sm">
              <MaterialIcon name="hub" className="!text-[14px]" />
              Core Architecture / Dynamic Coordinate Engine Active
            </div>
            <h1 className="font-headline-lg text-headline-lg text-on-surface mb-1">
              Computational Pathology Project Repository
            </h1>
            <p className="text-body-lg text-on-surface-variant max-w-2xl">
              Manage virtual patch extraction projects across your WSI cohorts. No patch images are ever
              extracted to disk -- every coordinate is generated dynamically from the source slide.
            </p>
          </div>
          <div className="flex items-center gap-space-sm shrink-0">
            <Button variant="primary" icon="add_box" onClick={() => navigate("/projects/new")}>
              New Project
            </Button>
          </div>
        </div>
      </div>

      {/* Metrics */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-space-md">
        <MetricCard icon="folder_special" label="Cohorts" value={String(projects.length)} sub="Projects" tone="primary" />
        <MetricCard
          icon="check_circle"
          label="Active"
          value={String(projects.filter((p) => p.status === "active").length)}
          sub="In progress"
          tone="tertiary"
        />
        <MetricCard
          icon="visibility"
          label="Review Phase"
          value={String(projects.filter((p) => p.status === "review").length)}
          sub="Awaiting sign-off"
          tone="secondary"
        />
        <MetricCard
          icon="task_alt"
          label="Completed"
          value={String(projects.filter((p) => p.status === "completed").length)}
          sub="Finalized"
          tone="highlight"
        />
      </div>

      {/* Filter bar */}
      <Card className="p-space-sm flex flex-wrap items-center gap-space-sm">
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search cohort, ID, pathology tags..."
          className="flex-1 min-w-[200px] bg-surface-container-low rounded px-space-sm py-1.5 text-body-md focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <select
          value={organFilter}
          onChange={(e) => setOrganFilter(e.target.value)}
          className="bg-surface-container-low rounded px-space-sm py-1.5 text-body-md"
        >
          {organs.map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="bg-surface-container-low rounded px-space-sm py-1.5 text-body-md"
        >
          {["All", "active", "review", "completed"].map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
        <button
          className="text-label-md text-primary hover:underline"
          onClick={() => {
            setSearch("");
            setOrganFilter("All");
            setStatusFilter("All");
          }}
        >
          Reset Filters
        </button>
        <div className="flex-1" />
        <span className="text-label-md text-on-surface-variant">
          Displaying {filtered.length} of {projects.length} Projects
        </span>
        <div className="flex bg-surface-container-low rounded p-0.5">
          <button
            onClick={() => setView("grid")}
            className={`px-space-sm py-1 rounded text-label-md ${view === "grid" ? "bg-surface-container-lowest shadow-sm" : ""}`}
          >
            <MaterialIcon name="grid_view" className="!text-[16px]" />
          </button>
          <button
            onClick={() => setView("table")}
            className={`px-space-sm py-1 rounded text-label-md ${view === "table" ? "bg-surface-container-lowest shadow-sm" : ""}`}
          >
            <MaterialIcon name="table_rows" className="!text-[16px]" />
          </button>
        </div>
      </Card>

      {loading && <div className="text-center py-space-xl text-on-surface-variant">Loading projects...</div>}

      {!loading && filtered.length === 0 && (
        <Card className="p-space-xl text-center text-on-surface-variant">
          {projects.length === 0
            ? "No projects yet. Create one to get started."
            : "No projects match your filters."}
        </Card>
      )}

      {!loading && filtered.length > 0 && view === "grid" && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-space-md">
          {filtered.map((p) => (
            <ProjectCard
              key={p.id}
              project={p}
              menuOpen={openMenuId === p.id}
              onToggleMenu={() => setOpenMenuId(openMenuId === p.id ? null : p.id)}
              onOpen={() => navigate(`/projects/${p.id}`)}
              onOpenVersions={() => navigate(`/projects/${p.id}/settings`)}
              onDelete={() => {
                setOpenMenuId(null);
                setDeleteTarget(p);
              }}
            />
          ))}
        </div>
      )}

      {!loading && filtered.length > 0 && view === "table" && (
        <Card className="overflow-x-auto">
          <table className="w-full text-body-md">
            <thead className="bg-surface-container-low text-label-md text-on-surface-variant">
              <tr>
                <th className="text-left px-space-md py-space-sm">Cohort / Project ID</th>
                <th className="text-left px-space-md py-space-sm">Organ</th>
                <th className="text-left px-space-md py-space-sm">Status</th>
                <th className="text-left px-space-md py-space-sm">Slides</th>
                <th className="text-left px-space-md py-space-sm">Patches</th>
                <th className="text-left px-space-md py-space-sm">Modified</th>
                <th className="text-left px-space-md py-space-sm">Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((p) => (
                <tr key={p.id} className="border-t border-outline-variant hover:bg-surface-container-low/50">
                  <td className="px-space-md py-space-sm">
                    <div className="font-headline-sm text-headline-sm">{p.name}</div>
                    <div className="font-mono text-label-sm text-secondary">
                      {p.slug} &middot; {p.project_type === "image" ? "images" : "WSI"}
                    </div>
                  </td>
                  <td className="px-space-md py-space-sm">{p.organ ?? "--"}</td>
                  <td className="px-space-md py-space-sm">
                    <StatusPill status={p.status} />
                  </td>
                  <td className="px-space-md py-space-sm font-mono">--</td>
                  <td className="px-space-md py-space-sm font-mono">--</td>
                  <td className="px-space-md py-space-sm text-on-surface-variant">
                    {new Date(p.updated_at).toLocaleDateString()}
                  </td>
                  <td className="px-space-md py-space-sm">
                    <Button variant="primary" onClick={() => navigate(`/projects/${p.id}`)}>
                      Open
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      <ConfirmDeleteModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={handleDeleteConfirmed}
        busy={deleting}
        title="Delete this project?"
        confirmPhrase={deleteTarget?.slug ?? ""}
        description={
          <>
            This permanently deletes <strong>{deleteTarget?.name}</strong> and everything under it -- every
            slide, generated patch coordinate, annotation, and config version. WSI files and cached tissue masks
            on disk are also removed. This cannot be undone.
          </>
        }
      />
    </div>
  );
}

function MetricCard({
  icon,
  label,
  value,
  sub,
  tone,
}: {
  icon: string;
  label: string;
  value: string;
  sub: string;
  tone: "primary" | "secondary" | "tertiary" | "highlight";
}) {
  const bar: Record<string, string> = {
    primary: "bg-primary",
    secondary: "bg-secondary",
    tertiary: "bg-tertiary",
    highlight: "bg-error",
  };
  return (
    <Card className={`p-space-md relative overflow-hidden ${tone === "highlight" ? "bg-surface-container-high" : ""}`}>
      <div className={`absolute left-0 top-0 bottom-0 w-1 ${bar[tone]}`} />
      <div className="flex items-center gap-space-sm text-on-surface-variant mb-1">
        <MaterialIcon name={icon} className="!text-[18px]" />
        <span className="text-label-md">{label}</span>
      </div>
      <div className="font-headline-lg text-headline-lg text-on-surface">{value}</div>
      <div className="text-body-sm text-on-surface-variant">{sub}</div>
    </Card>
  );
}

function ProjectCard({
  project,
  menuOpen,
  onToggleMenu,
  onOpen,
  onOpenVersions,
  onDelete,
}: {
  project: Project;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onOpen: () => void;
  onOpenVersions: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex flex-col bg-surface-container-lowest rounded shadow-sm group hover:shadow-md transition-shadow">
      <div className="h-24 rounded-t bg-gradient-to-br from-primary-fixed to-primary-fixed-dim relative flex items-center justify-between px-space-sm py-space-sm">
        <span className="flex items-center gap-1">
          {project.organ && (
            <span className="px-space-sm py-0.5 rounded-full bg-surface-container-lowest/90 backdrop-blur text-label-sm">
              {project.organ}
            </span>
          )}
          <span className="px-space-sm py-0.5 rounded-full bg-surface-container-lowest/90 backdrop-blur text-label-sm">
            {project.project_type === "image" ? "Images" : "WSI"}
          </span>
        </span>
        <StatusPill status={project.status} />
      </div>
      <div className="p-space-md flex-1 flex flex-col gap-space-sm">
        <div>
          <div className="font-mono text-label-sm text-primary">PROJECT ID: {project.slug}</div>
          <h2 className="font-headline-md text-headline-md text-on-surface">{project.name}</h2>
          <p className="text-body-md text-on-surface-variant line-clamp-2">
            {project.description || "No description provided."}
          </p>
        </div>
      </div>
      <div className="px-space-md pb-space-md flex items-center justify-between gap-space-sm">
        <span className="text-body-sm text-on-surface-variant">{project.team || "Unassigned"}</span>
        <div className="flex items-center gap-1 relative">
          <Button variant="primary" icon="arrow_forward" onClick={onOpen}>
            Workspace
          </Button>
          <button
            onClick={onToggleMenu}
            className="w-8 h-8 rounded flex items-center justify-center hover:bg-surface-container-low"
          >
            <MaterialIcon name="more_vert" />
          </button>
          {menuOpen && (
            <div className="absolute right-0 top-9 z-20 w-44 bg-surface-container-lowest rounded shadow-lg border border-outline-variant py-1">
              <MenuItem icon="grid_view" label="Dashboard" onClick={onOpen} />
              <MenuItem icon="settings" label="Settings" onClick={onOpenVersions} />
              <MenuItem icon="file_download" label="Export (pick a slide)" onClick={onOpen} />
              <div className="h-px bg-outline-variant my-1" />
              <MenuItem icon="delete" label="Delete Project" onClick={onDelete} destructive />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  destructive,
}: {
  icon: string;
  label: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-2 px-space-md py-1.5 text-body-md text-left ${
        destructive ? "text-error hover:bg-error-container" : "hover:bg-surface-container-low"
      }`}
    >
      <MaterialIcon name={icon} className="!text-[16px]" />
      {label}
    </button>
  );
}
