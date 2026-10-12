import { useState } from "react";
import { Button } from "../../components/primitives";
import type { Slide } from "../../types/api";
import { ImportAnnotationsModal } from "./ImportAnnotationsModal";

/** The workspace's way into importing annotations made elsewhere; `onImported` reloads what is on screen,
 * `onClassesCreated` the classes, when the import made new ones of the file's labels. */
export function ImportAnnotationsButton({
  slide,
  onImported,
  onClassesCreated,
}: {
  slide: Slide;
  onImported: () => void;
  onClassesCreated?: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="secondary"
        icon="upload_file"
        onClick={() => setOpen(true)}
        disabled={slide.status === "error"}
        title="Import annotations from WSI JSON, GeoJSON (QuPath), COCO, ASAP/Aperio XML, CSV, or a mask image (TIFF/PNG)"
      >
        Import Annotations
      </Button>
      <ImportAnnotationsModal
        open={open}
        onClose={() => setOpen(false)}
        slideId={slide.id}
        onImported={(result) => {
          setOpen(false);
          if (result.classes_created?.length) onClassesCreated?.();
          onImported();
        }}
      />
    </>
  );
}
