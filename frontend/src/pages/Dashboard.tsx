import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronRight, Plus, ServerCrash } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Run } from "../api/types";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingState } from "../components/ui/LoadingState";
import { StatusBadge } from "../components/ui/StatusBadge";
import { InlineLink, MetricCard, OutcomeDonut, PageHeader, Panel, PanelHeading } from "../components/ui/ResearchUI";
import { formatCost, formatLatency } from "../lib/utils";

const defaultBars = [1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1];

export function Dashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    api.getRuns().then(setRuns).catch((err) => {
      setError(err instanceof ApiError ? err : new ApiError(500, "Unknown error"));
    }).finally(() => setLoading(false));
  }, []);

  const metrics = useMemo(() => summarizeRuns(runs), [runs]);
  if (loading) return <LoadingState />;
  if (error) return <EmptyState title="Research console unavailable" description={error.message} isError icon={ServerCrash} actionText="New Run" actionHref="/new-run" />;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Dashboard"
        subtitle="Observe repair quality, verification evidence and agent efficiency."
        action={
          <div className="flex items-center gap-3">
            <span className="hidden items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-[#168258] sm:flex">
              <span className="h-2 w-2 rounded-full bg-[#20a673]" /> System ready
            </span>
            <Link to="/new-run" className="research-button"><Plus className="h-4 w-4" /> New Run</Link>
          </div>
        }
      />

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <MetricCard label="Resolution rate" value={`${metrics.resolutionRate}%`} note={`${metrics.resolved}/${metrics.valid} valid`} bars={metrics.resolutionTrend} accent="lavender" />
        <MetricCard label="Median latency" value={formatLatency(metrics.medianLatency)} note={metrics.medianLatency ? "all runs" : "no data"} bars={metrics.latencyTrend} accent="green" />
        <MetricCard label="Repair uplift" value={`${metrics.repairUplift >= 0 ? "+" : ""}${metrics.repairUplift}%`} note="CEGIS vs A/B" bars={metrics.repairTrend} />
        <MetricCard label="Total cost" value={formatCost(metrics.totalCost)} note={`${runs.length} runs`} bars={metrics.costTrend} />
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.75fr_0.85fr]">
        <Panel className="p-5">
          <PanelHeading title="Repair pipeline" subtitle="Where runs succeed, fail or trigger bounded repair" />
          <div className="mt-5 grid gap-2 sm:grid-cols-5">
            {[
              [metrics.localized, "Localize", "SBFL / task"],
              [runs.length, "Patch P0", "Initial candidate"],
              [runs.length, "Verify", "Tests + gates"],
              [metrics.repairs, "Counterexample", "Failure evidence"],
              [metrics.repairs, "Patch P1", "1 repair max"],
            ].map(([value, label, note], index) => (
              <div key={String(label)} className="relative rounded-xl border border-border bg-[#f7f9f5] px-4 py-4">
                <span className={`absolute inset-x-0 top-0 h-[3px] rounded-t-xl ${index === 3 ? "bg-[#d59a18]" : "bg-[#78977f]"}`} />
                <div className="text-2xl font-bold text-[var(--sage-ink)]">{value}</div>
                <div className="mt-2 text-xs font-bold">{label}</div>
                <div className="mt-1 text-[10px] text-muted-foreground">{note}</div>
                {index < 4 && <ChevronRight className="absolute -right-3 top-1/2 z-10 h-4 w-4 -translate-y-1/2 text-muted-foreground/50" />}
              </div>
            ))}
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <div className="rounded-xl bg-[var(--red-soft)] px-4 py-3 text-xs text-[#a84046]">
              <div className="font-bold">Failure evidence</div>
              <div className="mt-1">{metrics.failed} verifier failures · {metrics.infrastructure} provider / infra</div>
            </div>
            <div className="rounded-xl bg-[#e7f5ee] px-4 py-3 text-xs text-[#177650]">
              <div className="flex items-center gap-2 font-bold"><span className="h-3 w-3 rounded-full bg-[#20a673]" /> Resolved</div>
              <div className="mt-1">{metrics.resolved} / {metrics.valid} valid runs</div>
            </div>
          </div>
        </Panel>

        <Panel className="p-5">
          <PanelHeading title="Outcome mix" subtitle="Latest evaluation runs" />
          <div className="mt-6"><OutcomeDonut resolved={metrics.resolved} failed={metrics.failed} infrastructure={metrics.infrastructure} /></div>
        </Panel>
      </div>

      <Panel className="overflow-hidden">
        <div className="border-b border-border px-5 py-4"><PanelHeading title="Recent runs" action={<InlineLink to="/runs">View all</InlineLink>} /></div>
        <div className="overflow-x-auto">
          <table className="research-table w-full min-w-[760px]">
            <thead><tr><th>Task</th><th>Config</th><th>Status</th><th>Evidence</th><th className="text-right">Latency</th><th className="text-right">Cost</th></tr></thead>
            <tbody className="divide-y divide-border/70">
              {runs.slice(0, 6).map((run) => (
                <tr key={run.run_id} className="transition hover:bg-[#f7f9f5]">
                  <td className="font-mono text-xs"><Link to={`/runs/${run.run_id}`} className="font-semibold text-[var(--sage-ink)] hover:text-primary">{run.task_id}</Link></td>
                  <td><span className="inline-grid min-w-6 place-items-center rounded-md bg-[#edf2e9] px-1.5 py-1 text-[9px] font-bold text-primary">{compactConfiguration(run.configuration_id)}</span></td>
                  <td><StatusBadge status={run.status} /></td>
                  <td className="text-xs text-muted-foreground">{evidenceLabel(run)}</td>
                  <td className="text-right font-mono text-xs">{formatLatency(run.latency_ms)}</td>
                  <td className="text-right font-mono text-xs">{formatCost(run.estimated_cost)}</td>
                </tr>
              ))}
              {runs.length === 0 && <tr><td colSpan={6} className="py-12 text-center text-sm text-muted-foreground">No run evidence yet. Start the first evaluation from New Run.</td></tr>}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}

