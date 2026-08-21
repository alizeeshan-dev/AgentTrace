# Pilot-Task Concepts

These concepts are for later construction of engineering pilot fixtures. They are not benchmark manifests, do not name invented repositories or commits, and must not be included in confirmatory analysis. Each needs a real permitted source, immutable commit, independent hidden evaluator, reference outcome, and full [admission review](task-selection.md).

## 1. Empty delimited input

- **Type / difficulty:** `bug_fix` / `easy`
- **Concept:** A record parser treats an empty string as one empty field or raises an index error; the specified result is an empty record collection while non-empty parsing remains unchanged.
- **Visible evidence:** Ordinary one-row and multi-column examples plus a concise task description.
- **Hidden focus:** Empty string, whitespace policy, repeated delimiters, line-ending variants, and regression cases for non-empty records.
- **Expected scope:** One parser module, fewer than roughly 15 changed lines.
- **Research value:** Tests whether prepared context is enough for a common edge case and whether failed visible verification enables a bounded repair without encouraging a special-case regression.
- **Admission work:** Choose a real small parser repository/revision, settle whitespace semantics with curators, and ensure tests accept more than one reasonable implementation.

## 2. Exact-multiple pagination boundary

- **Type / difficulty:** `bug_fix` / `medium`
- **Concept:** A pagination helper produces an extra empty page when item count is an exact multiple of page size because its boundary calculation is off by one.
- **Visible evidence:** Typical non-multiple examples and public API contract.
- **Hidden focus:** Zero items, exactly one full page, multiple full pages, one-over/one-under boundaries, invalid page size, and unchanged ordering.
- **Expected scope:** One or two modules and a small arithmetic/iteration correction.
- **Research value:** Provides a localized but repository-dependent boundary bug where inspection may reveal shared validation or caller assumptions.
- **Admission work:** Verify that the baseline defect is real, page-size validation is unambiguous, and the hidden suite distinguishes a genuine fix from dropping empty results indiscriminately.

## 3. Nested configuration mutation

- **Type / difficulty:** `bug_fix` / `medium`
- **Concept:** A configuration normalization function shallow-copies its input, then mutates a nested mapping, unexpectedly changing caller-owned state.
- **Visible evidence:** Normalized output examples and a statement that inputs remain unchanged.
- **Hidden focus:** Nested dictionaries/lists, reused input objects, already-normalized values, missing optional sections, and preservation of supported custom mapping behavior.
- **Expected scope:** One normalization module and possibly one small helper.
- **Research value:** Requires inspection of callers and existing copy conventions; superficially plausible fixes may over-copy unsupported objects or change output types.
- **Admission work:** Select explicit copy semantics, avoid tasks whose correct behavior depends on undocumented object identity, and bound supported input types.

## 4. Consolidate duplicate identifier normalization

- **Type / difficulty:** `refactor` / `hard`
- **Concept:** Two nearby code paths implement equivalent identifier normalization with small structural duplication. Extract one internal helper while preserving every public result and exception behavior.
- **Visible evidence:** Existing behavior tests and a refactor statement naming the duplicated responsibility, not a required implementation diff.
- **Hidden focus:** Full behavioral regression suite, exception type/message stability where contractual, and a curator-defined structural acceptance check that rejects leaving both duplicated implementations intact.
- **Expected scope:** At most three production files and under 100 changed lines.
- **Research value:** Tests whether tools help locate all call sites and whether verification catches behavior drift in a task without a user-visible bug.
- **Admission work:** Ensure the structural check admits reasonable helper placement/naming, prove the baseline acceptance check fails for the intended duplication, and avoid style-only assertions.

## Pilot balance

The first three built pilots should include at least two bug fixes and one refactor, span at least two difficulty levels, and exercise different failure modes. Pilot outcomes may change later engineering but may not be used to select favorable confirmatory tasks or estimate the final hypotheses.
