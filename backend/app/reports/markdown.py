"""Professional deterministic Markdown rendering for a typed run report."""

from __future__ import annotations

from app.reports.models import PatchReport, RunReport, VerificationGateReport


def render_markdown(report: RunReport) -> str:
    """Render stored evidence without adding narrative claims or model output."""

    identity = report.identity
    outcome = report.outcome
    lines = [
        "# AgentTrace Run Analysis Report",
        "",
        "> This document reports evidence observed during one AgentTrace run. "
        "It is not a comprehensive repository health audit.",
        "",
        "## Run Summary",
        "",
        f"- **Report ID:** {_md(report.report_id)}",
        f"- **Run ID:** {_md(report.run_id)}",
        f"- **Configuration:** {_md(identity.configuration)}",
        f"- **Model:** {_md(identity.model)}",
        f"- **Final status:** {_md(outcome.final_status)}",
        f"- **Resolved:** {_tri_state(outcome.resolved)}",
        f"- **Generated:** {_md(report.generated_at.isoformat())}",
        "",
        "## Task",
        "",
        f"- **Repository:** {_md(identity.repository_name)}",
        f"- **Commit:** `{_md(identity.repository_commit)}`",
        f"- **Task source:** {_md(identity.task_source)}",
        f"- **Task:** {_md(identity.task_title)}",
        "",
        _md(identity.task_description),
        "",
        "## Issue Evidence",
        "",
        _md(report.issue_summary),
        "",
    ]
    if report.investigation.fault_localization is not None:
        localization = report.investigation.fault_localization
        lines.extend(["## Fault Localization", ""])
        lines.append(
            "Probabilistic localization evidence; suspiciousness does not establish fault truth."
        )
        lines.append("")
        for location in localization.suspicious_locations:
            symbol = f" — {_md(location.symbol)}" if location.symbol else ""
            lines.append(
                f"{location.rank}. `{_md(location.file)}:{location.line}`{symbol} — "
                f"{_md(localization.metric)} {location.score:.6f}"
            )
        lines.append("")

    lines.extend(["## Agent Investigation", ""])
    lines.append(f"- **Files inspected:** {report.investigation.files_inspected}")
    lines.append(f"- **Tool calls:** {len(report.investigation.tool_calls)}")
    if report.investigation.inspected_paths:
        lines.append(
            "- **Observed paths:** "
            + ", ".join(f"`{_md(path)}`" for path in report.investigation.inspected_paths)
        )
    for call in report.investigation.tool_calls:
        arguments = f" — {_md(call.arguments_summary)}" if call.arguments_summary else ""
        lines.append(f"- `{_md(call.tool)}` ({_md(call.status)}){arguments}")
    lines.append("")

    if report.initial_patch is not None:
        lines.extend(["## Initial Patch", ""])
        _append_patch(lines, report.initial_patch)

    lines.extend(["## Verification", ""])
    lines.append(f"**Final verification status:** {_md(report.verification.final_status)}")
    lines.append("")
    gates = [
        *report.verification.required_gates,
        *report.verification.advisory_gates,
        *report.verification.baseline_gates,
    ]
    if gates:
        lines.extend(
            [
                "| Attempt | Gate | Role | Status | Result |",
                "| ---: | --- | --- | --- | --- |",
            ]
        )
        for gate in gates:
            lines.append(_gate_row(gate))
    else:
        lines.append("No verification gate evidence was stored for this run.")
    lines.append("")

    if report.counterexamples:
        lines.extend(["## Counterexample", ""])
        for index, counterexample in enumerate(report.counterexamples, start=1):
            lines.append(f"### Counterexample {index}")
            lines.append("")
            lines.append(f"- **Source:** {_md(counterexample.source)}")
            lines.append(f"- **Failed gate:** {_md(counterexample.failed_gate)}")
            if counterexample.input_summary:
                lines.append(f"- **Input:** {_md(counterexample.input_summary)}")
            if counterexample.expected_behavior:
                lines.append(
                    f"- **Expected behavior:** {_md(counterexample.expected_behavior)}"
                )
            lines.append(
                f"- **Observed behavior:** {_md(counterexample.observed_behavior)}"
            )
            lines.append(
                f"- **New versus baseline:** {_tri_state(counterexample.new_vs_baseline)}"
            )
            if counterexample.location_hints:
                lines.append(
                    "- **Location hints:** "
                    + ", ".join(
                        f"`{_md(location)}`" for location in counterexample.location_hints
                    )
                )
            lines.append("")

    if report.repair is not None:
        lines.extend(["## CEGIS Repair", ""])
        lines.append(
            "Initial Patch → Verification Failure → Counterexample → "
            "Replacement Patch → Final Verification"
        )
        lines.append("")
        lines.append(f"- **Repair outcome:** {_md(report.repair.verification_outcome)}")
        lines.append(f"- **Repair successful:** {_tri_state(report.repair.successful)}")
        if report.repair.added_input_tokens is not None:
            lines.append(
                f"- **Incremental input tokens:** {report.repair.added_input_tokens}"
            )
        if report.repair.added_output_tokens is not None:
            lines.append(
                f"- **Incremental output tokens:** {report.repair.added_output_tokens}"
            )
        if report.repair.added_cost is not None:
            lines.append(f"- **Incremental estimated cost:** {_cost(report.repair.added_cost)}")
        if report.repair.added_latency_ms is not None:
            lines.append(
                f"- **Incremental latency:** {_duration(report.repair.added_latency_ms)}"
            )
        if report.repair.replacement_patch is not None:
            lines.append("")
            lines.append("### Replacement Patch")
            lines.append("")
            _append_patch(lines, report.repair.replacement_patch)

    lines.extend(["## Final Outcome", ""])
    lines.append(f"- **Run status:** {_md(outcome.final_status)}")
    lines.append(f"- **Resolution:** {_tri_state(outcome.resolved)}")
    lines.append(f"- **Verification:** {_md(outcome.final_verification_status)}")
    if outcome.failure_category:
        lines.append(f"- **Failure category:** {_md(outcome.failure_category)}")
    lines.append("")

    lines.extend(["## Evidence-Based Assessment", ""])
    for label, dimension in (
        ("Final Resolution", report.assessment.final_resolution),
        ("Verification Outcome", report.assessment.verification_outcome),
        ("Test / Oracle Strength", report.assessment.test_oracle_strength),
        ("Regression Evidence", report.assessment.regression_evidence),
        ("Patch Scope", report.assessment.patch_scope),
        ("Fault Localization Evidence", report.assessment.fault_localization_evidence),
        ("Repair Requirement", report.assessment.repair_requirement),
        ("Static Analysis", report.assessment.static_analysis),
    ):
        lines.append(f"### {label}: {_md(dimension.value)}")
        lines.append("")
        for basis in dimension.basis:
            lines.append(f"- {_md(basis)}")
        lines.append("")

    efficiency = report.efficiency
    lines.extend(["## Efficiency Metrics", ""])
    lines.extend(
        [
            f"- **Input tokens:** {efficiency.input_tokens}",
            f"- **Output tokens:** {efficiency.output_tokens}",
            f"- **Total tokens:** {efficiency.total_tokens}",
            f"- **Estimated cost:** {_cost(efficiency.estimated_cost)}",
            f"- **Latency:** {_duration(efficiency.total_latency_ms)}",
            f"- **Tool calls:** {efficiency.tool_calls}",
            f"- **Files inspected:** {efficiency.files_inspected}",
            f"- **Lines exposed:** {efficiency.lines_exposed}",
            "",
            "## Limitations",
            "",
        ]
    )
    lines.extend(f"- {_md(item)}" for item in report.limitations)
    lines.extend(
        [
            "",
            f"Evidence snapshot SHA-256: `{_md(report.evidence_sha256)}`",
            "",
        ]
    )
    return "\n".join(lines)


