import { useParams } from "react-router-dom";
import { MaterialIcon } from "./MaterialIcon";
import { useContextStore } from "../stores/contextStore";
import { navItemsFor } from "./navConfig";
import { NavIconLink } from "./NavIconLink";

export function IconRail() {
  const params = useParams();
  const projectId = params.projectId ? Number(params.projectId) : undefined;
  const slideId = params.slideId ? Number(params.slideId) : undefined;
  const projectType = useContextStore((s) => s.activeProject?.project_type);

  return (
    <aside className="fixed left-0 top-0 h-full w-14 bg-[#0f172a] z-50 flex flex-col justify-between items-center py-space-sm border-r border-[#1e293b]">
      <div className="flex flex-col items-center gap-space-sm w-full">
        <div className="h-8 w-8 rounded-lg bg-[#1e293b] flex items-center justify-center">
          <MaterialIcon name="biotech" className="text-primary-fixed-dim" />
        </div>
        <div className="h-px w-8 bg-[#1e293b]" />
        {navItemsFor(projectType).map((item) => (
          <NavIconLink key={item.key} item={item} projectId={projectId} slideId={slideId} />
        ))}
      </div>
      <div className="flex flex-col items-center gap-space-sm w-full">
        <button
          className="w-10 h-10 rounded-lg text-slate-400 hover:bg-[#1e293b] hover:text-white flex items-center justify-center"
          title="Documentation"
        >
          <MaterialIcon name="menu_book" />
        </button>
        <button
          className="w-10 h-10 rounded-lg text-slate-400 hover:bg-[#1e293b] hover:text-white flex items-center justify-center"
          title="System Settings"
        >
          <MaterialIcon name="settings" />
        </button>
      </div>
    </aside>
  );
}
