import { useUiStore } from "../stores/uiStore";
import { MaterialIcon } from "./MaterialIcon";

const TONE_STYLES: Record<string, string> = {
  info: "bg-[#0f172a] text-white border-slate-700",
  success: "bg-emerald-900 text-emerald-100 border-emerald-700",
  error: "bg-red-950 text-red-100 border-red-700",
};

const TONE_ICON: Record<string, string> = {
  info: "info",
  success: "check_circle",
  error: "error",
};

export function ToastHost() {
  const toasts = useUiStore((s) => s.toasts);
  const dismissToast = useUiStore((s) => s.dismissToast);

  if (!toasts.length) return null;

  return (
    <div className="fixed bottom-6 right-6 z-[100] flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`flex items-center gap-2 px-space-md py-space-sm rounded-lg border shadow-lg text-body-md ${TONE_STYLES[t.tone]}`}
          onClick={() => dismissToast(t.id)}
        >
          <MaterialIcon name={TONE_ICON[t.tone]} className="!text-[16px]" />
          {t.message}
        </div>
      ))}
    </div>
  );
}
