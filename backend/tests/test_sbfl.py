from __future__ import annotations

from math import sqrt

import pytest

from app.fault_localization.ochiai import ochiai
from app.fault_localization.ranking import rank_of_location, rank_spectrum
from app.fault_localization.spectrum import (
    SourceLocation,
    SpectrumLine,
    TestExecution,
    build_line_spectrum,
    resolve_symbols,
)


def test_ochiai_matches_hand_calculated_case() -> None:
    # 2 / sqrt((2 + 1) * (2 + 2)) = 1 / sqrt(3)
    assert ochiai(ef=2, nf=1, ep=2) == pytest.approx(1 / sqrt(3))


def test_spectrum_ranking_uses_deterministic_tie_policy_and_top_k() -> None:
    first = SourceLocation("src/logic.py", 3)
    second = SourceLocation("src/logic.py", 8)
    executions = (
        TestExecution("tests/test_logic.py::test_bad", False, frozenset({first, second})),
        TestExecution("tests/test_logic.py::test_good", True, frozenset({first, second})),
    )

    spectrum = build_line_spectrum(executions)
    ranking = rank_spectrum(spectrum, top_k=1)

    assert [(line.ef, line.nf, line.ep) for line in spectrum] == [(1, 0, 1), (1, 0, 1)]
    assert len(ranking) == 1
    assert ranking[0].location == first
    assert ranking[0].score == pytest.approx(1 / sqrt(2))
    assert rank_of_location(ranking, first) == 1
    assert rank_of_location(ranking, second) is None


def test_ochiai_is_zero_without_failing_test_evidence() -> None:
    assert ochiai(ef=0, nf=0, ep=4) == 0.0
    untouched = SpectrumLine(SourceLocation("src/logic.py", 12), ef=0, nf=0, ep=0)
    assert rank_spectrum([untouched])[0].score == 0.0


def test_symbol_resolution_selects_innermost_enclosing_symbol() -> None:
    source = """\
class Parser:
    def parse(self, value: str) -> str:
        def clean() -> str:
            return value.strip()
        return clean()

answer = 42
"""

    assert resolve_symbols(source, {1, 2, 4, 7}) == {
        1: "Parser",
        2: "Parser.parse",
        4: "Parser.parse.clean",
        7: None,
    }
