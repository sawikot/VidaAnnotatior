import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { MaterialIcon } from "./MaterialIcon";
import type { NavItem } from "./navConfig";

/** The section links as one dropdown: a button naming the current page that opens the full list. */
export function HeaderNavMenu({ items, projectId, slideId }: { items: NavItem[]; projectId?: number; slideId?: number }) {
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => setOpen(false), [pathname]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const entries = items.map((item) => ({
    item,
    to: item.path(projectId, slideId),
    disabled: (item.requiresProject && !projectId) || (item.requiresProjectSlide && !(projectId && slideId)),
  }));

  // The current page is the entry whose link is the longest prefix of the URL, so the
  // Dashboard (/projects/2) isn't mistaken for a page beneath it (/projects/2/images).
  const current = entries
    .filter((e) => !e.disabled && (pathname === e.to || (e.to !== "/projects" && pathname.startsWith(e.to + "/"))))
    .sort((a, b) => b.to.length - a.to.length)[0];

  return (
    <div ref={root} className="relative">
      <button
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className={`flex items-center gap-1.5 pl-space-sm pr-1.5 py-1.5 rounded-lg text-label-md whitespace-nowrap transition-colors ${
          open ? "bg-[#1e293b] text-white" : "text-slate-300 hover:text-white hover:bg-[#1e293b]/60"
        }`}
      >
        <MaterialIcon name={current?.item.icon ?? "menu"} className="!text-[18px]" />
        <span className="font-headline-sm">{current?.item.label ?? "Menu"}</span>
        <MaterialIcon name={open ? "expand_less" : "expand_more"} className="!text-[18px]" />
      </button>

      {open && (
        <div
          role="menu"
          className="absolute left-0 top-full mt-1 w-56 rounded-lg bg-[#0f172a] border border-[#1e293b] shadow-xl py-1 z-50"
        >
          {entries.map(({ item, to, disabled }) => {
            const isCurrent = current?.item.key === item.key;
            const base = "flex items-center gap-space-sm px-space-md py-2 text-label-md";
            if (disabled) {
              return (
                <div
                  key={item.key}
                  role="menuitem"
                  aria-disabled="true"
                  title="Open a project (and a slide) first"
                  className={`${base} text-slate-600 cursor-not-allowed`}
                >
                  <MaterialIcon name={item.icon} className="!text-[18px]" />
                  {item.label}
                </div>
              );
            }
            return (
              <Link
                key={item.key}
                to={to}
                role="menuitem"
                onClick={() => setOpen(false)}
                className={`${base} ${isCurrent ? "bg-[#1e293b] text-white font-headline-sm" : "text-slate-300 hover:text-white hover:bg-[#1e293b]/60"}`}
              >
                <MaterialIcon name={item.icon} className="!text-[18px]" />
                {item.label}
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}
