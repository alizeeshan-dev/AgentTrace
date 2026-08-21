"""Small, bounded JUnit reader used for baseline regression comparisons."""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class TestInventory:
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    skipped: tuple[str, ...]
    evidence_valid: bool = True
    evidence_error: str | None = None

    @property
    def all_tests(self) -> frozenset[str]:
        return frozenset((*self.passed, *self.failed, *self.skipped))


def read_junit(
    path: Path, *, private_ids: bool = False, max_bytes: int = 2_000_000
) -> TestInventory:
    """Read a verifier-created JUnit file without retaining messages or source."""

    if _is_link_like(path) or not path.is_file():
        return TestInventory((), (), (), False, "missing_or_non_regular")
    try:
        with path.open("rb") as stream:
            data = stream.read(max_bytes + 1)
    except OSError:
        return TestInventory((), (), (), False, "unreadable")
    if len(data) > max_bytes:
        return TestInventory((), (), (), False, "oversized")
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return TestInventory((), (), (), False, "unsafe_xml")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return TestInventory((), (), (), False, "invalid_xml")
    passed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    for case in root.iter("testcase"):
        raw = f"{case.attrib.get('classname', '')}::{case.attrib.get('name', '')}".strip(":")
        test_id = hashlib.sha256(raw.encode()).hexdigest() if private_ids else raw[:500]
        if case.find("failure") is not None or case.find("error") is not None:
            failed.append(test_id)
        elif case.find("skipped") is not None:
            skipped.append(test_id)
        else:
            passed.append(test_id)
    return TestInventory(tuple(sorted(passed)), tuple(sorted(failed)), tuple(sorted(skipped)))


def compare_inventories(baseline: TestInventory, candidate: TestInventory) -> dict[str, object]:
    """Return the stable test-level delta used to identify regressions."""

    baseline_passing = set(baseline.passed)
    candidate_failing = set(candidate.failed)
    baseline_failing = set(baseline.failed)
    difference: dict[str, object] = {
        "new_failures": sorted(baseline_passing & candidate_failing),
        "fixed_failures": sorted(baseline_failing - candidate_failing),
        "remaining_failures": sorted(baseline_failing & candidate_failing),
    }
    missing = sorted(baseline.all_tests - candidate.all_tests)
    if missing:
        difference["missing_tests"] = missing
    if not candidate.evidence_valid:
        difference["result_evidence"] = candidate.evidence_error or "invalid"
    return difference


def _is_link_like(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())
