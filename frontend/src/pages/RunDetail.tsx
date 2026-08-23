import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { EmptyState } from "../components/ui/EmptyState";
import { LoadingState } from "../components/ui/LoadingState";
import type { RunDetail as RunDetailType } from "../api/types";
import { useParams, Link } from "react-router-dom";
import { ServerCrash, CheckCircle2, AlertCircle, ChevronRight, FileText, Loader2 } from "lucide-react";
import { formatLatency, formatCost, formatTokens, cn } from "../lib/utils";
import { StatusBadge } from "../components/ui/StatusBadge";
import { ReportViewer } from "./ReportViewer";

export function RunDetail() {
  const { id } = useParams<{ id: string }>();
  const [data, setData] = useState<RunDetailType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<ApiError | null>(null);
  
  const [activeTab, setActiveTab] = useState<"overview" | "trace" | "verification" | "patch" | "report">("overview");
  const [generatingReport, setGeneratingReport] = useState(false);
  const [reportExists, setReportExists] = useState(false);

  useEffect(() => {
    async function loadData() {
      if (!id) return;
      try {
        const result = await api.getRun(id);
        setData(result);
        // check if report exists
        try {
          await api.getRunReport(id);
          setReportExists(true);
        } catch {
          setReportExists(false);
        }
      } catch (err) {
        if (err instanceof ApiError) setError(err);
        else setError(new ApiError(500, "Unknown error"));
      } finally {
        setLoading(false);
      }
    }
    loadData();
  }, [id]);

  const handleGenerateReport = async () => {
    if (!id) return;
    setGeneratingReport(true);
    try {
      await api.generateRunReport(id);
      setReportExists(true);
      setActiveTab("report");
    } catch (err) {
      alert(err instanceof ApiError ? err.message : "Failed to generate report.");
    } finally {
      setGeneratingReport(false);
    }
  };

  if (loading) return <LoadingState />;

  if (error) {
    if (error.status === 404) {
      return (
        <div className="space-y-6">
          <EmptyState
            title="Backend Endpoint Missing"
            description={`The GET /runs/${id} endpoint is not implemented on the backend.`}
            isError
            actionText="Go to Dashboard"
            actionHref="/"
          />
        </div>
      );
    }
    return <EmptyState title="System Error" description={error.message} isError icon={ServerCrash} />;
  }

  if (!data) return <EmptyState title="Not found" description="Run not found" />;

  const { run, task, trace, verification, patches, counterexamples, sbfl } = data;
  const isComplete = Boolean(run.finished_at) || run.status.includes("resolved") || run.status.includes("fail") || run.status.includes("completed") || run.status.includes("error");

  return (
    <div className="space-y-6">
      {/* HEADER */}
      <div className="bg-card p-6 rounded-xl border shadow-sm">
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3 text-sm text-muted-foreground mb-2">
              <Link to="/runs" className="hover:text-foreground hover:underline">Runs</Link>
              <ChevronRight className="w-4 h-4" />
              <span className="font-mono">{run.run_id}</span>
            </div>
            <div className="flex items-center gap-3">
              <h1 className="text-3xl font-bold tracking-tight font-mono">{run.task_id}</h1>
              {task?.task_source === "external" && <span className="bg-primary/10 text-primary text-xs px-2 py-0.5 rounded uppercase font-bold tracking-wider">External Repository</span>}
            </div>
            <div className="mt-4 flex flex-wrap gap-4 text-sm">
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground font-medium">Config:</span> {run.configuration_id}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground font-medium">Model:</span> {run.model}
              </div>
              <div className="flex items-center gap-2">
                <span className="text-muted-foreground font-medium">Status:</span>
                <StatusBadge status={run.status} />
              </div>
              {run.final_resolution !== null && (
                <div className="flex items-center gap-2">
                  <span className="text-muted-foreground font-medium">Resolution:</span>
                  {run.final_resolution ? (
                    <span className="text-green-600 font-semibold flex items-center gap-1"><CheckCircle2 className="w-4 h-4"/> RESOLVED</span>
                  ) : (
                    <span className="text-red-600 font-semibold flex items-center gap-1"><AlertCircle className="w-4 h-4"/> FAILED</span>
                  )}
                </div>
              )}
            </div>
          </div>
          <div>
            {isComplete && (
              <button 
                onClick={reportExists ? () => setActiveTab("report") : handleGenerateReport}
                disabled={generatingReport}
                className="inline-flex items-center gap-2 rounded-md bg-secondary px-4 py-2 text-sm font-medium hover:bg-secondary/80 transition-colors border"
              >
                {generatingReport ? <Loader2 className="w-4 h-4 animate-spin"/> : <FileText className="w-4 h-4" />}
                {generatingReport ? "Generating evidence-based report..." : reportExists ? "View Report" : "Generate Report"}
              </button>
            )}
          </div>
        </div>
      </div>

      {/* TABS */}
      <div className="flex space-x-2 border-b">
        {["overview", "trace", "verification", "patch", "report"].map(tab => {
          if (tab === "report" && !reportExists && !generatingReport) return null;
          return (
            <button
              key={tab}
              onClick={() => setActiveTab(tab as any)}
              className={cn("px-4 py-2 text-sm font-medium capitalize border-b-2 transition-colors", activeTab === tab ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground")}
            >
              {tab}
            </button>
          )
        })}
      </div>

      {/* TAB CONTENT */}
      {activeTab === "overview" && (
        <div className="space-y-8 animate-in fade-in duration-300">
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-4">
            <Metric title="Total Tokens" value={formatTokens(run.input_tokens + run.output_tokens)} />
            <Metric title="Input Tokens" value={formatTokens(run.input_tokens)} />
            <Metric title="Output Tokens" value={formatTokens(run.output_tokens)} />
            <Metric title="Latency" value={formatLatency(run.latency_ms)} />
            <Metric title="Est. Cost" value={formatCost(run.estimated_cost)} />
            <Metric title="Repair Attempts" value={run.repair_attempted ? "1" : "0"} />
          </div>
          {sbfl && (
            <div className="space-y-4">
              <h2 className="text-xl font-semibold tracking-tight">Fault Localization Evidence</h2>
              <div className="rounded-md border bg-card">
                <table className="w-full text-sm text-left">
                  <thead className="border-b bg-muted/50 text-muted-foreground">
                    <tr>
                      <th className="font-medium p-3">Rank</th>
                      <th className="font-medium p-3">Location</th>
                      <th className="font-medium p-3 text-right">Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sbfl.ranked_locations.slice(0, sbfl.top_k).map((loc, idx) => (
                      <tr key={idx} className="border-b last:border-0">
                        <td className="p-3 text-muted-foreground">#{idx + 1}</td>
                        <td className="p-3 font-mono text-xs">{loc.file as string}:{loc.line as string}</td>
                        <td className="p-3 text-right font-mono">{loc.score as string}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}

      {activeTab === "trace" && (
        <div className="space-y-6 animate-in fade-in duration-300">
          <div className="relative border-l border-muted ml-3 space-y-6 pb-4">
            {trace.length === 0 && (
              <div className="pl-6 text-sm text-muted-foreground italic">No trace events recorded.</div>
            )}
            {trace.map((event) => (
              <div key={event.event_id} className="relative pl-6">
                <div className="absolute w-3 h-3 bg-primary rounded-full -left-[6.5px] top-1 ring-4 ring-background" />
                <div className="font-semibold text-sm">{event.operation}</div>
                <div className="text-xs text-muted-foreground mt-1 flex gap-2">
                  <StatusBadge status={event.status} />
                </div>
                {event.error_type && (
                  <div className="mt-2 text-xs text-destructive bg-destructive/10 p-2 rounded-md border border-destructive/20 font-mono">
                    {event.error_type}
                  </div>
                )}
                {event.output_summary && (
                  <div className="mt-2 text-xs text-muted-foreground bg-muted/50 p-2 rounded-md font-mono whitespace-pre-wrap">
                    {event.output_summary}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {activeTab === "verification" && (
        <div className="space-y-8 animate-in fade-in duration-300">
          {verification.length > 0 ? (
            <div className="grid gap-3">
              {verification.map((v, i) => (
                <div key={i} className="flex items-center justify-between p-4 bg-card border rounded-lg shadow-sm">
                  <div className="flex items-center gap-3">
                    {v.status === "passed" ? <CheckCircle2 className="w-5 h-5 text-green-500" /> : <AlertCircle className="w-5 h-5 text-red-500" />}
                    <div>
                      <div className="font-semibold text-sm flex items-center gap-2">
                        {v.gate}
                        {v.required ? <span className="text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded uppercase font-bold tracking-wider">Required</span> : <span className="text-[10px] bg-muted text-muted-foreground px-1.5 py-0.5 rounded uppercase font-bold tracking-wider">Advisory</span>}
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5">{v.summary}</div>
                    </div>
                  </div>
                  <div className="text-xs font-mono text-muted-foreground bg-muted px-2 py-1 rounded">
                    {v.duration_ms}ms
                  </div>
                </div>
              ))}
            </div>
          ) : <EmptyState title="No Verification" description="No verification gates were run." />}

          {counterexamples.length > 0 && (
            <div className="space-y-4">
              <h2 className="text-xl font-semibold tracking-tight">Counterexample Evidence</h2>
              {counterexamples.map((ce) => (
                <div key={ce.counterexample_id} className="p-4 bg-red-50/50 border border-red-100 rounded-lg shadow-sm dark:bg-red-950/20 dark:border-red-900/30">
                  <div className="font-semibold text-red-900 dark:text-red-400 mb-2">{ce.source} Failure</div>
                  <div className="text-sm space-y-2 font-mono">
                    {ce.input_summary && <div><span className="text-muted-foreground">Input:</span> {ce.input_summary}</div>}
                    {ce.expected_summary && <div><span className="text-muted-foreground">Expected:</span> {ce.expected_summary}</div>}
                    <div><span className="text-muted-foreground">Observed:</span> <span className="text-red-700 dark:text-red-300">{ce.observed_summary}</span></div>
                    {ce.location_hints.length > 0 && (
                      <div className="mt-2 text-xs text-muted-foreground">
                        Hints: {ce.location_hints.join(", ")}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "patch" && (
        <div className="space-y-4 animate-in fade-in duration-300">
          {patches.length > 0 ? patches.map((patch, i) => (
            <div key={i} className="border rounded-lg overflow-hidden bg-card shadow-sm">
              <div className="bg-muted px-4 py-2 border-b flex justify-between items-center text-sm">
                <span className="font-medium">Attempt #{patch.attempt_number}</span>
                <div className="flex gap-4 text-muted-foreground">
                  <span className="text-green-600">+{patch.lines_added}</span>
                  <span className="text-red-600">-{patch.lines_removed}</span>
                </div>
              </div>
              <pre className="p-4 overflow-x-auto text-xs font-mono bg-zinc-950 text-zinc-50 leading-relaxed">
                {patch.unified_diff || "No diff content"}
              </pre>
            </div>
          )) : <EmptyState title="No Patches" description="No patches were generated in this run." />}
        </div>
      )}

      {activeTab === "report" && (
        <ReportViewer runId={id!} />
      )}
    </div>
  );
}

function Metric({ title, value }: { title: string, value: string | number }) {
  return (
    <div className="bg-card p-4 rounded-xl border shadow-sm">
      <div className="text-xs text-muted-foreground font-medium mb-1">{title}</div>
      <div className="text-xl font-semibold tracking-tight">{value}</div>
    </div>
  );
}
