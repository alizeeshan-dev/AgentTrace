"""Constrained, non-shell repository inspection tools for Phase 5 agents."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import Field

from app.agent.budgets import BudgetExhausted, BudgetTracker
from app.repositories.path_policy import PathPolicyError, RepositoryPathPolicy, TreeEntry
from app.schemas.common import ResearchSchema

ToolName = Literal["list_tree", "read_file", "search_code"]


class ToolInputError(ValueError):
    """Raised when a structured tool call has invalid arguments."""


class ToolExecutionResult(ResearchSchema):
    """Bounded repository evidence safe to return to a model."""

    tool: ToolName
    content: str
    paths: list[str] = Field(default_factory=list)
    truncated: bool = False


class ListTreeArguments(ResearchSchema):
    path: str = "."


class ReadFileArguments(ResearchSchema):
    path: str


class SearchCodeArguments(ResearchSchema):
    query: str = Field(min_length=1, max_length=200)
    path: str = "."
    case_sensitive: bool = True


class ConstrainedRepositoryTools:
    """Execute the small repository-tool allowlist within a path policy."""

    def __init__(self, policy: RepositoryPathPolicy, tracker: BudgetTracker) -> None:
        self.policy = policy
        self.tracker = tracker

    def execute(self, name: str, arguments: Mapping[str, object]) -> ToolExecutionResult:
        """Validate a structured action and dispatch only an approved tool."""

        if name == "list_tree":
            list_arguments = ListTreeArguments.model_validate(arguments)
            return self.list_tree(list_arguments.path)
        if name == "read_file":
            read_arguments = ReadFileArguments.model_validate(arguments)
            return self.read_file(read_arguments.path)
        if name == "search_code":
            search_arguments = SearchCodeArguments.model_validate(arguments)
            return self.search_code(
                search_arguments.query,
                path=search_arguments.path,
                case_sensitive=search_arguments.case_sensitive,
            )
        self.tracker.begin_tool_call()
        raise ToolInputError(f"Unknown repository tool: {name}")

    def list_tree(self, path: str = ".") -> ToolExecutionResult:
        self.tracker.begin_tool_call()
        entries = self.policy.list_tree(path)
        selected: list[TreeEntry] = []
        selected_files: list[str] = []
        new_files: set[str] = set()
        truncated = False
        for entry in entries:
            if len(selected) >= self.tracker.limits.max_tree_entries:
                truncated = True
                break
            if entry.kind == "file" and not self.tracker.is_file_exposed(entry.path):
                if len(new_files) >= self.tracker.remaining_files_exposed:
                    truncated = True
                    break
                new_files.add(entry.path)
            if entry.kind == "file":
                selected_files.append(entry.path)
            selected.append(entry)

        rendered = "\n".join(_render_tree_entry(entry) for entry in selected)
        rendered, content_truncated = _fit_content(
            rendered,
            min(
                self.tracker.remaining_content_characters,
                self.tracker.limits.max_search_result_characters,
            ),
        )
        if content_truncated:
            complete_lines = rendered.splitlines()
            emitted_paths = {line.split("\t", 1)[0] for line in complete_lines if "\t" in line}
            selected_files = [item for item in selected_files if item in emitted_paths]
        self.tracker.record_exposure(rendered, paths=selected_files)
        return ToolExecutionResult(
            tool="list_tree",
            content=rendered,
            paths=selected_files,
            truncated=truncated or content_truncated,
        )

    def read_file(self, path: str) -> ToolExecutionResult:
        self.tracker.begin_tool_call()
        content = self.policy.read_text(path)
        canonical_path = self.policy.resolve(path).relative_to(self.policy.root).as_posix()
        if (
            not self.tracker.is_file_exposed(canonical_path)
            and self.tracker.remaining_files_exposed < 1
        ):
            raise BudgetExhausted("max_files_exposed")
        if not self.tracker.is_file_read(canonical_path) and self.tracker.remaining_files_read < 1:
            raise BudgetExhausted("max_files_read")
        content, truncated = _fit_content(content, self.tracker.remaining_content_characters)
        self.tracker.record_exposure(
            content,
            paths=(canonical_path,),
            files_read=(canonical_path,),
        )
        return ToolExecutionResult(
            tool="read_file",
            content=content,
            paths=[canonical_path],
            truncated=truncated,
        )

    def search_code(
        self,
        query: str,
        *,
        path: str = ".",
        case_sensitive: bool = True,
    ) -> ToolExecutionResult:
        self.tracker.begin_tool_call()
        if not query or len(query) > 200 or any(character in query for character in "\x00\r\n"):
            raise ToolInputError(
                "Search query must be one non-empty line of at most 200 characters"
            )

        candidates = self._search_candidates(path)
        needle = query if case_sensitive else query.casefold()
        output: list[str] = []
        output_length = 0
        matched_paths: list[str] = []
        scanned_paths: list[str] = []
        newly_exposed_paths: set[str] = set()
        max_characters = min(
            self.tracker.limits.max_search_result_characters,
            self.tracker.remaining_content_characters,
        )
        truncated = False
        match_count = 0
        new_scan_count = 0
        for candidate in candidates:
            if (
                not self.tracker.is_file_read(candidate)
                and candidate not in scanned_paths
                and new_scan_count >= self.tracker.remaining_files_read
            ):
                truncated = True
                break
            try:
                text = self.policy.read_text(candidate)
            except PathPolicyError:
                continue
            if candidate not in scanned_paths:
                scanned_paths.append(candidate)
                if not self.tracker.is_file_read(candidate):
                    new_scan_count += 1
            for line_number, line in enumerate(text.splitlines(), start=1):
                haystack = line if case_sensitive else line.casefold()
                if needle not in haystack:
                    continue
                new_file = (
                    not self.tracker.is_file_exposed(candidate)
                    and candidate not in newly_exposed_paths
                )
                if new_file and len(newly_exposed_paths) >= self.tracker.remaining_files_exposed:
                    truncated = True
                    break
                rendered = f"{candidate}:{line_number}:{line}"
                separator = 1 if output else 0
                if (
                    match_count >= self.tracker.limits.max_search_matches
                    or output_length + separator + len(rendered) > max_characters
                ):
                    truncated = True
                    break
                output.append(rendered)
                output_length += separator + len(rendered)
                match_count += 1
                if candidate not in matched_paths:
                    matched_paths.append(candidate)
                if new_file:
                    newly_exposed_paths.add(candidate)
            if truncated:
                break

        content = "\n".join(output)
        self.tracker.record_exposure(
            content,
            paths=matched_paths,
            files_read=scanned_paths,
        )
        return ToolExecutionResult(
            tool="search_code",
            content=content,
            paths=matched_paths,
            truncated=truncated,
        )

    def _search_candidates(self, path: str) -> list[str]:
        resolved = self.policy.resolve(path, allow_root=True)
        if resolved.is_file():
            return [resolved.relative_to(self.policy.root).as_posix()]
        entries = self.policy.list_tree(path)
        return sorted(entry.path for entry in entries if entry.kind == "file")


def _render_tree_entry(entry: TreeEntry) -> str:
    size = "" if entry.size is None else str(entry.size)
    return f"{entry.path}\t{entry.kind}\t{size}"


def _fit_content(content: str, limit: int) -> tuple[str, bool]:
    if len(content) <= limit:
        return content, False
    if limit <= 0:
        raise BudgetExhausted("max_content_characters")
    # Avoid returning a partial source/listing line: it is hard for a model to
    # distinguish truncation from actual repository content.
    prefix = content[:limit]
    complete, separator, _ = prefix.rpartition("\n")
    if separator:
        return complete, True
    return prefix, True
