import { useEffect, useState } from "react";
import { getTissueMaskOutline } from "../../services/api";

const MASK_COLOR = "#3b82f6";

interface Props {
  slideId: number;
  /** Changes whenever the mask is rebuilt (detection, region edits), to fetch the new outline. */
  refreshKey?: unknown;
  /** Fill opacity: 0 = outline only, 1 = solid. The border is always drawn. */
  opacity?: number;
}

/**
 * The tissue mask as a blue, lightly filled outline -- drawn as vectors in Level-0 pixels (inside
 * WsiViewer's SVG), so the border stays a thin crisp line at any zoom, like drawn regions and annotations.
 * One path with the even-odd rule: holes in the tissue stay unfilled.
 */
export function TissueMaskOutline({ slideId, refreshKey, opacity = 0.18 }: Props) {
  const [d, setD] = useState("");

  useEffect(() => {
    let cancelled = false;
    getTissueMaskOutline(slideId)
      .then(({ rings }) => {
        if (cancelled) return;
        setD(rings.map((ring) => `M${ring.map(([x, y]) => `${x},${y}`).join("L")}Z`).join(""));
      })
      .catch(() => !cancelled && setD(""));
    return () => {
      cancelled = true;
    };
  }, [slideId, refreshKey]);

  if (!d) return null;
  return (
    <path
      d={d}
      fill={MASK_COLOR}
      fillOpacity={opacity}
      fillRule="evenodd"
      stroke={MASK_COLOR}
      strokeWidth={1.5}
      strokeLinejoin="round"
      vectorEffect="non-scaling-stroke"
      style={{ pointerEvents: "none" }}
    />
  );
}
