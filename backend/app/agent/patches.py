"""Unified-diff safety validation and orchestrator-only application primitives."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from typing import Annotated

from pydantic import Field, StringConstraints

from app.agent.budgets import AgentBudgets
from app.repositories.git import GitError, run_git
from app.repositories.path_policy import PathPolicyError, RepositoryPathPolicy
from app.schemas.common import ResearchSchema

_DIFF_HEADER = re.compile(r"^diff --git a/([^\s]+) b/([^\s]+)$")
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@(?: .*)?$")
_PROHIBITED_METADATA = (
    "GIT binary patch",
    "Binary files ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
    "new file mode ",
    "deleted file mode ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "dissimilarity index ",
    "Subproject commit ",
)


class PatchValidationError(ValueError):
    """Raised when a candidate patch is unsafe, malformed, or inapplicable."""


class PatchValidationResult(ResearchSchema):
    """Auditable summary binding validation to the exact patch bytes."""

    files_changed: list[str] = Field(min_length=1)
    lines_added: int = Field(ge=0)
    lines_removed: int = Field(ge=0)
    patch_bytes: int = Field(ge=1)
    patch_lines: int = Field(ge=1)
    patch_sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class PatchValidator:
    """Validate a text patch against repository, task, and experiment limits."""

    def __init__(self, policy: RepositoryPathPolicy, budgets: AgentBudgets) -> None:
        self.policy = policy
        self.budgets = budgets

    def validate(
        self,
        unified_diff: str,
        *,
        check_applies: bool = True,
    ) -> PatchValidationResult:
        encoded = _validate_patch_text(unified_diff, self.budgets)
        parsed = _parse_unified_diff(unified_diff)
        if len(parsed.files_changed) > self.budgets.max_changed_files:
            raise PatchValidationError("Patch exceeds the changed-file-count limit")

        for path in parsed.files_changed:
            self._validate_changed_path(path)

        result = PatchValidationResult(
            files_changed=parsed.files_changed,
            lines_added=parsed.lines_added,
            lines_removed=parsed.lines_removed,
            patch_bytes=len(encoded),
            patch_lines=len(unified_diff.splitlines()),
            patch_sha256=hashlib.sha256(encoded).hexdigest(),
        )
        if check_applies:
            _check_patch_applies(unified_diff, self.policy.root)
        return result

    def _validate_changed_path(self, path: str) -> None:
        if _looks_like_hidden_evaluator_path(path):
            raise PatchValidationError("Hidden evaluator artifacts cannot be modified")
        if _looks_like_test_path(path) and not any(
            _matches_policy_entry(path, entry) for entry in self.policy.allowed_paths
        ):
            raise PatchValidationError("Test files may be modified only when explicitly allowed")
        try:
            resolved = self.policy.resolve(path, access="write", must_exist=False)
        except PathPolicyError as error:
            raise PatchValidationError(str(error)) from error
        if not _is_within(resolved, self.policy.root):
            raise PatchValidationError("Patch path escapes the disposable repository")


def apply_validated_patch(
    unified_diff: str,
    validation: PatchValidationResult,
    repository_root: str | Path,
) -> None:
    """Apply the exact already-validated patch from the trusted orchestrator.

    This function is deliberately not exposed through ``ConstrainedRepositoryTools``.
    It binds the supplied text to the validation hash and repeats ``git apply
    --check`` immediately before changing the disposable worktree.
    """

    try:
        encoded = unified_diff.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PatchValidationError("Patch must be valid UTF-8 text") from error
    if hashlib.sha256(encoded).hexdigest() != validation.patch_sha256:
        raise PatchValidationError("Patch content does not match its validation record")
    root = Path(repository_root).resolve(strict=True)
    if not root.is_dir():
        raise PatchValidationError("Patch target must be a repository directory")
    _check_patch_applies(unified_diff, root)
    patch_path = _write_temporary_patch(encoded)
    try:
        run_git(["apply", "--", str(patch_path)], cwd=root)
    except GitError as error:
        raise PatchValidationError("Validated patch could not be applied") from error
    finally:
        patch_path.unlink(missing_ok=True)
        patch_path.parent.rmdir()


class _ParsedPatch:
    def __init__(self, files_changed: list[str], lines_added: int, lines_removed: int) -> None:
        self.files_changed = files_changed
        self.lines_added = lines_added
        self.lines_removed = lines_removed


def _validate_patch_text(unified_diff: str, budgets: AgentBudgets) -> bytes:
    if not isinstance(unified_diff, str) or not unified_diff:
        raise PatchValidationError("Patch must be non-empty text")
    if "\x00" in unified_diff or "\r" in unified_diff:
        raise PatchValidationError("Patch must be NUL-free UTF-8 text with LF line endings")
    try:
        encoded = unified_diff.encode("utf-8")
    except UnicodeEncodeError as error:
        raise PatchValidationError("Patch must be valid UTF-8 text") from error
    if len(encoded) > budgets.max_patch_bytes:
        raise PatchValidationError("Patch exceeds the byte-size limit")
    if len(unified_diff.splitlines()) > budgets.max_patch_lines:
        raise PatchValidationError("Patch exceeds the line-count limit")
    return encoded


def _parse_unified_diff(unified_diff: str) -> _ParsedPatch:
    lines = unified_diff.splitlines()
    starts = [index for index, line in enumerate(lines) if line.startswith("diff --git ")]
    if not starts or starts[0] != 0:
        raise PatchValidationError("Patch must contain only git-style unified diff blocks")
    starts.append(len(lines))

    files: list[str] = []
    additions = 0
    removals = 0
    for position in range(len(starts) - 1):
        block = lines[starts[position] : starts[position + 1]]
        match = _DIFF_HEADER.fullmatch(block[0])
        if match is None:
            raise PatchValidationError("Diff headers must use unquoted a/path and b/path names")
        old_diff_path, new_diff_path = match.groups()
        if old_diff_path != new_diff_path:
            raise PatchValidationError("Rename and copy patches are not supported")
        if any(
            line.startswith(_PROHIBITED_METADATA) or " 120000" in line or " 160000" in line
            for line in block
        ):
            raise PatchValidationError(
                "Binary, rename, mode-only, and submodule patches are forbidden"
            )

        hunk_indexes = [index for index, line in enumerate(block) if line.startswith("@@ ")]
        if not hunk_indexes:
            raise PatchValidationError("Each changed file must contain a text hunk")
        first_hunk = hunk_indexes[0]
        old_headers = [line for line in block[1:first_hunk] if line.startswith("--- ")]
        new_headers = [line for line in block[1:first_hunk] if line.startswith("+++ ")]
        if len(old_headers) != 1 or len(new_headers) != 1:
            raise PatchValidationError("Each diff block requires one --- and one +++ file header")
        old_header = old_headers[0][4:]
        new_header = new_headers[0][4:]
        expected_old = f"a/{old_diff_path}"
        expected_new = f"b/{new_diff_path}"
        if old_header not in {expected_old, "/dev/null"}:
            raise PatchValidationError("Old file header does not match the diff path")
        if new_header not in {expected_new, "/dev/null"}:
            raise PatchValidationError("New file header does not match the diff path")
        if old_header == "/dev/null" and new_header == "/dev/null":
            raise PatchValidationError("A patch cannot have two null file headers")

        for index in hunk_indexes:
            if _HUNK_HEADER.fullmatch(block[index]) is None:
                raise PatchValidationError("Malformed unified-diff hunk header")
        for line in block[first_hunk + 1 :]:
            if line.startswith("@@ "):
                continue
            if line == r"\ No newline at end of file":
                continue
            if not line or line[0] not in {" ", "+", "-"}:
                raise PatchValidationError("Malformed unified-diff hunk body")
            if line.startswith("+"):
                additions += 1
            elif line.startswith("-"):
                removals += 1
        files.append(new_diff_path)

    if len(set(files)) != len(files):
        raise PatchValidationError("A patch cannot contain duplicate file blocks")
    return _ParsedPatch(files, additions, removals)


def _check_patch_applies(unified_diff: str, repository_root: Path) -> None:
    encoded = unified_diff.encode("utf-8")
    patch_path = _write_temporary_patch(encoded)
    try:
        run_git(["apply", "--check", "--", str(patch_path)], cwd=repository_root)
    except GitError as error:
        raise PatchValidationError(
            "Patch does not apply cleanly to the disposable workspace"
        ) from error
    finally:
        patch_path.unlink(missing_ok=True)
        patch_path.parent.rmdir()


def _write_temporary_patch(encoded: bytes) -> Path:
    directory = Path(tempfile.mkdtemp(prefix="agentrace-patch-"))
    patch_path = directory / "candidate.diff"
    patch_path.write_bytes(encoded)
    return patch_path


def _looks_like_hidden_evaluator_path(path: str) -> bool:
    segments = tuple(segment.casefold() for segment in PurePosixPath(path).parts)
    return any(
        segment in {"hidden_tests", "hidden-tests", ".agenttrace-evaluator"}
        or segment.startswith("hidden_test")
        for segment in segments
    )


def _looks_like_test_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    segments = tuple(segment.casefold() for segment in parts[:-1])
    filename = parts[-1].casefold()
    return (
        any(segment in {"test", "tests"} for segment in segments)
        or filename.startswith("test_")
        or filename.endswith("_test.py")
    )


def _matches_policy_entry(path: str, entry: str) -> bool:
    comparison_path = os.path.normcase(path)
    comparison_entry = os.path.normcase(entry)
    if comparison_entry.endswith("/"):
        prefix = comparison_entry[:-1]
        return comparison_path == prefix or comparison_path.startswith(f"{prefix}/")
    return comparison_path == comparison_entry


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False
