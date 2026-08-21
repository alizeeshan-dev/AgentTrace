"""Build a deterministic test-by-line execution spectrum."""

from __future__ import annotations

import ast
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, order=True)
class SourceLocation:
    """A repository-relative source line identity.

    Path authorization belongs to the repository-safety layer.  Normalizing
    separators here only makes equivalent collector output rank consistently
    across supported operating systems.
    """

    file: str
    line: int

    def __post_init__(self) -> None:
        normalized_file = self.file.replace("\\", "/")
        if not normalized_file:
            raise ValueError("source file cannot be empty")
        if "\n" in normalized_file or "\r" in normalized_file:
            raise ValueError("source file cannot contain a newline")
        if isinstance(self.line, bool) or not isinstance(self.line, int):
            raise TypeError("source line must be an integer")
        if self.line < 1:
            raise ValueError("source line must be positive")
        object.__setattr__(self, "file", normalized_file)


@dataclass(frozen=True)
class TestExecution:
    """The outcome and executed production lines for one collected test."""

    __test__: ClassVar[bool] = False

    test_id: str
    passed: bool
    executed_lines: frozenset[SourceLocation]

    def __post_init__(self) -> None:
        if not self.test_id:
            raise ValueError("test_id cannot be empty")
        if not isinstance(self.passed, bool):
            raise TypeError("passed must be a boolean")
        object.__setattr__(self, "executed_lines", frozenset(self.executed_lines))


@dataclass(frozen=True)
class SpectrumLine:
    """Execution counts for a single source line."""

    location: SourceLocation
    ef: int
    nf: int
    ep: int
    symbol: str | None = None

    def __post_init__(self) -> None:
        for name in ("ef", "nf", "ep"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} cannot be negative")


def build_line_spectrum(
    executions: Iterable[TestExecution],
    *,
    relevant_lines: Iterable[SourceLocation] = (),
    symbols: Mapping[SourceLocation, str] | None = None,
) -> tuple[SpectrumLine, ...]:
    """Construct ``ef``, ``nf``, and ``ep`` counts for relevant source lines.

    Relevant lines are the union of the explicitly supplied lines and all
    lines observed in test execution.  Duplicate test identifiers are rejected
    because counting the same logical test twice would distort suspiciousness.
    Output is sorted by file and line for reproducible persistence.
    """

    collected = tuple(executions)
    test_ids = [execution.test_id for execution in collected]
    if len(test_ids) != len(set(test_ids)):
        raise ValueError("test execution identifiers must be unique")

    all_lines = set(relevant_lines)
    for execution in collected:
        all_lines.update(execution.executed_lines)

    total_failing = sum(not execution.passed for execution in collected)
    symbol_map = symbols or {}
    spectrum: list[SpectrumLine] = []
    for location in sorted(all_lines):
        ef = sum(
            not execution.passed and location in execution.executed_lines
            for execution in collected
        )
        ep = sum(
            execution.passed and location in execution.executed_lines
            for execution in collected
        )
        spectrum.append(
            SpectrumLine(
                location=location,
                ef=ef,
                nf=total_failing - ef,
                ep=ep,
                symbol=symbol_map.get(location),
            )
        )
    return tuple(spectrum)


@dataclass(frozen=True)
class _SymbolRange:
    name: str
    start: int
    end: int
    depth: int


class _SymbolCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self._names: list[str] = []
        self.ranges: list[_SymbolRange] = []

    def _visit_symbol(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        decorator_lines = [decorator.lineno for decorator in node.decorator_list]
        start = min([node.lineno, *decorator_lines])
        end = node.end_lineno or node.lineno
        qualified_name = ".".join([*self._names, node.name])
        self.ranges.append(
            _SymbolRange(
                name=qualified_name,
                start=start,
                end=end,
                depth=len(self._names) + 1,
            )
        )
        self._names.append(node.name)
        self.generic_visit(node)
        self._names.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_symbol(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_symbol(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_symbol(node)


def resolve_symbols(source: str, line_numbers: Collection[int]) -> dict[int, str | None]:
    """Resolve each line to its innermost enclosing class or function symbol.

    Lines outside a named class/function map to ``None``.  Invalid Python is
    reported as ``SyntaxError`` to avoid silently persisting misleading symbol
    evidence.
    """

    tree = ast.parse(source)
    collector = _SymbolCollector()
    collector.visit(tree)

    resolved: dict[int, str | None] = {}
    for line in line_numbers:
        if isinstance(line, bool) or not isinstance(line, int):
            raise TypeError("line numbers must be integers")
        if line < 1:
            raise ValueError("line numbers must be positive")
        candidates = [item for item in collector.ranges if item.start <= line <= item.end]
        candidates.sort(
            key=lambda item: (-item.depth, item.end - item.start, item.start, item.name)
        )
        resolved[line] = candidates[0].name if candidates else None
    return resolved
