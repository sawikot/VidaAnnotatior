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

  const push = useCallback(
    (cmd: Command) => {
      undoStack.current.push(cmd);
      redoStack.current = [];
      sync();
    },
    [sync],
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

  return { push, reset, undo, redo, canUndo, canRedo };
}
