import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingState } from "../components/ui/LoadingState";
import type { Run } from "../api/types";
import { Link } from "react-router-dom";
import { ServerCrash, AlertCircle, CheckCircle2 } from "lucide-react";
import { formatLatency, formatCost } from "../lib/utils";
import { StatusBadge } from "../components/ui/StatusBadge";

export function Runs() {
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
              <h1 className="text-3xl font-bold tracking-tight">Runs History</h1>
              <p className="text-muted-foreground mt-1">Review all AgentTrace execution runs.</p>
            </div>
          </div>
          <EmptyState
            title="Backend Endpoint Missing"
            description="The GET /runs endpoint is not implemented on the backend. Cannot list run history."
            isError
            actionText="New Run"
            actionHref="/new-run"
          />
        </div>
      );
    }
    return <EmptyState title="System Error" description={error.message} isError icon={ServerCrash} />;
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Runs History</h1>
        <p className="text-muted-foreground mt-1">Review all AgentTrace execution runs.</p>
      </div>

      <div className="rounded-md border bg-card overflow-hidden">
        <table className="w-full text-sm text-left">
          <thead className="border-b bg-muted/50 text-muted-foreground">
            <tr>
              <th className="font-medium p-4">Task</th>
              <th className="font-medium p-4">Model</th>
              <th className="font-medium p-4">Config</th>
              <th className="font-medium p-4">Status</th>
              <th className="font-medium p-4">Resolution</th>
              <th className="font-medium p-4 text-right">Tokens</th>
              <th className="font-medium p-4 text-right">Cost</th>
              <th className="font-medium p-4 text-right">Latency</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {runs.map((run) => (
              <tr key={run.run_id} className="hover:bg-muted/30 transition-colors">
                <td className="p-4 font-mono">
                  <Link to={`/runs/${run.run_id}`} className="hover:underline text-primary">
                    {run.task_id}
                  </Link>
                </td>
                <td className="p-4 truncate max-w-[120px]" title={run.model}>{run.model}</td>
                <td className="p-4">{run.configuration_id}</td>
                <td className="p-4"><StatusBadge status={run.status} /></td>
                <td className="p-4">
                  {run.final_resolution === true && <span className="text-green-600 flex items-center gap-1"><CheckCircle2 className="w-4 h-4"/> Passed</span>}
                  {run.final_resolution === false && <span className="text-red-600 flex items-center gap-1"><AlertCircle className="w-4 h-4"/> Failed</span>}
                  {run.final_resolution === null && <span className="text-muted-foreground">-</span>}
                </td>
                <td className="p-4 text-right">{(run.input_tokens + run.output_tokens).toLocaleString()}</td>
                <td className="p-4 text-right">{formatCost(run.estimated_cost)}</td>
                <td className="p-4 text-right">{formatLatency(run.latency_ms)}</td>
              </tr>
            ))}
            {runs.length === 0 && (
              <tr>
                <td colSpan={8} className="p-8 text-center text-muted-foreground">No runs found.</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
