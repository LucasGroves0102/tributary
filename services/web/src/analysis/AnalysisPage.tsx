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
  saved: Saved;
  readable: Readable;
  label: (companyId: string) => string;
  onOpenChart: (s: ChartState) => void;
  onChartMany: (ids: string[], metric: string, transform: "ttm" | "q" | "yoy", title: string) => void;
}) {
  if (!props.projectId) return <AnalysisHome token={props.token} saved={props.saved} readable={props.readable} label={props.label} />;
  return <ProjectPage key={props.projectId} {...props} projectId={props.projectId} />;
}
