import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingState } from "../components/ui/LoadingState";
import type { Run } from "../api/types";
import { Link } from "react-router-dom";
import { ServerCrash, CheckCircle2, PlayCircle, PlusCircle, AlertCircle } from "lucide-react";
import { formatLatency, formatCost } from "../lib/utils";
import { StatusBadge } from "../components/ui/StatusBadge";

export function Dashboard() {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    async function loadData() {
      try {
        const data = await api.getRuns();
        setRuns(data);
      } catch (err) {
        if (err instanceof ApiError) setError(err);
        else setError(new ApiError(500, "Unknown error"));
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, []);

  if (loading) return <LoadingState />;

  if (error) {
    if (error.status === 404) {
      return (
        <div className="space-y-6">
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
              <p className="text-muted-foreground mt-1">LLM Program Repair Research Platform</p>
            </div>
          </div>
          <EmptyState
            title="Backend Endpoint Missing"
            description="The GET /runs endpoint is not implemented on the backend yet. We cannot fetch recent runs."
            isError
            actionText="New Run"
            actionHref="/new-run"
          />
        </div>
      );
    }
    return <EmptyState title="System Error" description={error.message} isError icon={ServerCrash} />;
  }

  if (runs.length === 0) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground mt-1">LLM Program Repair Research Platform</p>
        </div>
        <EmptyState
          title="No runs yet"
          description="AgentTrace evaluates LLM-based automated program repair. Create your first run to get started."
          actionText="Create First Run"
          actionHref="/new-run"
          icon={PlayCircle}
        />
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
          <p className="text-muted-foreground mt-1">LLM Program Repair Research Platform</p>
        </div>
        <Link
          to="/new-run"
          className="inline-flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90"
        >
          <PlusCircle className="w-4 h-4" />
          New Run
        </Link>
      </div>

      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        {/* Simple Summary Metrics */}
        <MetricCard title="Total Runs" value={runs.length} />
        <MetricCard title="Resolved" value={runs.filter(r => r.final_resolution).length} />
        <MetricCard title="Repaired" value={runs.filter(r => r.repair_attempted).length} />
        <MetricCard title="Total Cost" value={formatCost(runs.reduce((acc, r) => acc + (r.estimated_cost || 0), 0))} />
      </div>

      <div>
        <h2 className="text-xl font-semibold tracking-tight mb-4">Recent Runs</h2>
        <div className="rounded-md border bg-card">
          <table className="w-full text-sm text-left">
            <thead className="border-b bg-muted/50 text-muted-foreground">
              <tr>
                <th className="font-medium p-4">Task</th>
                <th className="font-medium p-4">Configuration</th>
                <th className="font-medium p-4">Status</th>
                <th className="font-medium p-4">Resolution</th>
                <th className="font-medium p-4 text-right">Latency</th>
              </tr>
            </thead>
            <tbody>
              {runs.slice(0, 10).map((run) => (
                <tr key={run.run_id} className="border-b last:border-0 hover:bg-muted/30 transition-colors">
                  <td className="p-4 font-mono">
                    <Link to={`/runs/${run.run_id}`} className="hover:underline text-primary">
                      {run.task_id}
                    </Link>
                  </td>
                  <td className="p-4">{run.configuration_id}</td>
                  <td className="p-4"><StatusBadge status={run.status} /></td>
                  <td className="p-4">
                    {run.final_resolution === true && <span className="text-green-600 flex items-center gap-1"><CheckCircle2 className="w-4 h-4"/> Passed</span>}
                    {run.final_resolution === false && <span className="text-red-600 flex items-center gap-1"><AlertCircle className="w-4 h-4"/> Failed</span>}
                    {run.final_resolution === null && <span className="text-muted-foreground">-</span>}
                  </td>
                  <td className="p-4 text-right">{formatLatency(run.latency_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function MetricCard({ title, value }: { title: string, value: string | number }) {
  return (
    <div className="rounded-xl border bg-card text-card-foreground shadow-sm p-6">
      <h3 className="tracking-tight text-sm font-medium text-muted-foreground">{title}</h3>
      <div className="mt-2 text-3xl font-bold">{value}</div>
    </div>
  );
}
