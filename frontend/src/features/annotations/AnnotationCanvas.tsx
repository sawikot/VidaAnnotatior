import type { Point } from "../../utils/coordinates";
import type { AnnotationTool } from "../../stores/annotationStore";
import type { AnnotationClass, GeometryAnnotation, GeometryType } from "../../types/api";
import { ShapeLayer, type LayerShape } from "./ShapeLayer";

interface Props {
  imageUrl: string;
  patchWidth: number;
  patchHeight: number;
  tool: AnnotationTool;
  zoom: number;
  annotations: GeometryAnnotation[];
  /** Annotations drawn on the whole slide, already projected into this patch's pixels: shown faintly, edited in WSI mode. */
  background?: LayerShape[];
  onBackgroundPress?: (id: number) => void;
  classes: AnnotationClass[];
  selectedId: number | null;
  onSelect: (id: number | null) => void;
  onShapeComplete: (type: GeometryType, points: Point[]) => void;
  /** A shape's new points once the Select tool finishes moving or reshaping it. */
  onShapeEdit: (id: number, points: Point[]) => void;
  onDeleteSelected: () => void;
}

/** One patch: its image with the drawing/editing layer on top, in the patch's own pixels. */
export function AnnotationCanvas({
  imageUrl,
  patchWidth,
  patchHeight,
  tool,
  zoom,
  annotations,
  background,
  onBackgroundPress,
  classes,
  selectedId,
  onSelect,
  onShapeComplete,
  onShapeEdit,
  onDeleteSelected,
}: Props) {
  const shapes: LayerShape[] = annotations.map((a) => ({
    id: a.id,
    type: a.type,
    points: a.coordinates_patch_local as Point[],
    class_id: a.class_id,
    unsure: a.unsure,
    excluded: a.excluded,
  }));

  return (
    <div className="relative inline-block outline-none">
      <img
        src={imageUrl}
        alt="Patch"
        width={patchWidth * zoom}
        height={patchHeight * zoom}
        className="block select-none max-w-none" // never capped to the container: it must match the drawing layer exactly
        style={{ imageRendering: zoom >= 3 ? "pixelated" : "auto" }}
        draggable={false}
      />
      <svg
        viewBox={`0 0 ${patchWidth} ${patchHeight}`}
        width={patchWidth * zoom}
        height={patchHeight * zoom}
        className="absolute inset-0"
        style={{ touchAction: "none" }}
      >
        <ShapeLayer
          width={patchWidth}
          height={patchHeight}
          scale={zoom}
          tool={tool}
          shapes={shapes}
          background={background}
          onBackgroundPress={onBackgroundPress}
          classes={classes}
          selectedId={selectedId}
          onSelect={onSelect}
          onShapeComplete={onShapeComplete}
          onShapeEdit={onShapeEdit}
          onDeleteSelected={onDeleteSelected}
          resetKey={imageUrl}
        />
      </svg>
    </div>
  );
}
