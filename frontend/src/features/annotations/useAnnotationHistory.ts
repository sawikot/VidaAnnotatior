import { useCallback, useRef, useState } from "react";

export interface Command {
  label: string;
  do: () => Promise<void>;
  undo: () => Promise<void>;
}

/** Session-scoped undo/redo command stack for the current patch's annotation
 * edits. Resets whenever the caller calls `reset()` (e.g. on patch navigation).
 * Each command's `do`/`undo` perform the actual API calls, so undo/redo stay
 * consistent with the persisted (autosaved) state rather than diverging from it. */
export function useAnnotationHistory() {
  const [canUndo, setCanUndo] = useState(false);
  const [canRedo, setCanRedo] = useState(false);
  const undoStack = useRef<Command[]>([]);
  const redoStack = useRef<Command[]>([]);

  const sync = useCallback(() => {
    setCanUndo(undoStack.current.length > 0);
    setCanRedo(redoStack.current.length > 0);
  }, []);

  const batching = useRef<Command[] | null>(null);

  const push = useCallback(
    (cmd: Command) => {
      if (batching.current) {
        batching.current.push(cmd);
        return;
      }
      undoStack.current.push(cmd);
      redoStack.current = [];
      sync();
    },
    [sync],
  );

  /** Everything pushed while `work` runs becomes one step: undone together, last first. */
  const batch = useCallback(
    async (label: string, work: () => Promise<void>) => {
      const cmds: Command[] = [];
      batching.current = cmds;
      try {
        await work();
      } finally {
        batching.current = null;
        if (cmds.length === 1) push(cmds[0]);
        else if (cmds.length > 1) {
          push({
            label,
            do: async () => {
              for (const cmd of cmds) await cmd.do();
            },
            undo: async () => {
              for (const cmd of [...cmds].reverse()) await cmd.undo();
            },
          });
        }
      }
    },
    [push],
  );

  const reset = useCallback(() => {
    undoStack.current = [];
    redoStack.current = [];
    sync();
  }, [sync]);

  const undo = useCallback(async () => {
    const cmd = undoStack.current.pop();
    if (!cmd) return;
    await cmd.undo();
    redoStack.current.push(cmd);
    sync();
  }, [sync]);

  const redo = useCallback(async () => {
    const cmd = redoStack.current.pop();
    if (!cmd) return;
    await cmd.do();
    undoStack.current.push(cmd);
    sync();
  }, [sync]);

  return { push, batch, reset, undo, redo, canUndo, canRedo };
}
