import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useContextStore } from "../../stores/contextStore";

/** For pages that only make sense for whole-slide projects (tissue detection, patch grids,
 * stitched overview): an image project sends the visitor to annotate instead. */
export function useImageProjectRedirect(projectId: number): void {
  const navigate = useNavigate();
  const activeId = useContextStore((s) => s.activeProject?.id);
  const type = useContextStore((s) => s.activeProject?.project_type);

  useEffect(() => {
    if (activeId === projectId && type === "image") navigate(`/projects/${projectId}/annotate`, { replace: true });
  }, [activeId, type, projectId, navigate]);
}
