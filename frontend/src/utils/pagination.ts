import { useSearchParams } from "react-router-dom";

export const PAGE_SIZES = [24, 48, 96];

/**
 * The page buttons to show (0-based page numbers), with "gap" where pages are skipped: always the
 * first and last page, and `siblings` pages either side of the current one. A gap never stands for a
 * single page (that page is shown instead), so the row keeps the same length as you move through it.
 *   pageItems(6, 178) -> [0, "gap", 5, 6, 7, "gap", 177]
 */
export function pageItems(page: number, pageCount: number, siblings = 1): (number | "gap")[] {
  if (pageCount <= 0) return [];
  const slots = 2 * siblings + 5; // first, gap, siblings, current, siblings, gap, last
  if (pageCount <= slots) return Array.from({ length: pageCount }, (_, i) => i);

  const last = pageCount - 1;
  const current = Math.min(Math.max(page, 0), last);
  const band = 2 * siblings + 3; // pages shown next to an end when the other end has the gap
  if (current <= siblings + 2) {
    return [...Array.from({ length: band }, (_, i) => i), "gap", last];
  }
  if (current >= last - siblings - 2) {
    return [0, "gap", ...Array.from({ length: band }, (_, i) => last - band + 1 + i)];
  }
  return [0, "gap", ...Array.from({ length: 2 * siblings + 1 }, (_, i) => current - siblings + i), "gap", last];
}

/**
 * The page and page size, kept in the URL (?page=, 1-based, and ?size=): leaving for a patch and
 * pressing Back returns to the same page, and a page can be linked to.
 */
export function usePageParams(defaultSize = PAGE_SIZES[0]) {
  const [params, setParams] = useSearchParams();
  const page = Math.max(0, (Number(params.get("page")) || 1) - 1);
  const asked = Number(params.get("size"));
  const pageSize = PAGE_SIZES.includes(asked) ? asked : defaultSize;

  const update = (changes: Record<string, string | null>) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        for (const [k, v] of Object.entries(changes)) {
          if (v === null) next.delete(k);
          else next.set(k, v);
        }
        return next;
      },
      { replace: true },
    );

  return {
    page,
    pageSize,
    setPage: (p: number) => update({ page: p > 0 ? String(p + 1) : null }),
    /** Keeps the first item on screen in view: the new page is the one that contains it. */
    setPageSize: (size: number) => {
      const next = Math.floor((page * pageSize) / size);
      update({ size: size === defaultSize ? null : String(size), page: next > 0 ? String(next + 1) : null });
    },
  };
}
