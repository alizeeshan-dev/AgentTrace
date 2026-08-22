"""Capture the frozen native Windows verification environment exactly once."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from .environment import (
    build_windows_environment_manifest,
    write_windows_environment_manifest,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Freeze the AgentTrace native Windows verification environment"
    )
    parser.add_argument("--repository-root", type=Path, default=Path("."))
    parser.add_argument(
        "--dependency-lock",
        type=Path,
        default=Path("constraints/main-experiment.txt"),
    )
    parser.add_argument("--benchmark-version", required=True)
    parser.add_argument("--verification-profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)

    root = arguments.repository_root.resolve(strict=True)
    lock = arguments.dependency_lock
    if not lock.is_absolute():
        lock = root / lock
    manifest = build_windows_environment_manifest(
        repository_root=root,
        dependency_lock=lock,
        benchmark_version=arguments.benchmark_version,
        verification_profile=arguments.verification_profile,
    )
    write_windows_environment_manifest(manifest, arguments.output)
    print(manifest.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
