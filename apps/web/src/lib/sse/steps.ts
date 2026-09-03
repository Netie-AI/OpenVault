/**
 * Single source of truth for build-step ordering and progress.
 *
 * The backend emits `{type:"progress", currentStep, progress}` derived from
 * these exact numbers; the UI renders the rail from the same table. Keep the
 * two ends identical — a mismatch shows up as a progress bar that jumps
 * backwards mid-deploy, which is worse than no bar at all.
 */

export const STEP_INDEX: Record<string, number> = {
  prepare: 0,
  clone: 1,
  install: 2,
  build: 3,
  deploy: 4,
};

export const STEP_PROGRESS: Record<string, number> = {
  prepare: 3,
  clone: 10,
  install: 30,
  build: 55,
  deploy: 80,
};

export type StepStatus = "running" | "completed" | "failed" | "skipped";

/** A completed step claims the next 10 points of the bar. */
export function progressForStep(step: string, stepStatus?: StepStatus): number {
  const base = STEP_PROGRESS[step] ?? 0;
  return stepStatus === "completed" ? base + 10 : base;
}

/** Ordered phases for the build-phases panel. `prepare` is one-time setup. */
export const BUILD_PHASES: ReadonlyArray<{ id: string; label: string; oneTime?: boolean }> = [
  { id: "prepare", label: "Prepare", oneTime: true },
  { id: "clone", label: "Clone" },
  { id: "install", label: "Install" },
  { id: "build", label: "Build" },
  { id: "deploy", label: "Deploy" },
];
