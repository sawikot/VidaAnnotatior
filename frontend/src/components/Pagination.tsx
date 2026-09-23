import { useState } from "react";
import { PAGE_SIZES, pageItems } from "../utils/pagination";
import { MaterialIcon } from "./MaterialIcon";

interface Props {
  /** 0-based */
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  onPageSize?: (size: number) => void;
  /** What is being paged, for "Showing 1-24 of 4,258 patches". */
  noun?: string;
}

/** Page buttons with gaps (1 ... 5 6 7 ... 178), first/last, a "go to page" box and the page size. */
export function Pagination({ page, pageSize, total, onPage, onPageSize, noun = "items" }: Props) {
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, pageCount - 1);
  const [jump, setJump] = useState("");
  const go = (p: number) => onPage(Math.min(Math.max(p, 0), pageCount - 1));

  function submitJump() {
    const n = Number(jump);
    if (Number.isInteger(n) && n >= 1) go(n - 1);
    setJump("");
  }

  const from = total === 0 ? 0 : current * pageSize + 1;
  const to = Math.min(total, (current + 1) * pageSize);

  return (
    <nav className="flex flex-wrap items-center justify-between gap-space-sm" aria-label="Pagination">
      <div className="flex items-center gap-space-md text-label-md text-on-surface-variant">
        <span>
          Showing {from.toLocaleString()}–{to.toLocaleString()} of {total.toLocaleString()} {noun}
        </span>
        {onPageSize && (
          <label className="flex items-center gap-1.5">
            Per page
            <select className="input !w-auto !py-0.5" value={pageSize} onChange={(e) => onPageSize(Number(e.target.value))}>
              {PAGE_SIZES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="flex flex-wrap items-center gap-1">
        <PageButton icon="first_page" label="First page" disabled={current === 0} onClick={() => go(0)} />
        <PageButton icon="chevron_left" label="Previous page" disabled={current === 0} onClick={() => go(current - 1)} />
        {pageItems(current, pageCount).map((item, i) =>
          item === "gap" ? (
            <span key={`gap-${i}`} className="w-8 text-center text-on-surface-variant select-none">
              …
            </span>
          ) : (
            <button
              key={item}
              onClick={() => go(item)}
              aria-current={item === current ? "page" : undefined}
              className={`min-w-[2rem] h-8 px-1.5 rounded font-mono text-label-md transition-colors ${
                item === current ? "bg-primary text-on-primary" : "text-on-surface-variant hover:bg-surface-container-low"
              }`}
            >
              {item + 1}
            </button>
          ),
        )}
        <PageButton icon="chevron_right" label="Next page" disabled={current >= pageCount - 1} onClick={() => go(current + 1)} />
        <PageButton icon="last_page" label="Last page" disabled={current >= pageCount - 1} onClick={() => go(pageCount - 1)} />

        {pageCount > 7 && (
          <form
            className="flex items-center gap-1 ml-space-sm text-label-md text-on-surface-variant"
            onSubmit={(e) => {
              e.preventDefault();
              submitJump();
            }}
          >
            Go to
            <input
              className="input !w-16 !py-0.5 text-center font-mono"
              inputMode="numeric"
              placeholder={String(current + 1)}
              value={jump}
              onChange={(e) => setJump(e.target.value.replace(/\D/g, ""))}
              aria-label={`Go to page (1-${pageCount})`}
            />
            <button type="submit" className="h-8 px-space-sm rounded hover:bg-surface-container-low" disabled={!jump}>
              Go
            </button>
          </form>
        )}
      </div>
    </nav>
  );
}

function PageButton({ icon, label, disabled, onClick }: { icon: string; label: string; disabled: boolean; onClick: () => void }) {
  return (
    <button
      title={label}
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="w-8 h-8 rounded flex items-center justify-center text-on-surface-variant hover:bg-surface-container-low disabled:opacity-30 disabled:cursor-not-allowed"
    >
      <MaterialIcon name={icon} className="!text-[20px]" />
    </button>
  );
}
