"""Rank SBFL locations as reproducible fault-localization evidence."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.fault_localization.ochiai import ochiai
from app.fault_localization.spectrum import SourceLocation, SpectrumLine

TIE_BREAK_POLICY = "score descending, then file ascending, then line ascending, then symbol"


@dataclass(frozen=True)
class RankedLocation:
    """One ordinally ranked line of SBFL evidence, not a fault assertion."""

    rank: int
    location: SourceLocation
    symbol: str | None
    score: float
    ef: int
    nf: int
    ep: int


def rank_spectrum(
    spectrum: Iterable[SpectrumLine],
    *,
    top_k: int | None = None,
) -> tuple[RankedLocation, ...]:
    """Rank spectrum lines by Ochiai with a stable, documented tie policy.

    Equal scores are ordered lexicographically by normalized file, then line,
    then symbol.  Ranks are ordinal positions after that deterministic tie
    break.  ``top_k=None`` returns every ranked location.
    """

    if top_k is not None:
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise TypeError("top_k must be an integer or None")
        if top_k < 1:
            raise ValueError("top_k must be positive")

    scored = [
        (
            line,
            ochiai(ef=line.ef, nf=line.nf, ep=line.ep),
        )
        for line in spectrum
    ]
    scored.sort(
        key=lambda item: (
            -item[1],
            item[0].location.file,
            item[0].location.line,
            item[0].symbol or "",
        )
    )
    if top_k is not None:
        scored = scored[:top_k]

    return tuple(
        RankedLocation(
            rank=index,
            location=line.location,
            symbol=line.symbol,
            score=score,
            ef=line.ef,
            nf=line.nf,
            ep=line.ep,
        )
        for index, (line, score) in enumerate(scored, start=1)
    )


def rank_of_location(
    ranking: Iterable[RankedLocation],
    location: SourceLocation,
) -> int | None:
    """Return a known location's ordinal evidence rank, when present."""

    return next((item.rank for item in ranking if item.location == location), None)
