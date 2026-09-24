import { NavLink, useParams } from "react-router-dom";
import { useCan } from "../stores/authStore";
import { MaterialIcon } from "./MaterialIcon";
import { useContextStore } from "../stores/contextStore";
import { navItemsFor } from "./navConfig";
import { NavIconLink } from "./NavIconLink";

export function IconRail() {
  const params = useParams();
  const projectId = params.projectId ? Number(params.projectId) : undefined;
  const slideId = params.slideId ? Number(params.slideId) : undefined;
  const projectType = useContextStore((s) => s.activeProject?.project_type);
  const { manage, admin } = useCan();

  return (
    <aside className="fixed left-0 top-0 h-full w-14 bg-[#0f172a] z-50 flex flex-col justify-between items-center py-space-sm border-r border-[#1e293b]">
      <div className="flex flex-col items-center gap-space-sm w-full">
        <div className="h-8 w-8 rounded-lg bg-[#1e293b] flex items-center justify-center">
          <MaterialIcon name="biotech" className="text-primary-fixed-dim" />
        </div>
        <div className="h-px w-8 bg-[#1e293b]" />
        {navItemsFor(projectType, manage).map((item) => (
          <NavIconLink key={item.key} item={item} projectId={projectId} slideId={slideId} />
        ))}
      </div>
      {/* App-wide, not per project: the administrator's user management sits apart, at the bottom. */}
      {admin && (
        <div className="flex flex-col items-center w-full">
          <NavLink
            to="/admin/users"
            title="Users"
            className={({ isActive }) =>
              `w-10 h-10 rounded-lg flex items-center justify-center transition-colors ${
                isActive ? "bg-[#007bb9] text-[#fdfcff] shadow-sm" : "text-slate-400 hover:bg-[#1e293b] hover:text-white"
              }`
            }
          >
            <MaterialIcon name="group" />
          </NavLink>
        </div>
      )}
    </aside>
  );
}
