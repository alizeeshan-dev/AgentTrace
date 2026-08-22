"""Provider-independent token pricing used for research cost estimates."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TokenPricing:
    """A frozen USD pricing snapshot, expressed per million tokens."""

    input_per_million_tokens: float | None = None
    output_per_million_tokens: float | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        for value in (self.input_per_million_tokens, self.output_per_million_tokens):
            if value is not None and value < 0:
                raise ValueError("token pricing cannot be negative")
        if (self.input_per_million_tokens is None) != (
            self.output_per_million_tokens is None
        ):
            raise ValueError("input and output token pricing must be configured together")

    def estimate(self, *, input_tokens: int, output_tokens: int) -> float | None:
        """Return a deterministic estimate, or None when pricing is not frozen."""

        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.input_per_million_tokens is None or self.output_per_million_tokens is None:
            return None
        return (
            input_tokens * self.input_per_million_tokens
            + output_tokens * self.output_per_million_tokens
        ) / 1_000_000
