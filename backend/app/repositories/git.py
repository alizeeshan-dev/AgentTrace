"""Small, non-shelling wrapper around the Git executable."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Sequence
from pathlib import Path


class GitError(RuntimeError):
    """Raised when Git cannot complete a repository operation."""


def run_git(
    arguments: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: int = 30,
) -> str:
    """Run Git with a fixed argv boundary and return stripped stdout.

    Callers supply individual arguments.  No shell is involved, so repository
    paths and refs cannot be interpreted as shell syntax.
    """

    if not arguments or any(not isinstance(argument, str) for argument in arguments):
        raise ValueError("Git arguments must be a non-empty sequence of strings")
    try:
        environment = os.environ.copy()
        environment.update(
            {
                "GCM_INTERACTIVE": "never",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_TERMINAL_PROMPT": "0",
            }
        )
        completed = subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=",
                "-c",
                "credential.helper=",
                "-c",
                "core.askPass=",
                *arguments,
            ],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            check=False,
            shell=False,
            env=environment,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise GitError(f"Git invocation failed: {error}") from error

    if completed.returncode != 0:
        detail = completed.stderr.strip() or "unknown Git error"
        raise GitError(f"git {arguments[0]} failed: {detail}")
    return completed.stdout.strip()
