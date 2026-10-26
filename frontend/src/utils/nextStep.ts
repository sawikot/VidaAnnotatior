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

const count = (n: number, one: string, many = `${one}s`) => `${n.toLocaleString()} ${n === 1 ? one : many}`;

/** What a WSI slide needs next, from its status and its progress in the active patch grid. */
export function slideNextStep(s: Slide): SlideStep {
  if (s.status === "error") {
    return { label: "Fix import", detail: s.error_message || "The slide could not be read; import it again.", target: null, tone: "problem" };
  }
  const total = HAS_PATCHES.includes(s.status) ? (s.patch_count ?? 0) : 0;
  const annotated = s.annotated_patch_count ?? 0;
  const reviewed = s.reviewed_patch_count ?? 0;
  const drawn = s.annotation_count ?? 0;
  // No patches: the slide is annotated as a whole (patches are optional, made from the workspace).
  if (total === 0) {
    return drawn === 0
      ? { label: "Annotate", detail: "Not annotated yet · patches optional.", target: "workspace", tone: "todo" }
      : { label: "Continue", detail: `${count(drawn, "annotation")} on the whole slide · patches optional.`, target: "workspace", tone: "todo" };
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
  /** The step to work on now (the first one not done and not optional). */
  current: boolean;
  /** Can be skipped: it does not hold up the steps after it (e.g. review when nothing has patches). */
  optional?: boolean;
  /** The slide the button should open for this step, if any. */
  slideId: number | null;
  target: StepTarget | "add" | null;
}


/** The project's path from empty to exported, with how far along each step is. */
export function projectChecklist(project: ProjectDetail, slides: Slide[]): ChecklistStep[] {
  const isImage = project.project_type === "image";
  const st = project.stats;
  const usable = slides.filter((s) => s.status !== "error");
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
  const drawnOn = (s: Slide) => (s.annotation_count ?? 0) > 0 || (s.annotated_patch_count ?? 0) > 0;
  const annotatedSlides = usable.filter(drawnOn);
  steps.push(
    isImage
      ? {
          id: "annotate",
          title: "Annotate",
          progress: st.total_patches ? `${st.annotated_patches.toLocaleString()} of ${count(st.total_patches, unit, units)}` : `no ${units} yet`,
          done: st.annotated_patches > 0 && withPatches.length === usable.length,
          slideId: first(withPatches, (s) => (s.annotated_patch_count ?? 0) === 0) ?? first(withPatches, () => true),
          target: "workspace",
        }
      : {
          // A WSI slide is annotated straight away, on the whole slide; tissue and patches are optional.
          id: "annotate",
          title: "Annotate",
          progress: usable.length ? `${annotatedSlides.length} of ${count(usable.length, "slide")} started` : "no slides yet",
          done: usable.length > 0 && annotatedSlides.length === usable.length,
          slideId: first(usable, (s) => !drawnOn(s)) ?? first(usable, () => true),
          target: "workspace",
        },
    {
      id: "review",
      title: "Review",
      progress: st.annotated_patches
        ? `${st.reviewed_patches.toLocaleString()} of ${st.annotated_patches.toLocaleString()} annotated`
        : isImage
          ? "nothing to review yet"
          : "patch by patch · optional",
      done: st.annotated_patches > 0 && st.reviewed_patches >= st.annotated_patches,
      optional: !isImage && st.annotated_patches === 0, // reviewing is per patch: nothing to do without patches
      slideId: first(withPatches, (s) => (s.reviewed_patch_count ?? 0) < (s.annotated_patch_count ?? 0)),
      target: "workspace",
    },
    {
      id: "export",
      title: "Export",
      progress: isImage ? (withPatches.length ? "ready whenever you are" : "after images exist") : annotatedSlides.length ? "ready whenever you are" : "after annotating",
      done: false,
      slideId: isImage ? (withPatches[0]?.id ?? null) : (annotatedSlides[0]?.id ?? null),
      target: "export",
    },
  );
  const currentIndex = steps.findIndex((s) => !s.done && !s.optional);
  return steps.map((s, i) => ({ ...s, current: i === currentIndex }));
}
