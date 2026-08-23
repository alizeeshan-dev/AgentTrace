import { useEffect, useState } from "react";
import { api, ApiError } from "../api/client";
import { useNavigate } from "react-router-dom";
import { Loader2 } from "lucide-react";
import type { Task } from "../api/types";

export function NewRun() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [form, setForm] = useState({
    task_id: "",
    configuration_id: "A",
    model: "gemini-3.7-flash",
  });

  useEffect(() => {
    api.getTasks()
      .then((availableTasks) => {
        setTasks(availableTasks);
        if (availableTasks.length > 0) {
          setForm((current) => ({...current, task_id: availableTasks[0].task_id}));
        }
      })
      .catch((err) => {
        setError(err instanceof ApiError ? err.message : "Could not load benchmark tasks.");
      })
      .finally(() => setTasksLoading(false));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const response = await api.createRun(form);
      navigate(`/runs/${response.run_id}`);
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.status === 404) {
           setError("Backend limitation: The POST /runs endpoint is not implemented. Cannot start new run.");
        } else {
           setError(err.message);
        }
      } else {
        setError("An unknown error occurred.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-8">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">New Run</h1>
        <p className="text-muted-foreground mt-1">Start a new AgentTrace benchmark evaluation.</p>
      </div>

      {error && (
        <div className="rounded-md bg-destructive/15 p-4 text-destructive border border-destructive/20 text-sm">
          <p className="font-semibold">Failed to start run</p>
          <p className="mt-1">{error}</p>
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6 bg-card p-6 rounded-xl border shadow-sm">
        
        <div className="space-y-2">
          <label className="text-sm font-medium">Benchmark Task ID</label>
          <select
            className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2" 
            value={form.task_id}
            onChange={(e) => setForm({...form, task_id: e.target.value})}
            disabled={tasksLoading || tasks.length === 0}
            required
          >
            {tasksLoading && <option value="">Loading qualified tasks…</option>}
            {!tasksLoading && tasks.length === 0 && <option value="">No qualified tasks available</option>}
            {tasks.map((task) => (
              <option key={task.task_id} value={task.task_id}>
                {task.task_id} — {task.title}
              </option>
            ))}
          </select>
        </div>

        <div className="space-y-3">
          <label className="text-sm font-medium">Configuration</label>
          <div className="grid gap-3">
            {[
              { id: "A", name: "Direct Patch", desc: "One-shot patch generation without iterative repository tools." },
              { id: "B", name: "Tool Agent", desc: "Agent can inspect the repository before generating a patch." },
              { id: "C", name: "Verified CEGIS", desc: "Verification failures can produce one bounded repair attempt." },
              { id: "D", name: "Research Enhanced", desc: "Adds fault-localization and configured research evidence to the CEGIS workflow." },
            ].map(cfg => (
              <label key={cfg.id} className={`flex items-start gap-3 p-4 rounded-lg border cursor-pointer transition-colors ${form.configuration_id === cfg.id ? "bg-primary/5 border-primary" : "hover:bg-muted/50"}`}>
                <input 
                  type="radio" 
                  name="config" 
                  value={cfg.id} 
                  checked={form.configuration_id === cfg.id}
                  onChange={(e) => setForm({...form, configuration_id: e.target.value})}
                  className="mt-1"
                />
                <div>
                  <div className="font-semibold">{cfg.id} — {cfg.name}</div>
                  <div className="text-sm text-muted-foreground mt-1">{cfg.desc}</div>
                </div>
              </label>
            ))}
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium">Model</label>
          <select 
            className="flex h-10 w-full items-center justify-between rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            value={form.model}
            onChange={(e) => setForm({...form, model: e.target.value})}
          >
            <option value="gemini-3.7-flash">Gemini 3.7 Flash</option>
          </select>
        </div>

        <div className="pt-4 flex justify-end">
          <button 
            type="submit" 
            disabled={loading || tasksLoading || tasks.length === 0}
            className="inline-flex items-center justify-center rounded-md bg-primary px-6 py-2.5 text-sm font-medium text-primary-foreground shadow transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {loading ? <><Loader2 className="w-4 h-4 mr-2 animate-spin"/> Starting...</> : "Start Run"}
          </button>
        </div>
      </form>
    </div>
  );
}
