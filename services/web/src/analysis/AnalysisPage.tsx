import type { Readable } from "../access";
import type { ChartState } from "../chart/model";
import type { ProjectStep } from "../types";
import type { Saved } from "../workspaces/useSaved";
import { AnalysisHome } from "./AnalysisHome";
import { ProjectPage } from "./ProjectPage";

/** The Analysis tab (`#/analysis`, `#/analysis/{project}[/{step}[/{item}]]`): the user's
 * projects, or one project. One lazily loaded chunk (pages/lazy.ts). */
export function AnalysisPage(props: {
  token: string;
  user: string | null;
  projectId?: string;
  step?: ProjectStep;
  itemId?: string;
  /** a company to start a new analysis of (from its company page) */
  start?: string;
  saved: Saved;
  readable: Readable;
  /** company id → ticker or name (companies/directory.ts `short`) */
  label: (companyId: string) => string;
  onOpenChart: (s: ChartState) => void;
  onChartMany: (ids: string[], metric: string, transform: "ttm" | "q" | "yoy") => void;
}) {
  if (!props.projectId) {
    return <AnalysisHome key={props.start ?? ""} token={props.token} saved={props.saved} readable={props.readable} label={props.label} start={props.start} />;
  }
  return <ProjectPage key={props.projectId} {...props} projectId={props.projectId} />;
}
