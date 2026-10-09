import type { AnnotationClass, Suggestion } from "../../types/api";

// Below this size on screen a shape gets no confidence tag: it would hide the shape itself.
const TAG_MIN_PX = 28;

/**
 * A model's suggestions drawn as SVG content -- boxes and outlines, dashed, with how sure the model
 * is -- so they are never mistaken for annotations. `space` says which coordinates to draw in: a
 * patch's own pixels, or the slide's Level-0 pixels. Pointer input passes through to the drawing layer
 * underneath; deciding on them happens in the suggestions panel. A suggested patch label is no shape
 * and is not drawn.
 */
export function SuggestionOverlay({
  suggestions,
  classes,
  scale,
  highlightId = null,
  space = "patch",
}: {
  suggestions: Suggestion[];
  classes: AnnotationClass[];
  /** Screen pixels per coordinate unit: keeps strokes and tags the same size on screen at any zoom. */
  scale: number;
  highlightId?: number | null;
  space?: "patch" | "level0";
}) {
  const px = 1 / scale;
  return (
    <g pointerEvents="none" data-testid="suggestion-overlay">
      {suggestions.map((s) => {
        const points = (space === "level0" ? s.coordinates_level0 : s.coordinates_patch_local) ?? [];
        if (s.type === "patch_label" || points.length < 3) return null;
        const xs = points.map((p) => p[0]);
        const ys = points.map((p) => p[1]);
        const x = Math.min(...xs);
        const y = Math.min(...ys);
        const color = classes.find((c) => c.id === s.class_id)?.color_hex ?? "#94a3b8";
        const on = s.id === highlightId;
        const tag = `${Math.round(s.score * 100)}%`;
        const outline = points.map((p) => p.join(",")).join(" ");
        const big = (Math.max(...xs) - x) * scale >= TAG_MIN_PX || on;
        return (
          <g key={s.id}>
            {/* A dark line under the coloured dashes keeps them visible on any tissue. */}
            <polygon points={outline} fill={on ? color : "none"} fillOpacity={0.2} stroke="#0f172a" strokeWidth={(on ? 5 : 3.5) * px} strokeLinejoin="round" />
            <polygon points={outline} fill="none" stroke={color} strokeWidth={(on ? 3 : 2) * px} strokeDasharray={`${6 * px} ${4 * px}`} strokeLinejoin="round" />
            {big && (
              <>
                <rect x={x} y={y - 16 * px} width={(tag.length * 7 + 8) * px} height={15 * px} fill="#0f172a" />
                <text x={x + 4 * px} y={y - 4.5 * px} fontSize={11 * px} fill="#fff" fontFamily="ui-monospace, monospace">
                  {tag}
                </text>
              </>
            )}
          </g>
        );
      })}
    </g>
  );
}
