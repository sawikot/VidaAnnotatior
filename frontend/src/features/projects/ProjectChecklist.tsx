import { useNavigate } from "react-router-dom";
import { MaterialIcon } from "../../components/MaterialIcon";
import { Button, Card } from "../../components/primitives";
import type { ProjectDetail, Slide } from "../../types/api";
import { projectChecklist, type ChecklistStep } from "../../utils/nextStep";

interface Props {
  project: ProjectDetail;
  slides: Slide[];
  onAdd: () => void;
}

const ACTION: Record<ChecklistStep["id"], string> = {
  add: "Add",
  tissue: "Find tissue",
  patches: "Generate patches",
  annotate: "Annotate",
  review: "Review",
  export: "Export",
};

/** The project's path from empty to exported: where it stands, and a button for what to do now. */
export function ProjectChecklist({ project, slides, onAdd }: Props) {
  const navigate = useNavigate();
  const pid = project.id;
  const isImage = project.project_type === "image";
  const steps = projectChecklist(project, slides);

  function go(step: ChecklistStep) {
    if (step.target === "add") return onAdd();
    if (isImage && step.target === "workspace") return navigate(`/projects/${pid}/annotate`);
    if (step.slideId === null || step.target === null) return;
    const scope = isImage && step.target === "export" ? "?scope=project" : "";
    navigate(`/projects/${pid}/slides/${step.slideId}/${step.target}${scope}`);
  }

  return (
    <Card className="p-space-md">
      <div className="text-label-md text-on-surface-variant mb-space-sm">Project progress</div>
      <ol className={`grid grid-cols-2 sm:grid-cols-3 gap-space-sm ${steps.length > 4 ? "lg:grid-cols-6" : "lg:grid-cols-4"}`}>
        {steps.map((step, i) => {
          const canGo = step.target === "add" || (isImage && step.target === "workspace") || step.slideId !== null;
          return (
            <li
              key={step.id}
              className={`rounded-lg p-space-sm flex flex-col gap-1 min-w-0 ${
                step.current ? "bg-primary-fixed/50 ring-1 ring-primary" : step.done ? "bg-surface-container-low" : "bg-surface-container-lowest"
              }`}
              aria-current={step.current ? "step" : undefined}
            >
              <div className="flex items-center gap-1.5">
                <span
                  className={`w-6 h-6 rounded-full flex items-center justify-center text-label-md shrink-0 ${
                    step.done ? "bg-emerald-600 text-white" : step.current ? "bg-primary text-on-primary" : "bg-surface-container-high text-on-surface-variant"
                  }`}
                >
                  {step.done ? <MaterialIcon name="check" className="!text-[16px]" /> : i + 1}
                </span>
                <span className="font-headline-sm text-label-lg truncate">{step.title}</span>
              </div>
              <span className="text-body-sm text-on-surface-variant truncate" title={step.progress}>
                {step.progress}
              </span>
              {step.current && canGo && (
                <Button variant="primary" className="self-start mt-1" onClick={() => go(step)}>
                  {ACTION[step.id]}
                  <MaterialIcon name="arrow_forward" className="!text-[16px]" />
                </Button>
              )}
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