function summarizeRuns(runs: Run[]) {
  const valid = runs.filter((run) => run.final_resolution != null && !isInfrastructure(run));
  const resolved = valid.filter((run) => run.final_resolution === true).length;
  const failed = valid.filter((run) => run.final_resolution === false).length;
  const infrastructure = runs.filter(isInfrastructure).length;
  const repairs = runs.filter((run) => run.repair_attempted).length;
  const localized = runs.filter((run) => run.configuration_id === "D").length;
  const latencies = runs.map((run) => run.latency_ms).filter((value): value is number => typeof value === "number").sort((a, b) => a - b);
  const medianLatency = latencies.length ? latencies[Math.floor(latencies.length / 2)] : 0;
  const direct = valid.filter((run) => ["A", "B"].includes(run.configuration_id));
  const cegis = valid.filter((run) => ["C", "D"].includes(run.configuration_id));
  const directRate = direct.length ? direct.filter((run) => run.final_resolution).length / direct.length : 0;
  const cegisRate = cegis.length ? cegis.filter((run) => run.final_resolution).length / cegis.length : 0;
  const chronological = [...runs].reverse().slice(-12);
  return {
    valid: valid.length,
    resolved,
    failed,
    infrastructure,
    repairs,
    localized,
    medianLatency,
    resolutionRate: valid.length ? Math.round((resolved / valid.length) * 100) : 0,
    repairUplift: Math.round((cegisRate - directRate) * 100),
    totalCost: runs.reduce((sum, run) => sum + (run.estimated_cost ?? 0), 0),
    resolutionTrend: chronological.length ? chronological.map((run) => run.final_resolution ? 2 : 1) : defaultBars,
    latencyTrend: chronological.length ? chronological.map((run) => Math.max(1, run.latency_ms ?? 1)) : defaultBars,
    repairTrend: chronological.length ? chronological.map((run) => run.repair_attempted ? 2 : 1) : defaultBars,
    costTrend: chronological.length ? chronological.map((run) => Math.max(0.0001, run.estimated_cost ?? 0.0001)) : defaultBars,
  };
}

function isInfrastructure(run: Run) {
  const category = (run.failure_category ?? "").toLowerCase();
  return category.includes("infrastructure") || category.includes("provider");
}

function evidenceLabel(run: Run) {
  if (run.repair_attempted) return "Counterexample · repair";
  if (run.configuration_id === "D") return "CEGIS · SBFL";
  if (run.final_resolution === false) return "Verification failure";
  return run.tool_calls ? `${run.tool_calls} tool calls` : "Patch evidence";
}

function compactConfiguration(value: string) {
  return ["A", "B", "C", "D"].includes(value) ? value : value.replace("-only", "").slice(0, 4).toUpperCase();
}
