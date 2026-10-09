import { useEffect, useState } from "react";
import { getTrainerStatus, listProjectModels } from "../../services/api";
import type { TrainedModel } from "../../types/api";

const MODEL_KEY = "vp.suggestModel"; // the model last used, per project, kept in this browser

function remembered(projectId: number): number | null {
  try {
    return Number(localStorage.getItem(`${MODEL_KEY}.${projectId}`)) || null;
  } catch {
    return null;
  }
}

/** The project's models, the one picked for suggestions (remembered per project), and whether the trainer is running. */
export function useSuggestModels(projectId: number) {
  const [models, setModels] = useState<TrainedModel[] | null>(null);
  const [modelId, setModelIdState] = useState<number | null>(null);
  const [online, setOnline] = useState<boolean | null>(null);

  const refreshOnline = () => getTrainerStatus().then((s) => setOnline(s.online)).catch(() => setOnline(false));

  useEffect(() => {
    listProjectModels(projectId)
      .then((list) => {
        setModels(list);
        const last = remembered(projectId);
        setModelIdState(list.some((m) => m.id === last) ? last : (list[0]?.id ?? null));
      })
      .catch(() => setModels([]));
    void refreshOnline();
  }, [projectId]);

  function setModelId(id: number) {
    setModelIdState(id);
    try {
      localStorage.setItem(`${MODEL_KEY}.${projectId}`, String(id));
    } catch {
      /* remembering the model is a convenience only */
    }
  }

  return { models, modelId, setModelId, online, setOnline, refreshOnline };
}