def _append_patch(lines: list[str], patch: PatchReport) -> None:
    lines.append(f"- **Files changed:** {len(patch.files_changed)}")
    lines.append(f"- **Lines added/removed:** +{patch.lines_added} / -{patch.lines_removed}")
    lines.append(f"- **Applied successfully:** {_tri_state(patch.applied_successfully)}")
    lines.append(f"- **Verification:** {_md(patch.verification_outcome)}")
    if patch.rationale:
        lines.append(f"- **Rationale:** {_md(patch.rationale)}")
    if patch.expected_behavioral_change:
        lines.append(
            f"- **Expected behavioral change:** {_md(patch.expected_behavioral_change)}"
        )
    if patch.files_changed:
        lines.append(
            "- **Changed paths:** "
            + ", ".join(f"`{_md(path)}`" for path in patch.files_changed)
        )
    lines.extend(["", "Patch:", ""])
    lines.extend(f"    {line}" if line else "    " for line in patch.unified_diff.splitlines())
    lines.append("")


def _gate_row(gate: VerificationGateReport) -> str:
    role = "required" if gate.required else "advisory"
    if gate.gate.startswith("baseline_"):
        role = f"baseline {role}"
    return (
        f"| {gate.attempt_number} | {_cell(gate.gate)} | {role} | "
        f"{_cell(gate.status)} | {_cell(gate.concise_result)} |"
    )


def _md(value: str) -> str:
    flattened = " ".join(str(value).splitlines())
    for character in ("\\", "`", "*", "_", "[", "]", "<", ">", "|"):
        flattened = flattened.replace(character, f"\\{character}")
    return flattened


def _cell(value: str) -> str:
    return _md(value)


def _tri_state(value: bool | None) -> str:
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "Not Assessed"


def _cost(value: float | None) -> str:
    return f"${value:.6f}" if value is not None else "Not Available"


def _duration(value: int | None) -> str:
    return f"{value} ms" if value is not None else "Not Available"
