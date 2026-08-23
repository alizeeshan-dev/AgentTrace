import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Check, CircleDot, GitBranch, HelpCircle, Loader2, ShieldCheck, Terminal } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Task } from "../api/types";
import { PageHeader, Panel, PanelHeading } from "../components/ui/ResearchUI";
import { cn } from "../lib/utils";

type RunMode = "benchmark" | "external";

const configurations = [
  { id: "A", name: "Direct Patch", short: "One-shot · no tools", desc: "One patch from deterministic context." },
  { id: "B", name: "Tool Agent", short: "Inspect · then patch", desc: "Bounded repository investigation." },
  { id: "C", name: "Verified CEGIS", short: "Verify · 1 repair", desc: "Counterexample-guided repair." },
  { id: "D", name: "Research Enhanced", short: "SBFL · verify · repair", desc: "Localization and configured evidence." },
];

export function NewRun() {
  const navigate = useNavigate();
  const [mode, setMode] = useState<RunMode>("external");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [repoUrl, setRepoUrl] = useState("");
  const [repoData, setRepoData] = useState<any>(null);
  const [trustConfirmed, setTrustConfirmed] = useState(false);
  const [taskData, setTaskData] = useState({ title: "", description: "", test_command: "" });
  const [taskId, setTaskId] = useState("");
  const [configurationId, setConfigurationId] = useState("D");
  const [model, setModel] = useState("gemini-3.7-flash");

  useEffect(() => {
    api.getTasks().then((items) => {
      const benchmarkTasks = items.filter((task) => task.task_source !== "external");
      setTasks(benchmarkTasks);
      if (benchmarkTasks[0]) setTaskId(benchmarkTasks[0].task_id);
    }).catch((err) => setTasksError(err instanceof ApiError ? err.message : "Tasks could not be loaded."));
  }, []);

  const currentStep = mode === "benchmark" ? (taskId ? 4 : 2) : !repoData ? 1 : !trustConfirmed || !taskData.description ? 2 : 4;

  const registerRepository = async () => {
    if (!repoUrl.trim()) return;
    setLoading(true); setError(null);
    try {
      const data = await api.registerExternalRepository({ repository_url: repoUrl.trim() });
      setRepoData(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to register repository.");
    } finally { setLoading(false); }
  };

  const confirmTrust = async () => {
    if (!repoData) return;
    setLoading(true); setError(null);
    try {
      const data = await api.setRepositoryTrust(repoData.repository_id, { trusted_for_local_execution: true, acknowledgement: true });
      setRepoData(data); setTrustConfirmed(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to update trust settings.");
    } finally { setLoading(false); }
  };

  const startRun = async () => {
    setLoading(true); setError(null);
    try {
      let selectedTaskId = taskId;
      if (mode === "external") {
        if (!repoData || !trustConfirmed || !taskData.description.trim()) throw new Error("Complete the repository trust and task fields first.");
        const task = await api.createExternalTask({
          repository_id: repoData.repository_id,
          title: taskData.title.trim() || "External Task",
          description: taskData.description.trim(),
          test_command: taskData.test_command.trim() || undefined,
          trusted_execution_acknowledged: true,
        });
        selectedTaskId = task.task_id;
      }
      if (!selectedTaskId) throw new Error("Select a benchmark task first.");
      const run = await api.createRun({ task_id: selectedTaskId, configuration_id: configurationId, model });
      navigate(`/runs/${run.run_id}`);
    } catch (err) {
      setError(err instanceof ApiError || err instanceof Error ? err.message : "Failed to start run.");
    } finally { setLoading(false); }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="New Run"
        subtitle="Choose a source, define the repair objective and preview the execution plan."
        action={<span className="inline-flex items-center gap-2 rounded-lg border border-border bg-white px-3 py-2 text-xs font-semibold text-muted-foreground"><HelpCircle className="h-3.5 w-3.5" /> Run guide</span>}
      />

      <Panel className="px-5 py-4">
        <div className="grid grid-cols-4 gap-2">
          {["Repository", "Task", "Configuration", "Review & run"].map((label, index) => {
            const number = index + 1;
            const active = number <= currentStep;
            return (
              <div key={label} className="flex items-center gap-3">
                <span className={cn("grid h-7 w-7 shrink-0 place-items-center rounded-full text-[10px] font-bold", active ? "bg-primary text-white" : "bg-muted text-muted-foreground")}>{number}</span>
                <span className={cn("hidden text-[10px] font-bold sm:block", active ? "text-foreground" : "text-muted-foreground")}>{label}</span>
                {index < 3 && <span className="ml-auto hidden h-px min-w-4 flex-1 bg-border md:block" />}
              </div>
            );
          })}
        </div>
      </Panel>

      {error && <div className="rounded-xl border border-[#efc5c5] bg-[var(--red-soft)] px-4 py-3 text-sm text-[#a84046]"><strong>Run setup failed.</strong> {error}</div>}

      <div className="grid items-start gap-4 lg:grid-cols-[1.65fr_0.75fr]">
        <Panel className="p-5">
          <PanelHeading title="Source" />
          <div className="mt-4 grid grid-cols-2 rounded-lg bg-muted p-1">
            <ModeButton active={mode === "benchmark"} onClick={() => setMode("benchmark")}>Benchmark task</ModeButton>
            <ModeButton active={mode === "external"} onClick={() => setMode("external")}>External repository</ModeButton>
          </div>

          {mode === "benchmark" ? (
            <div className="mt-5 space-y-5">
              <Field label="Benchmark task">
                <select className="research-input" value={taskId} onChange={(event) => setTaskId(event.target.value)} disabled={!tasks.length}>
                  {!tasks.length && <option value="">No registered benchmark tasks</option>}
                  {tasks.map((task) => <option key={task.task_id} value={task.task_id}>{task.task_id} — {task.title}</option>)}
                </select>
                {tasksError && <p className="mt-2 text-xs text-[#a84046]">{tasksError}</p>}
              </Field>
              {tasks.find((task) => task.task_id === taskId) && <TaskSummary task={tasks.find((task) => task.task_id === taskId)!} />}
              <RunConfiguration value={configurationId} onChange={setConfigurationId} />
              <ModelSelector value={model} onChange={setModel} />
            </div>
          ) : (
            <div className="mt-5 space-y-5">
              <Field label="Repository URL">
                <div className="flex flex-col gap-2 sm:flex-row">
                  <input className="research-input font-mono text-xs" value={repoUrl} onChange={(event) => setRepoUrl(event.target.value)} placeholder="https://github.com/example/parser-tool.git" disabled={Boolean(repoData)} />
                  <button type="button" onClick={registerRepository} disabled={loading || !repoUrl || Boolean(repoData)} className="research-button shrink-0 px-5">{loading && !repoData ? <Loader2 className="h-4 w-4 animate-spin" /> : null} Add repo</button>
                </div>
              </Field>

              {repoData && (
                <div className="rounded-xl border border-[#d7e2d2] bg-[#f2f6ef] p-4">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="flex items-center gap-2 text-sm font-bold"><GitBranch className="h-4 w-4 text-primary" /> {repoData.name}</div>
                      <div className="mt-2 flex flex-wrap gap-3 font-mono text-[10px] text-muted-foreground"><span>{repoData.base_commit?.slice(0, 10)}</span><span>{repoData.python_version || repoData.primary_language || "Python"}</span><span>{repoData.verification_configured ? "tests configured" : "verification pending"}</span></div>
                    </div>
                    <button type="button" onClick={() => { setRepoData(null); setTrustConfirmed(false); }} className="text-xs font-semibold text-primary hover:underline">Change</button>
                  </div>
                </div>
              )}

              {repoData && !trustConfirmed && (
                <div className="rounded-xl border border-[#efd9a4] bg-[var(--amber-soft)] p-4 text-xs text-[#7f5a11]">
                  <div className="flex items-start gap-3"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0" /><div><div className="font-bold">Local execution trust</div><p className="mt-1 leading-relaxed">Repository tests may execute on this Windows machine. Continue only for a trusted, pre-qualified repository.</p></div></div>
                  <button type="button" onClick={confirmTrust} disabled={loading} className="mt-3 inline-flex items-center gap-2 rounded-lg border border-[#e5ca86] bg-white/70 px-3 py-2 font-bold">{loading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />} Trust repository</button>
                </div>
              )}

              {trustConfirmed && (
                <>
                  <Field label="Repair objective">
                    <input className="research-input" value={taskData.title} onChange={(event) => setTaskData({ ...taskData, title: event.target.value })} placeholder="Fix empty-input parser failure" />
                    <textarea className="research-input mt-2 min-h-24 resize-y" value={taskData.description} onChange={(event) => setTaskData({ ...taskData, description: event.target.value })} placeholder="Describe the behavior AgentTrace should investigate and repair." />
                  </Field>
                  <Field label="Test command (optional)">
                    <div className="relative"><Terminal className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><input className="research-input pl-9 font-mono text-xs" value={taskData.test_command} onChange={(event) => setTaskData({ ...taskData, test_command: event.target.value })} placeholder={repoData.test_command || "pytest tests/"} /></div>
                    {!repoData.verification_configured && !taskData.test_command && <p className="mt-2 text-[10px] text-[#9a6a00]">Without a trusted test command, verification evidence will be unavailable.</p>}
                  </Field>
                  <RunConfiguration value={configurationId} onChange={setConfigurationId} />
                  <ModelSelector value={model} onChange={setModel} />
                </>
              )}
            </div>
          )}
        </Panel>

        <ExecutionPreview configurationId={configurationId} />
      </div>

      <div className="flex justify-end">
        <button type="button" className="research-button min-w-40" disabled={loading || (mode === "benchmark" ? !taskId : !trustConfirmed || !taskData.description.trim())} onClick={startRun}>
          {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <CircleDot className="h-4 w-4" />}
          {loading ? "Preparing run…" : "Review & start run"}
        </button>
      </div>
    </div>
  );
}

function ModeButton({ active, onClick, children }: { active: boolean; onClick: () => void; children: string }) {
  return <button type="button" onClick={onClick} className={cn("rounded-md px-3 py-2 text-xs font-semibold transition", active ? "bg-white text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>{children}</button>;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="block"><span className="mb-2 block text-[11px] font-bold text-foreground">{label}</span>{children}</label>;
}

function TaskSummary({ task }: { task: Task }) {
  return <div className="rounded-xl border border-[#d7e2d2] bg-[#f2f6ef] p-4"><div className="text-sm font-bold">{task.title}</div><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{task.description}</p><div className="mt-3 flex gap-2"><span className="rounded bg-white px-2 py-1 text-[9px] font-bold uppercase text-primary">{task.difficulty}</span><span className="rounded bg-white px-2 py-1 text-[9px] font-bold uppercase text-primary">{task.task_category.replace("_", " ")}</span></div></div>;
}

function RunConfiguration({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <div><div className="mb-3 text-[11px] font-bold">Configuration</div><div className="grid gap-2 sm:grid-cols-2">{configurations.map((config) => <button type="button" key={config.id} onClick={() => onChange(config.id)} className={cn("relative rounded-xl border p-3 text-left transition", value === config.id ? "border-primary bg-[#edf3e9]" : "border-border bg-white hover:bg-[#f7f9f5]")}><div className="flex items-center gap-2"><span className={cn("grid h-6 w-6 place-items-center rounded-md text-[10px] font-bold", value === config.id ? "bg-primary text-white" : "bg-muted text-muted-foreground")}>{config.id}</span><div><div className="text-xs font-bold">{config.name}</div><div className="text-[9px] text-muted-foreground">{config.short}</div></div></div>{value === config.id && <Check className="absolute right-3 top-3 h-3.5 w-3.5 text-primary" />}</button>)}</div></div>;
}

function ModelSelector({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return <Field label="Model"><select className="research-input" value={value} onChange={(event) => onChange(event.target.value)}><option value="gemini-3.7-flash">Gemini 3.7 Flash</option><option value="gemini-2.5-flash">Gemini 2.5 Flash</option><option value="gemini-2.5-pro">Gemini 2.5 Pro</option></select></Field>;
}

function ExecutionPreview({ configurationId }: { configurationId: string }) {
  const steps = useMemo(() => {
    const result = [{ label: "Prepare workspace", note: "Disposable Git copy" }];
    if (configurationId === "D") result.push({ label: "Fault localization", note: "SBFL / Ochiai if eligible" });
    if (configurationId !== "A") result.push({ label: "Agent investigation", note: "Restricted repository tools" });
    result.push({ label: "Patch P0", note: "Complete unified diff" }, { label: "Verification", note: "Required + advisory gates" });
    if (["C", "D"].includes(configurationId)) result.push({ label: "Counterexample", note: "Only on candidate failure" }, { label: "Patch P1", note: "Maximum one repair" });
    result.push({ label: "Final result", note: "Trace + report-ready evidence" });
    return result;
  }, [configurationId]);
  return <Panel className="sticky top-5 overflow-hidden p-5"><PanelHeading title="Execution preview" subtitle="Research-enhanced bounded repair" action={<span className="rounded-full bg-[#edf2e9] px-2 py-1 text-[9px] font-bold text-primary">CONFIG {configurationId}</span>} /><div className="mt-5 space-y-0">{steps.map((step, index) => <div key={step.label} className="relative flex gap-3 pb-5 last:pb-1"><span className={cn("relative z-10 grid h-7 w-7 shrink-0 place-items-center rounded-full text-[10px] font-bold", index === steps.length - 1 ? "bg-[#20a673] text-white" : index === steps.length - 3 && ["C", "D"].includes(configurationId) ? "bg-[#d59a18] text-white" : "bg-[#88a78c] text-white")}>{index + 1}</span>{index < steps.length - 1 && <span className="absolute left-[13px] top-7 h-full w-px bg-border" />}<div className="pt-0.5"><div className="text-xs font-bold">{step.label}</div><div className="mt-1 text-[9px] text-muted-foreground">{step.note}</div></div></div>)}</div><div className="mt-5 rounded-xl bg-[#f3f6f1] p-3 text-[10px] text-muted-foreground"><div className="flex justify-between"><span>Execution budget</span><strong className="text-foreground">1 repair max</strong></div><div className="mt-2 flex justify-between"><span>Evidence</span><strong className="text-foreground">bounded tools · trusted repo</strong></div></div><div className="mt-4 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full w-full rounded-full bg-primary" /></div></Panel>;
}
