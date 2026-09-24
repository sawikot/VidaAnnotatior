import type { ProjectDetail, Slide } from "../types/api";

/** Where a step's button takes you, for one slide. */
export type StepTarget = "processing" | "workspace" | "export";

export interface SlideStep {
  /** Short button text: what to do next with this slide. */
  label: string;
  /** One line on where the slide stands. */
  detail: string;
  target: StepTarget | null;
  /** "todo": something to do; "done": nothing left but exporting; "problem": needs fixing. */
  tone: "todo" | "done" | "problem";
}

const HAS_PATCHES = ["patches_generated", "annotating", "reviewed"];

/** What a WSI slide needs next, from its status and its progress in the active patch grid. */
export function slideNextStep(s: Slide): SlideStep {
  if (s.status === "error") {
    return { label: "Fix import", detail: s.error_message || "The slide could not be read; import it again.", target: null, tone: "problem" };
  }
  if (s.status === "imported" || !s.status) {
    return { label: "Find tissue", detail: "Detect the tissue, or draw it, so patches can be cut.", target: "processing", tone: "todo" };
  }
  if (s.status === "tissue_detected" || !HAS_PATCHES.includes(s.status)) {
    return { label: "Generate patches", detail: "Tissue found. Cut it into patches next.", target: "processing", tone: "todo" };
  }
  const total = s.patch_count ?? 0;
  const annotated = s.annotated_patch_count ?? 0;
  const reviewed = s.reviewed_patch_count ?? 0;
  if (total === 0) {
    return { label: "Generate patches", detail: "No patches in the current patch size yet.", target: "processing", tone: "todo" };
  }
  if (annotated === 0) {
    return { label: "Start annotating", detail: `${total.toLocaleString()} patches ready.`, target: "workspace", tone: "todo" };
  }
  if (reviewed < annotated) {
    return {
      label: "Continue",
      detail: `${annotated.toLocaleString()} of ${total.toLocaleString()} patches annotated, ${reviewed.toLocaleString()} reviewed.`,
      target: "workspace",
      tone: "todo",
    };
  }
  return {
    label: "Export",
    detail: `All ${annotated.toLocaleString()} annotated patches reviewed${annotated < total ? ` (${(total - annotated).toLocaleString()} left empty)` : ""}.`,
    target: "export",
    tone: "done",
  };
}

export interface ChecklistStep {
  id: "add" | "tissue" | "patches" | "annotate" | "review" | "export";
  title: string;
  /** Progress text, e.g. "2 of 3 slides". */
  progress: string;
  done: boolean;
  /** The step to work on now (the first one not done). */
  current: boolean;
  /** The slide the button should open for this step, if any. */
  slideId: number | null;
  target: StepTarget | "add" | null;
}

const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** The project's path from empty to exported, with how far along each step is. */
export function projectChecklist(project: ProjectDetail, slides: Slide[]): ChecklistStep[] {
  const isImage = project.project_type === "image";
  const st = project.stats;
  const usable = slides.filter((s) => s.status !== "error");
  const withTissue = usable.filter((s) => s.status !== "imported");
  const withPatches = usable.filter((s) => HAS_PATCHES.includes(s.status));
  const first = (list: Slide[], pred: (s: Slide) => boolean) => list.find(pred)?.id ?? null;
  const unit = isImage ? "image" : "patch";
  const units = isImage ? "images" : "patches";

  const steps: Omit<ChecklistStep, "current">[] = [
    {
      id: "add",
      title: isImage ? "Add images" : "Add slides",
      progress: slides.length ? count(slides.length, isImage ? "image" : "slide") : "none yet",
      done: slides.length > 0,
      slideId: null,
      target: "add",
    },
  ];
  if (!isImage) {
    steps.push(
      {
        id: "tissue",
        title: "Find tissue",
        progress: `${withTissue.length} of ${count(usable.length, "slide")}`,
        done: usable.length > 0 && withTissue.length === usable.length,
        slideId: first(usable, (s) => s.status === "imported"),
        target: "processing",
      },
      {
        id: "patches",
        title: "Generate patches",
        progress: `${withPatches.length} of ${count(usable.length, "slide")}`,
        done: usable.length > 0 && withPatches.length === usable.length,
        slideId: first(usable, (s) => !HAS_PATCHES.includes(s.status)),
        target: "processing",
      },
    );
  }
  steps.push(
    {
      id: "annotate",
      title: "Annotate",
      progress: st.total_patches ? `${st.annotated_patches.toLocaleString()} of ${count(st.total_patches, unit, units)}` : `no ${units} yet`,
      done: st.annotated_patches > 0 && withPatches.length === usable.length,
      slideId: first(withPatches, (s) => (s.annotated_patch_count ?? 0) === 0) ?? first(withPatches, () => true),
      target: "workspace",
    },
    {
      id: "review",
      title: "Review",
      progress: st.annotated_patches ? `${st.reviewed_patches.toLocaleString()} of ${st.annotated_patches.toLocaleString()} annotated` : "nothing to review yet",
      done: st.annotated_patches > 0 && st.reviewed_patches >= st.annotated_patches,
      slideId: first(withPatches, (s) => (s.reviewed_patch_count ?? 0) < (s.annotated_patch_count ?? 0)),
      target: "workspace",
    },
    {
      id: "export",
      title: "Export",
      progress: withPatches.length ? "ready whenever you are" : "after patches exist",
      done: false,
      slideId: withPatches[0]?.id ?? null,
      target: "export",
    },
  );
  const currentIndex = steps.findIndex((s) => !s.done);
  return steps.map((s, i) => ({ ...s, current: i === currentIndex }));
}
