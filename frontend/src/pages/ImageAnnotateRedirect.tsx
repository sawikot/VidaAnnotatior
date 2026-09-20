import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { firstToAnnotate } from "../features/images/imageNav";
import { listImages } from "../services/api";
import { useUiStore } from "../stores/uiStore";

/** "Annotate" entry point of an image project: jump to the first image still to do. */
export function ImageAnnotateRedirect() {
  const { projectId } = useParams();
  const pid = Number(projectId);
  const navigate = useNavigate();
  const pushToast = useUiStore((s) => s.pushToast);

  useEffect(() => {
    let cancelled = false;
    listImages(pid)
      .then((r) => {
        if (cancelled) return;
        const target = firstToAnnotate(r.items);
        if (target) {
          navigate(`/projects/${pid}/slides/${target.slide_id}/workspace`, { replace: true });
        } else {
          pushToast("Add some images to this project first", "info");
          navigate(`/projects/${pid}`, { replace: true });
        }
      })
      .catch(() => !cancelled && navigate(`/projects/${pid}`, { replace: true }));
    return () => {
      cancelled = true;
    };
  }, [pid]);

  return <div className="p-space-xl text-center text-on-surface-variant">Opening the next image...</div>;
}
