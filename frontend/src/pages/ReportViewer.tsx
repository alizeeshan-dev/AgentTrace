import { useState, useEffect } from "react";
import { api, ApiError } from "../api/client";
import type { RunReport } from "../api/types";
import { LoadingState } from "../components/ui/LoadingState";
import { EmptyState } from "../components/ui/EmptyState";
import { FileText, CheckCircle2, AlertCircle, ShieldAlert, FileCode2 } from "lucide-react";
import { formatLatency, formatCost, formatTokens } from "../lib/utils";

export function ReportViewer({ runId }: { runId: string }) {
  const [report, setReport] = useState<RunReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function fetchReport() {
      try {
        const data = await api.getRunReport(runId);
        setReport(data);
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) {
          // Report not found, let parent handle creation state? No, we show nothing and let parent show 'Generate' button.
          setReport(null);
        } else {
          setError(err instanceof ApiError ? err.message : "Failed to load report");
        }
      } finally {
        setLoading(false);
      }
    }
    fetchReport();
  }, [runId]);

  if (loading) return <LoadingState />;
  if (error) return <EmptyState title="Report Error" description={error} isError />;
  if (!report) return null; // Parent will show "Generate Report" button instead

  const handleCopyMarkdown = async () => {
    try {
      const md = await api.getRunReportMarkdown(runId);
      await navigator.clipboard.writeText(md);
      alert("Markdown report copied to clipboard!");
    } catch {
      alert("Failed to copy markdown");
    }
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      
      {/* HEADER */}
      <div className="flex items-center justify-between border-b pb-4">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">Run Analysis Report</h2>
          <p className="text-sm text-muted-foreground mt-1">Generated {new Date(report.generated_at).toLocaleString()}</p>
        </div>
        <button 
          onClick={handleCopyMarkdown}
          className="inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium hover:bg-muted"
        >
          <FileText className="w-4 h-4" />
          Copy Markdown
        </button>
      </div>

      {/* SUMMARY */}
      <section className="grid md:grid-cols-2 gap-6">
        <div className="space-y-4">
          <h3 className="text-lg font-semibold border-b pb-2">Run Summary</h3>
          <dl className="grid grid-cols-3 gap-2 text-sm">
            <dt className="text-muted-foreground">Repository</dt>
            <dd className="col-span-2 font-mono flex items-center gap-2">
              {report.identity.repository_name} 
              {report.identity.task_source === "external" && <span className="text-[10px] bg-primary/10 text-primary px-1 rounded uppercase font-bold tracking-wider">External</span>}
            </dd>
            <dt className="text-muted-foreground">Commit</dt>
            <dd className="col-span-2 font-mono">{report.identity.repository_commit}</dd>
            <dt className="text-muted-foreground">Task</dt>
            <dd className="col-span-2">{report.identity.task_title}</dd>
            <dt className="text-muted-foreground">Config</dt>
            <dd className="col-span-2">{report.identity.configuration} — {report.identity.model}</dd>
            <dt className="text-muted-foreground">Final Status</dt>
            <dd className="col-span-2">
              {report.outcome.resolved ? (
                <span className="text-green-600 font-semibold flex items-center gap-1"><CheckCircle2 className="w-4 h-4"/> Resolved</span>
              ) : (
                <span className="text-red-600 font-semibold flex items-center gap-1"><AlertCircle className="w-4 h-4"/> Unresolved ({report.outcome.failure_category || "Failed"})</span>
              )}
            </dd>
          </dl>
        </div>
        
        <div className="space-y-4">
          <h3 className="text-lg font-semibold border-b pb-2">Evidence-Based Assessment</h3>
          <div className="space-y-2">
            <AssessmentRow label="Final Resolution" dim={report.assessment.final_resolution} />
            <AssessmentRow label="Verification Outcome" dim={report.assessment.verification_outcome} />
            <AssessmentRow label="Test / Oracle Strength" dim={report.assessment.test_oracle_strength} />
            <AssessmentRow label="Regression Evidence" dim={report.assessment.regression_evidence} />
            <AssessmentRow label="Patch Scope" dim={report.assessment.patch_scope} />
            <AssessmentRow label="Fault Localization" dim={report.assessment.fault_localization_evidence} />
            <AssessmentRow label="Repair Requirement" dim={report.assessment.repair_requirement} />
          </div>
        </div>
      </section>

      {/* LIMITATIONS */}
      {report.limitations.length > 0 && (
        <section className="bg-muted/30 border p-4 rounded-xl space-y-2 text-sm">
          <h3 className="font-semibold flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 text-amber-600 dark:text-amber-400" />
            Evidence Limitations
          </h3>
          <ul className="list-disc pl-5 space-y-1 text-muted-foreground">
            {report.limitations.map((lim, i) => <li key={i}>{lim}</li>)}
          </ul>
        </section>
      )}

      {/* ISSUE EVIDENCE */}
      <section className="space-y-4">
        <h3 className="text-lg font-semibold border-b pb-2">Issue Summary</h3>
        <p className="text-sm text-foreground whitespace-pre-wrap leading-relaxed bg-muted/20 p-4 rounded-lg border border-dashed">
          {report.issue_summary}
        </p>
      </section>

      {/* FAULT LOCALIZATION */}
      {report.investigation.fault_localization && report.investigation.fault_localization.suspicious_locations.length > 0 && (
        <section className="space-y-4">
          <h3 className="text-lg font-semibold border-b pb-2">Fault Localization Evidence</h3>
          <div className="border rounded-md overflow-hidden bg-card">
            <table className="w-full text-sm text-left">
              <thead className="bg-muted/50 border-b">
                <tr>
                  <th className="p-3 font-medium text-muted-foreground">Rank</th>
                  <th className="p-3 font-medium text-muted-foreground">Location</th>
                  <th className="p-3 font-medium text-muted-foreground text-right">Score</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {report.investigation.fault_localization.suspicious_locations.map(loc => (
                  <tr key={loc.rank}>
                    <td className="p-3">#{loc.rank}</td>
                    <td className="p-3 font-mono text-xs">{loc.file}:{loc.line} {loc.symbol && <span className="text-muted-foreground ml-2">({loc.symbol})</span>}</td>
                    <td className="p-3 text-right font-mono">{loc.score}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {/* INVESTIGATION */}
      {(report.investigation.files_inspected > 0 || report.investigation.tool_calls.length > 0) && (
        <section className="space-y-4">
          <h3 className="text-lg font-semibold border-b pb-2">Agent Investigation</h3>
          <div className="grid grid-cols-2 gap-4 text-sm mb-4">
            <div className="bg-card border p-3 rounded-md shadow-sm">
              <span className="text-muted-foreground">Files Inspected: </span>
              <span className="font-semibold">{report.investigation.files_inspected}</span>
            </div>
            <div className="bg-card border p-3 rounded-md shadow-sm">
              <span className="text-muted-foreground">Tool Calls: </span>
              <span className="font-semibold">{report.investigation.tool_calls.length}</span>
            </div>
          </div>
          {report.investigation.inspected_paths.length > 0 && (
            <div className="text-sm">
              <div className="font-medium text-muted-foreground mb-2">Paths Analyzed:</div>
              <div className="flex flex-wrap gap-2">
                {report.investigation.inspected_paths.map(p => (
                  <span key={p} className="inline-flex items-center gap-1.5 px-2 py-1 rounded-md bg-muted/50 border font-mono text-xs">
                    <FileCode2 className="w-3 h-3"/> {p}
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>
      )}

      {/* INITIAL PATCH */}
      {report.initial_patch && (
        <section className="space-y-4">
          <h3 className="text-lg font-semibold border-b pb-2">Initial Patch</h3>
          <div className="bg-card border rounded-lg overflow-hidden shadow-sm">
            <div className="bg-muted px-4 py-3 border-b flex justify-between items-center text-sm">
              <div>
                <span className="font-medium mr-4">Verification: </span>
                {report.initial_patch.verification_outcome.includes("pass") ? (
                  <span className="text-green-600 font-semibold">{report.initial_patch.verification_outcome}</span>
                ) : (
                  <span className="text-red-600 font-semibold">{report.initial_patch.verification_outcome}</span>
                )}
              </div>
              <div className="flex gap-4 text-muted-foreground font-mono">
                <span className="text-green-600">+{report.initial_patch.lines_added}</span>
                <span className="text-red-600">-{report.initial_patch.lines_removed}</span>
              </div>
            </div>
            <pre className="p-4 overflow-x-auto text-xs font-mono bg-zinc-950 text-zinc-50">
              {report.initial_patch.unified_diff}
            </pre>
          </div>
        </section>
      )}

      {/* CEGIS REPAIR PROGRESSION */}
      {report.repair && report.repair.attempted && report.counterexamples.length > 0 && (
        <section className="space-y-4">
          <h3 className="text-lg font-semibold border-b pb-2">CEGIS Repair Flow</h3>
          <div className="relative border-l-2 border-muted ml-4 space-y-6 pb-2">
            
            <div className="relative pl-6">
              <div className="absolute w-4 h-4 bg-red-500 rounded-full -left-[9px] top-0 ring-4 ring-background" />
              <div className="font-semibold text-sm">Verification Failure</div>
              <div className="mt-2 p-3 bg-red-50 dark:bg-red-950/20 border border-red-100 dark:border-red-900/30 rounded-lg text-sm font-mono text-red-900 dark:text-red-400 space-y-1">
                {report.counterexamples.map(ce => (
                  <div key={ce.failed_gate}>
                    <div><span className="text-muted-foreground">Source:</span> {ce.source} ({ce.failed_gate})</div>
                    <div><span className="text-muted-foreground">Observed:</span> {ce.observed_behavior}</div>
                    {ce.input_summary && <div><span className="text-muted-foreground">Input:</span> {ce.input_summary}</div>}
                  </div>
                ))}
              </div>
            </div>

            {report.repair.replacement_patch && (
              <div className="relative pl-6">
                <div className="absolute w-4 h-4 bg-blue-500 rounded-full -left-[9px] top-0 ring-4 ring-background" />
                <div className="font-semibold text-sm">Replacement Patch Proposed</div>
                <div className="mt-2 p-3 bg-card border rounded-lg text-xs font-mono bg-zinc-950 text-zinc-50 overflow-x-auto">
                  {report.repair.replacement_patch.unified_diff}
                </div>
              </div>
            )}

            <div className="relative pl-6">
              <div className="absolute w-4 h-4 bg-primary rounded-full -left-[9px] top-0 ring-4 ring-background flex items-center justify-center">
                {report.repair.successful ? <CheckCircle2 className="w-3 h-3 text-white"/> : <AlertCircle className="w-3 h-3 text-white"/>}
              </div>
              <div className="font-semibold text-sm">Final Verification</div>
              <div className="mt-1 text-sm text-muted-foreground">
                Outcome: <span className="font-semibold text-foreground">{report.repair.verification_outcome}</span>
              </div>
            </div>

          </div>
        </section>
      )}
      
      {/* EFFICIENCY */}
      <section className="space-y-4 pt-4 border-t">
         <h3 className="text-lg font-semibold mb-2">Efficiency Metrics</h3>
         <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
           <div className="bg-card p-3 rounded-xl border shadow-sm text-center">
             <div className="text-xs text-muted-foreground mb-1">Total Tokens</div>
             <div className="font-semibold">{formatTokens(report.efficiency.total_tokens)}</div>
           </div>
           <div className="bg-card p-3 rounded-xl border shadow-sm text-center">
             <div className="text-xs text-muted-foreground mb-1">Est. Cost</div>
             <div className="font-semibold">{formatCost(report.efficiency.estimated_cost)}</div>
           </div>
           <div className="bg-card p-3 rounded-xl border shadow-sm text-center">
             <div className="text-xs text-muted-foreground mb-1">Latency</div>
             <div className="font-semibold">{formatLatency(report.efficiency.total_latency_ms)}</div>
           </div>
           <div className="bg-card p-3 rounded-xl border shadow-sm text-center">
             <div className="text-xs text-muted-foreground mb-1">Tool Calls</div>
             <div className="font-semibold">{report.efficiency.tool_calls}</div>
           </div>
         </div>
      </section>

    </div>
  );
}

function AssessmentRow({ label, dim }: { label: string, dim: { value: string, basis: string[] } }) {
  if (!dim || dim.value === "Not Assessed") return null;
  return (
    <div className="flex flex-col py-1.5 border-b last:border-0 border-muted/50">
      <div className="flex justify-between items-center mb-1">
        <span className="text-sm font-medium text-muted-foreground">{label}</span>
        <span className="text-sm font-semibold">{dim.value}</span>
      </div>
      {dim.basis.map((b, i) => (
        <div key={i} className="text-xs text-muted-foreground italic">— {b}</div>
      ))}
    </div>
  );
}
