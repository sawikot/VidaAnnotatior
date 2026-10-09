import { useState } from "react";
import { Button } from "../../components/primitives";
import type { Slide } from "../../types/api";
import { ImportAnnotationsModal } from "./ImportAnnotationsModal";

/** The workspace's way into importing annotations made elsewhere; `onImported` reloads what is on screen. */
export function ImportAnnotationsButton({ slide, onImported }: { slide: Slide; onImported: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        variant="secondary"
        icon="upload_file"
        onClick={() => setOpen(true)}
        disabled={slide.status === "imported" || slide.status === "tissue_detected"}
        title="Import annotations from WSI JSON, GeoJSON (QuPath), COCO, ASAP/Aperio XML or CSV"
      >
        Import Annotations
      </Button>
      <ImportAnnotationsModal
        open={open}
        onClose={() => setOpen(false)}
        slideId={slide.id}
        onImported={() => {
          setOpen(false);
          onImported();
        }}
      />
    </>
  );
}
