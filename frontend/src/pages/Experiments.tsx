import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Check, Circle, ServerCrash } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Run, Task } from "../api/types";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingState } from "../components/ui/LoadingState";
import { PageHeader, Panel, PanelHeading } from "../components/ui/ResearchUI";
import { cn } from "../lib/utils";

type ExperimentSummary = { experiment_id: string; benchmark_version?: string; runs: number; resolved: number };

const configurationDefinitions = [
  { id: "A", name: "Direct Patch", tools: "No tools", repair: "No repair", evidence: "Context" },
  { id: "B", name: "Tool Agent", tools: "Repo tools", repair: "No repair", evidence: "Inspection" },
  { id: "C", name: "Verified CEGIS", tools: "Repo tools", repair: "1 repair", evidence: "Verification" },
  { id: "D", name: "Research Enhanced", tools: "SBFL + tools", repair: "1 repair", evidence: "SBFL + verify" },
];

export function Experiments() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [experiments, setExperiments] = useState<ExperimentSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    Promise.all([api.getRuns(), api.getTasks(), api.getExperiments()]).then(([runData, taskData, experimentData]) => {
      setRuns(runData); setTasks(taskData); setExperiments(experimentData as ExperimentSummary[]);
    }).catch((err) => setError(err instanceof ApiError ? err : new ApiError(500, "Unknown error"))).finally(() => setLoading(false));
  }, []);

  const primaryExperiment = experiments[0];
  const workspaceRuns = useMemo(() => {
    if (!primaryExperiment) return runs;
    const selected = runs.filter((run) => run.model_parameters?.experiment?.experiment_id === primaryExperiment.experiment_id);
    return selected.length ? selected : runs.filter((run) => run.model_parameters?.experiment_id === primaryExperiment.experiment_id);
  }, [primaryExperiment, runs]);
  const matrixTasks = useMemo(() => {
    const runTaskIds = new Set(workspaceRuns.map((run) => run.task_id));
    return tasks.filter((task) => runTaskIds.has(task.task_id)).slice(0, 8);
  }, [workspaceRuns, tasks]);
  if (loading) return <LoadingState />;
  if (error) return <EmptyState title="Research workspace unavailable" description={error.message} isError icon={ServerCrash} />;

  const planned = Math.max(workspaceRuns.length, matrixTasks.length * 4);
  const progress = planned ? Math.min(100, Math.round((workspaceRuns.length / planned) * 100)) : 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Experiments"
        subtitle="Design controlled evaluations and compare repair configurations."
        action={<Link to="/new-run" className="research-button">+ New run</Link>}
      />

      <Panel className="flex flex-col gap-5 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div><div className="flex items-center gap-2"><h2 className="text-lg font-bold text-[var(--sage-ink)]">{primaryExperiment?.experiment_id ?? "Research workspace"}</h2>{!primaryExperiment && <span className="rounded-full bg-[var(--amber-soft)] px-2 py-1 text-[9px] font-bold uppercase text-[#9a6a00]">Unassigned</span>}</div><p className="mt-1 text-xs text-muted-foreground">{matrixTasks.length} observed tasks · Gemini runs · Configurations A / B / C / D</p>{primaryExperiment?.benchmark_version && <p className="mt-1 font-mono text-[9px] text-muted-foreground">Benchmark {primaryExperiment.benchmark_version}</p>}</div>
        <div className="w-full max-w-sm rounded-xl bg-[#f3f6f1] p-4"><div className="flex items-end justify-between"><div><div className="research-kicker">Run matrix</div><div className="mt-1 text-2xl font-bold">{workspaceRuns.length} / {planned || 0}</div></div><span className="text-[10px] text-muted-foreground">{progress}% complete</span></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-white"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${progress}%` }} /></div></div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-[1.45fr_0.75fr]">
        <Panel className="p-5"><PanelHeading title="Configuration comparison" subtitle="Experimental factors remain visible at a glance" /><div className="mt-4 divide-y divide-border/70">{configurationDefinitions.map((config) => { const configRuns = workspaceRuns.filter((run) => run.configuration_id === config.id); const resolved = configRuns.filter((run) => run.final_resolution).length; const rate = configRuns.length ? Math.round((resolved / configRuns.length) * 100) : 0; return <div key={config.id} className="grid grid-cols-[auto_1fr] items-center gap-3 py-3 sm:grid-cols-[auto_1.2fr_0.7fr_0.7fr_0.8fr]"><span className={cn("grid h-7 w-7 place-items-center rounded-lg text-[10px] font-bold text-white", config.id === "A" ? "bg-[#749e79]" : config.id === "B" ? "bg-[#68886f]" : config.id === "C" ? "bg-[#98a96e]" : "bg-[#486c59]")}>{config.id}</span><div><div className="text-xs font-bold">{config.name}</div><div className="text-[9px] text-muted-foreground sm:hidden">{config.tools} · {config.repair}</div></div><span className="hidden text-[10px] text-muted-foreground sm:block">{config.tools}</span><span className="hidden text-[10px] text-muted-foreground sm:block">{config.repair}</span><div className="hidden items-center gap-2 sm:flex"><div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary" style={{ width: `${rate}%` }} /></div><span className="w-8 text-right text-[9px] font-bold">{configRuns.length ? `${rate}%` : "—"}</span></div></div>; })}</div></Panel>

        <Panel className="p-5"><PanelHeading title="Primary outcome" subtitle="Resolved task rate by configuration" /><div className="mt-6 flex h-44 items-end justify-around gap-3 border-b border-border px-3">{configurationDefinitions.map((config) => { const configRuns = workspaceRuns.filter((run) => run.configuration_id === config.id); const rate = configRuns.length ? Math.round((configRuns.filter((run) => run.final_resolution).length / configRuns.length) * 100) : 0; return <div key={config.id} className="flex h-full flex-1 flex-col items-center justify-end"><span className="mb-2 text-[10px] font-bold">{configRuns.length ? `${rate}%` : "—"}</span><div className={cn("w-full max-w-12 rounded-t-full", config.id === "D" ? "bg-[#668b70]" : config.id === "C" ? "bg-[#9aaa73]" : "bg-[#7d9d84]")} style={{ height: `${configRuns.length ? Math.max(12, rate) : 8}%`, opacity: configRuns.length ? 1 : 0.25 }} /><span className="mt-2 text-[10px] font-bold">{config.id}</span></div>; })}</div></Panel>
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.45fr_0.75fr]">
        <Panel className="overflow-hidden"><div className="border-b border-border px-5 py-4"><PanelHeading title="Run matrix" subtitle="Tasks × configurations" /></div><div className="overflow-x-auto"><table className="research-table w-full min-w-[560px]"><thead><tr><th>Task</th>{configurationDefinitions.map((config) => <th key={config.id} className="text-center">{config.id}</th>)}</tr></thead><tbody className="divide-y divide-border/70">{matrixTasks.map((task) => <tr key={task.task_id}><td className="font-mono text-xs">{task.task_id}</td>{configurationDefinitions.map((config) => { const run = workspaceRuns.find((item) => item.task_id === task.task_id && item.configuration_id === config.id); return <td key={config.id} className="text-center"><RunCell run={run} /></td>; })}</tr>)}{!matrixTasks.length && <tr><td colSpan={5} className="py-10 text-center text-sm text-muted-foreground">No experiment-linked run matrix is available yet.</td></tr>}</tbody></table></div></Panel>

        <Panel className="p-5"><PanelHeading title="Analysis plan" subtitle="What this study can compare" /><div className="mt-5 space-y-4">{[["Resolution rate", "PRIMARY", "Resolved tasks / valid runs"], ["Repair uplift", "CEGIS", "Configurations C/D"], ["Localization quality", "SBFL", "Rank / exploration"], ["Oracle strength", "MUTATION", "Qualification evidence"], ["Efficiency", "COST", "Tokens + latency"]].map(([label, badge, note], index) => <div key={label} className="flex items-start gap-3"><span className={cn("mt-1 h-2.5 w-2.5 rounded-full", index === 3 ? "bg-[#d59a18]" : "bg-[#74967c]")} /><div className="min-w-0 flex-1"><div className="flex items-center justify-between gap-2"><span className="text-xs font-bold">{label}</span><span className="rounded-full bg-muted px-2 py-1 text-[8px] font-bold text-muted-foreground">{badge}</span></div><p className="mt-1 text-[9px] text-muted-foreground">{note}</p></div></div>)}</div><div className="mt-5 rounded-xl bg-[#edf3e9] p-3 text-[10px] text-primary">Frozen settings remain traceable through run metadata.</div></Panel>
      </div>
    </div>
  );
}

function RunCell({ run }: { run?: Run }) {
  if (!run) return <span className="inline-grid h-7 w-7 place-items-center rounded-md border border-border bg-[#f7f9f5]"><Circle className="h-2.5 w-2.5 text-border" /></span>;
  if (run.final_resolution === true) return <Link to={`/runs/${run.run_id}`} title="Resolved" className="inline-grid h-7 w-7 place-items-center rounded-md bg-[#e6f4ec] text-[#168258]"><Check className="h-3.5 w-3.5" /></Link>;
  if (run.final_resolution === false) return <Link to={`/runs/${run.run_id}`} title="Unresolved" className="inline-grid h-7 w-7 place-items-center rounded-md bg-[var(--red-soft)] text-[#b14248]">×</Link>;
  return <Link to={`/runs/${run.run_id}`} title="Active" className="inline-grid h-7 w-7 place-items-center rounded-md bg-[var(--amber-soft)] text-[#9a6a00]">•</Link>;
}
