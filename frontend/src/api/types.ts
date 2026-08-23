export type Identifier = string; // simplified for TS
export type JsonValue = any;

export interface Repository {
  repository_id: string;
  name: string;
  source_type?: string;
  repository_url?: string;
  base_commit: string;
  default_branch?: string;
  primary_language?: string;
  python_version?: string;
  test_command?: string;
  trusted_for_local_execution?: boolean;
  trust_confirmed_at?: string;
  registered_at?: string;
  verification_configured?: boolean;
  metadata?: Record<string, JsonValue>;
}

export interface Task {
  task_id: string;
  repository_id: string;
  title: string;
  description: string;
  task_category: "bug_fix" | "refactor";
  difficulty: "easy" | "medium" | "hard";
  allowed_paths?: string[];
  forbidden_paths?: string[];
  visible_test_command?: string;
  hidden_test_command?: string;
  hidden_tests_available?: boolean;
  property_profile?: string;
  symbolic_profile?: string;
  known_correct_patch?: string;
  task_source?: "benchmark" | "external";
  verification_configured?: boolean;
  created_at?: string;
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

export interface RunReportMetadata {
  report_status: string;
  report_id: string;
  run_id: string;
  report_version: string;
  generated_at: string;
  evidence_sha256: string;
  markdown_artifact: string;
  markdown_sha256: string;
}

export interface ReportIdentity {
  repository_id: string;
  repository_name: string;
  repository_url?: string;
  repository_commit: string;
  task_id: string;
  task_title: string;
  task_description: string;
  task_source: "benchmark" | "external";
  task_category: string;
  difficulty: string;
  configuration: string;
  model: string;
}

export interface ReportOutcome {
  final_status: string;
  resolved?: boolean;
  repair_attempted: boolean;
  repair_successful?: boolean;
  final_verification_status: string;
  failure_category?: string;
}

export interface ToolCallReport {
  sequence_number: number;
  tool: string;
  arguments_summary?: string;
  status: string;
}

export interface SuspiciousLocationReport {
  rank: number;
  file: string;
  line: number;
  score: number;
  symbol?: string;
}

export interface FaultLocalizationReport {
  metric: string;
  source_run_id: string;
  top_k: number;
  suspicious_locations: SuspiciousLocationReport[];
}

export interface InvestigationReport {
  files_inspected: number;
  inspected_paths: string[];
  tool_calls: ToolCallReport[];
  fault_localization?: FaultLocalizationReport;
}

export interface PatchReport {
  attempt_number: number;
  files_changed: string[];
  lines_added: number;
  lines_removed: number;
  applied_successfully: boolean;
  patch_sha256: string;
  unified_diff: string;
  rationale?: string;
  expected_behavioral_change?: string;
  verification_outcome: string;
}

export interface CounterexampleReport {
  source: string;
  failed_gate: string;
  input_summary?: string;
  expected_behavior?: string;
  observed_behavior: string;
  failure_type?: string;
  location_hints: string[];
  new_vs_baseline: boolean;
  safe_feedback: string;
}

export interface RepairReport {
  attempted: boolean;
  replacement_patch?: PatchReport;
  verification_outcome: string;
  added_input_tokens?: number;
  added_output_tokens?: number;
  added_cost?: number;
  added_latency_ms?: number;
  successful?: boolean;
}

export interface VerificationGateReport {
  attempt_number: number;
  gate: string;
  required: boolean;
  status: string;
  concise_result: string;
  baseline_difference?: Record<string, JsonValue>;
  duration_ms: number;
}

export interface VerificationReport {
  final_attempt?: number;
  final_status: string;
  required_gates: VerificationGateReport[];
  advisory_gates: VerificationGateReport[];
  baseline_gates: VerificationGateReport[];
  regression_detected?: boolean;
}

export interface EfficiencyReport {
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost?: number;
  total_latency_ms?: number;
  tool_calls: number;
  files_inspected: number;
  lines_exposed: number;
}

export interface AssessmentDimension {
  value: string;
  basis: string[];
}

export interface AssessmentReport {
  final_resolution: AssessmentDimension;
  verification_outcome: AssessmentDimension;
  test_oracle_strength: AssessmentDimension;
  regression_evidence: AssessmentDimension;
  patch_scope: AssessmentDimension;
  fault_localization_evidence: AssessmentDimension;
  repair_requirement: AssessmentDimension;
  static_analysis: AssessmentDimension;
}

export interface RunReport {
  schema_version: number;
  report_version: string;
  report_status: string;
  report_id: string;
  run_id: string;
  generated_at: string;
  identity: ReportIdentity;
  outcome: ReportOutcome;
  issue_summary: string;
  investigation: InvestigationReport;
  initial_patch?: PatchReport;
  counterexamples: CounterexampleReport[];
  repair?: RepairReport;
  verification: VerificationReport;
  efficiency: EfficiencyReport;
  assessment: AssessmentReport;
  limitations: string[];
  evidence_sha256: string;
  markdown_artifact?: string;
  markdown_sha256?: string;
}
