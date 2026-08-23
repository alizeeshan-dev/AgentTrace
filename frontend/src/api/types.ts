export type Identifier = string; // simplified for TS
export type JsonValue = any;

export interface Repository {
  repository_id: string;
  name: string;
  source: string;
  base_commit: string;
  python_version?: string;
  test_command: string;
}

export interface Task {
  task_id: string;
  repository_id: string;
  title: string;
  description: string;
  task_category: "bug_fix" | "refactor";
  difficulty: "easy" | "medium" | "hard";
  allowed_paths: string[];
  forbidden_paths: string[];
  visible_test_command: string;
  hidden_test_command: string;
  property_profile?: string;
  symbolic_profile?: string;
  known_correct_patch?: string;
}

export interface Run {
  run_id: string;
  task_id: string;
  configuration_id: string;
  model: string;
  model_parameters: Record<string, JsonValue>;
  status: string;
  started_at: string;
  finished_at?: string;
  latency_ms?: number;
  input_tokens: number;
  output_tokens: number;
  estimated_cost?: number;
  tool_calls: number;
  files_read: number;
  lines_exposed: number;
  repair_attempted: boolean;
  final_resolution?: boolean;
  failure_category?: string;
}

export interface TraceEvent {
  event_id: string;
  run_id: string;
  sequence_number: number;
  parent_event_id?: string;
  operation: string;
  started_at: string;
  finished_at?: string;
  status: string;
  input_summary?: string;
  output_summary?: string;
  error_type?: string;
}

export interface FaultLocalizationResult {
  run_id: string;
  metric: string;
  ranked_locations: Record<string, JsonValue>[];
  top_k: number;
  fault_rank_if_known?: number;
  coverage_artifact?: string;
}

export interface PatchArtifact {
  run_id: string;
  attempt_number: number;
  unified_diff: string;
  files_changed: string[];
  lines_added: number;
  lines_removed: number;
  applied_successfully: boolean;
}

export interface VerificationResult {
  run_id: string;
  attempt_number: number;
  gate: string;
  required: boolean;
  status: string;
  exit_code?: number;
  duration_ms: number;
  baseline_difference?: Record<string, JsonValue>;
  summary: string;
  log_artifact?: string;
}

export interface Counterexample {
  counterexample_id: string;
  run_id: string;
  attempt_number: number;
  source: string;
  gate: string;
  input_summary?: string;
  expected_summary?: string;
  observed_summary: string;
  failure_type?: string;
  location_hints: string[];
  is_new_vs_baseline: boolean;
  log_excerpt?: string;
  sanitized_feedback: string;
}

export interface RunDetail {
  run: Run;
  task: Task;
  trace: TraceEvent[];
  verification: VerificationResult[];
  patches: PatchArtifact[];
  counterexamples: Counterexample[];
  sbfl?: FaultLocalizationResult;
}
