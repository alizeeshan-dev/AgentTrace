"""Spectrum-based fault-localization services and research invariants."""

from .coverage import (
    CoverageCollectionError,
    CoveredTest,
    ExecutedLine,
    PerTestCoverage,
    PerTestCoverageCollector,
)
from .ochiai import ochiai
from .ranking import RankedLocation, rank_of_location, rank_spectrum
from .service import (
    FaultLocalizationError,
    FaultLocalizationRun,
    FaultLocalizationService,
    PilotLocalizationMetrics,
    format_fault_localization_evidence,
    localization_run_id,
)
from .spectrum import SourceLocation, SpectrumLine, TestExecution, build_line_spectrum

__all__ = [
    "CoverageCollectionError",
    "CoveredTest",
    "ExecutedLine",
    "FaultLocalizationError",
    "FaultLocalizationRun",
    "FaultLocalizationService",
    "PerTestCoverage",
    "PerTestCoverageCollector",
    "PilotLocalizationMetrics",
    "RankedLocation",
    "SourceLocation",
    "SpectrumLine",
    "TestExecution",
    "build_line_spectrum",
    "format_fault_localization_evidence",
    "localization_run_id",
    "ochiai",
    "rank_of_location",
    "rank_spectrum",
]
