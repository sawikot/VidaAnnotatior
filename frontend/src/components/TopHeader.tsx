import { useParams } from "react-router-dom";
import { MaterialIcon } from "./MaterialIcon";
import { HeaderNavMenu } from "./HeaderNavMenu";
import { navItemsFor } from "./navConfig";
import { useContextStore } from "../stores/contextStore";
import { useUiStore } from "../stores/uiStore";
import { useCan } from "../stores/authStore";
import { UserMenu } from "./UserMenu";

export function TopHeader() {
  const params = useParams();
  const projectId = params.projectId ? Number(params.projectId) : undefined;
  const slideId = params.slideId ? Number(params.slideId) : undefined;
  const activeProject = useContextStore((s) => s.activeProject);
  const setShortcutsOpen = useUiStore((s) => s.setShortcutsOpen);
  const canManage = useCan().manage;

  const projectPill = activeProject
    ? `${activeProject.slug}: ${activeProject.name}${
        activeProject.active_config
          ? activeProject.project_type === "image"
            ? " [images]"
            : ` [${activeProject.active_config.patch_width}px @ ${activeProject.active_config.target_magnification}x]`
          : ""
      }`
    : null;

  return (
    <header className="fixed top-0 left-14 right-0 h-14 bg-[#0f172a] z-40 border-b border-[#1e293b] flex items-center px-space-md gap-space-md text-white">
      <div className="flex items-center gap-space-sm shrink-0">
        <img src="/favicon.svg" alt="" className="h-8 w-8" />
        <div className="flex flex-col leading-tight">
          <span className="font-headline-sm text-headline-sm text-white">VirtualPatch WSI Annotator</span>
          <span className="hidden xl:block text-body-sm text-slate-400">Digital Pathology Research Suite (Level-0 Coordinate Engine)</span>
        </div>
      </div>

      <div className="hidden md:block h-6 w-px bg-slate-700 shrink-0" />
      <div className="hidden md:block shrink-0">
        <HeaderNavMenu items={navItemsFor(activeProject?.project_type, canManage)} projectId={projectId} slideId={slideId} />
      </div>

      {projectPill && (
        <>
          <div className="hidden md:block h-6 w-px bg-slate-700 shrink-0" />
          <div
            title={projectPill}
            className="hidden md:flex items-center gap-1.5 px-space-sm py-1 rounded-full bg-[#1e293b] border border-slate-700 text-cyan-200 font-mono text-label-sm min-w-0 max-w-md"
          >
            <span className="w-1.5 h-1.5 rounded-full bg-cyan-400 shrink-0" />
            <span className="truncate">{projectPill}</span>
          </div>
        </>
      )}

      <div className="flex-1" />



      <button
        className="w-8 h-8 rounded-lg text-slate-400 hover:bg-[#1e293b] hover:text-white flex items-center justify-center shrink-0"
        title="Keyboard and mouse shortcuts (?)"
        aria-label="Keyboard and mouse shortcuts"
        onClick={() => setShortcutsOpen(true)}
      >
        <MaterialIcon name="help_outline" />
      </button>

      <div className="h-6 w-px bg-slate-700 shrink-0" />

      <UserMenu />
    </header>
  );
}
