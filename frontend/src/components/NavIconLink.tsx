import { NavLink } from "react-router-dom";
import { MaterialIcon } from "./MaterialIcon";
import type { NavItem } from "./navConfig";

export function NavIconLink({
  item,
  projectId,
  slideId,
}: {
  item: NavItem;
  projectId?: number;
  slideId?: number;
}) {
  const disabled =
    (item.requiresProject && !projectId) || (item.requiresProjectSlide && !(projectId && slideId));
  const to = item.path(projectId, slideId);

  return (
    <NavLink
      to={to}
      title={item.label}
      aria-disabled={disabled}
      onClick={(e) => disabled && e.preventDefault()}
      className={({ isActive }) =>
        `relative w-10 h-10 rounded-lg flex items-center justify-center transition-colors ${
          disabled
            ? "text-slate-600 cursor-not-allowed"
            : isActive
              ? "bg-[#007bb9] text-[#fdfcff] shadow-sm"
              : "text-slate-400 hover:bg-[#1e293b] hover:text-white"
        }`
      }
    >
      <MaterialIcon name={item.icon} />
      {item.key === "workspace-annotator" && !disabled && (
        <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-emerald-400 ring-2 ring-[#0f172a]" />
      )}
    </NavLink>
  );
}
