import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Download, ServerCrash } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Run, Task } from "../api/types";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingState } from "../components/ui/LoadingState";
import { MetricCard, MiniBars, PageHeader, Panel, PanelHeading } from "../components/ui/ResearchUI";
import { StatusBadge } from "../components/ui/StatusBadge";
import { formatCost, formatLatency } from "../lib/utils";

export function Runs() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  const [search, setSearch] = useState("");
  const [configuration, setConfiguration] = useState("all");
  const [outcome, setOutcome] = useState("all");
  const [source, setSource] = useState("all");

  useEffect(() => {
    Promise.all([api.getRuns(), api.getTasks()]).then(([runData, taskData]) => {
      setRuns(runData); setTasks(taskData);
    }).catch((err) => setError(err instanceof ApiError ? err : new ApiError(500, "Unknown error"))).finally(() => setLoading(false));
  }, []);

  const taskById = useMemo(() => new Map(tasks.map((task) => [task.task_id, task])), [tasks]);
  const filtered = useMemo(() => runs.filter((run) => {
    const task = taskById.get(run.task_id);
    const external = task?.task_source === "external" || Boolean(run.model_parameters?.external_repository);
    const query = search.trim().toLowerCase();
    if (query && !`${run.task_id} ${run.model} ${task?.title ?? ""}`.toLowerCase().includes(query)) return false;
    if (configuration !== "all" && run.configuration_id !== configuration) return false;
    if (outcome === "resolved" && run.final_resolution !== true) return false;
    if (outcome === "failed" && run.final_resolution !== false) return false;
    if (outcome === "active" && run.final_resolution != null) return false;
    if (source === "benchmark" && external) return false;
    if (source === "external" && !external) return false;
    return true;
  }), [runs, taskById, search, configuration, outcome, source]);

  if (loading) return <LoadingState />;
  if (error) return <EmptyState title="Evaluation history unavailable" description={error.message} isError icon={ServerCrash} actionText="New Run" actionHref="/new-run" />;

  const resolved = runs.filter((run) => run.final_resolution === true).length;
  const repaired = runs.filter((run) => run.repair_attempted).length;
  const latencies = runs.map((run) => run.latency_ms).filter((value): value is number => typeof value === "number").sort((a, b) => a - b);
  const medianLatency = latencies.length ? latencies[Math.floor(latencies.length / 2)] : 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Runs"
        subtitle="Search, compare and inspect every repair attempt."
        action={<button type="button" onClick={() => exportCsv(filtered)} disabled={!filtered.length} className="inline-flex items-center gap-2 rounded-lg border border-border bg-white px-3 py-2 text-xs font-semibold text-foreground shadow-sm disabled:opacity-40"><Download className="h-3.5 w-3.5" /> Export CSV</button>}
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Total runs" value={runs.length} note="all time" />
        <MetricCard label="Resolved" value={resolved} note={`${runs.length ? Math.round((resolved / runs.length) * 100) : 0}%`} />
        <MetricCard label="Needed repair" value={repaired} note={`${runs.length ? Math.round((repaired / runs.length) * 100) : 0}%`} />
        <MetricCard label="Median latency" value={formatLatency(medianLatency)} note="valid timing" />
      </div>

      <div className="grid gap-4 lg:grid-cols-[0.75fr_1.25fr]">
        <Panel className="p-5"><PanelHeading title="Run outcomes" subtitle={`Latest ${Math.min(12, runs.length)} runs`} /><MiniBars values={(runs.slice(0, 12).reverse().map((run) => Math.max(1, run.latency_ms ?? 1))).length ? runs.slice(0, 12).reverse().map((run) => Math.max(1, run.latency_ms ?? 1)) : [1, 1, 1, 1, 1, 1]} className="mt-6 h-16" barClassName="bg-[#20a673]" /><div className="mt-3 flex gap-4 text-[10px] text-muted-foreground"><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-[#20a673]" />Resolved</span><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-[#ce5d62]" />Failed states remain visible in history</span></div></Panel>
        <Panel className="p-5"><PanelHeading title="Filters" /><div className="mt-4 grid gap-3 sm:grid-cols-2"><label className="text-[10px] font-bold text-muted-foreground">Search runs<input className="research-input mt-1.5" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="task, title or model…" /></label><Filter label="Configuration" value={configuration} onChange={setConfiguration} options={["all", "A", "B", "C", "D"]} /><Filter label="Status" value={outcome} onChange={setOutcome} options={["all", "resolved", "failed", "active"]} /><Filter label="Source" value={source} onChange={setSource} options={["all", "benchmark", "external"]} /></div></Panel>
      </div>

      <Panel className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-border px-5 py-4"><PanelHeading title="Evaluation history" /><div className="flex items-center gap-3 text-[10px] text-muted-foreground"><span className="rounded-full bg-[#edf2e9] px-2 py-1 font-bold text-primary">{filtered.length} RUNS</span><span>Newest first</span></div></div>
        <div className="overflow-x-auto">
          <table className="research-table w-full min-w-[1020px]">
            <thead><tr><th>Task</th><th>Source</th><th>Model</th><th>Cfg</th><th>Status</th><th className="text-right">Tokens</th><th className="text-right">Cost</th><th className="text-right">Latency</th><th /></tr></thead>
            <tbody className="divide-y divide-border/70">
              {filtered.map((run) => {
                const task = taskById.get(run.task_id);
                const external = task?.task_source === "external" || Boolean(run.model_parameters?.external_repository);
                return <tr key={run.run_id} className="transition hover:bg-[#f7f9f5]"><td><Link to={`/runs/${run.run_id}`} className="block font-mono text-xs font-semibold text-[var(--sage-ink)] hover:text-primary">{run.task_id}</Link>{task?.title && <span className="mt-1 block max-w-56 truncate text-[10px] text-muted-foreground">{task.title}</span>}</td><td><span className={`rounded-full px-2 py-1 text-[9px] font-bold uppercase ${external ? "bg-[#e7f3f1] text-[#267b70]" : "bg-muted text-muted-foreground"}`}>{external ? "external" : "bench"}</span></td><td className="max-w-36 truncate text-xs" title={run.model}>{run.model}</td><td><span className="inline-grid min-w-6 place-items-center rounded-md bg-[#edf2e9] px-1.5 py-1 text-[9px] font-bold text-primary">{compactConfiguration(run.configuration_id)}</span></td><td><StatusBadge status={run.status} /></td><td className="text-right font-mono text-xs">{(run.input_tokens + run.output_tokens).toLocaleString()}</td><td className="text-right font-mono text-xs">{formatCost(run.estimated_cost)}</td><td className="text-right font-mono text-xs">{formatLatency(run.latency_ms)}</td><td><Link to={`/runs/${run.run_id}`} aria-label={`Open ${run.task_id}`}><ChevronRight className="h-4 w-4 text-muted-foreground" /></Link></td></tr>;
              })}
              {!filtered.length && <tr><td colSpan={9} className="py-12 text-center text-sm text-muted-foreground">No runs match the current evidence filters.</td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function Filter({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[] }) {
  return <label className="text-[10px] font-bold text-muted-foreground">{label}<select className="research-input mt-1.5 capitalize" value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option key={option} value={option}>{option === "all" ? `All ${label.toLowerCase()}` : option}</option>)}</select></label>;
}

function exportCsv(runs: Run[]) {
  const headers = ["run_id", "task_id", "configuration", "model", "status", "resolved", "repair_attempted", "tokens", "cost", "latency_ms"];
  const rows = runs.map((run) => [run.run_id, run.task_id, run.configuration_id, run.model, run.status, run.final_resolution ?? "", run.repair_attempted, run.input_tokens + run.output_tokens, run.estimated_cost ?? "", run.latency_ms ?? ""]);
  const csv = [headers, ...rows].map((row) => row.map((value) => `"${String(value).replace(/"/g, '""')}"`).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = "agentrace-runs.csv"; anchor.click(); URL.revokeObjectURL(url);
}

function compactConfiguration(value: string) {
  return ["A", "B", "C", "D"].includes(value) ? value : value.replace("-only", "").slice(0, 4).toUpperCase();
}
