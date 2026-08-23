"""Metadata-only ingestion of public HTTPS Git repositories."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import shutil
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import SplitResult, urlsplit, urlunsplit
from uuid import uuid4

from app.filesystem import validate_runtime_root

from .git import GitError, run_git

_FULL_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_MAX_TREE_ENTRIES = 50_000
_MAX_METADATA_BYTES = 256 * 1024


class ExternalRepositoryError(ValueError):
    """Controlled registration failure safe to return through the local API."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ExternalRepositoryRegistration:
    identity: str
    name: str
    repository_url: str
    source_path: Path
    base_commit: str
    default_branch: str | None
    python_version: str | None
    test_command: str | None
    primary_language: str
    metadata: dict[str, object]


def register_external_repository(
    repository_url: str,
    *,
    managed_root: str | Path,
    test_command: str | None = None,
) -> ExternalRepositoryRegistration:
    """Clone a public repository as bare Git data and inspect declarative metadata.

    Bare ingestion avoids checking out repository-controlled files before the
    user grants local-execution trust. No project code, test, hook, or setup
    script is invoked here.
    """

    normalized_url = validate_external_git_url(repository_url)
    explicit_command = _validated_test_command(test_command)
    root = validate_runtime_root(Path(managed_root), field_name="external_repository_root")
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve(strict=True)

    staging = root / f".ingest-{uuid4().hex}"
    final_parent: Path | None = None
    created_final = False
    try:
        remote_commit = _remote_head(normalized_url, root)
        identity = _repository_identity(normalized_url, remote_commit)
        final_parent = root / identity
        final_source = final_parent / "source.git"
        if final_source.exists():
            if _commit(final_source) != remote_commit:
                raise ExternalRepositoryError(
                    "registration_conflict",
                    "Managed repository identity conflicts with a different commit.",
                )
            commit = remote_commit
        else:
            try:
                run_git(
                    ["clone", "--bare", "--no-tags", "--", normalized_url, str(staging)],
                    cwd=root,
                    timeout_seconds=300,
                    max_output_chars=200_000,
                )
            except GitError as error:
                raise ExternalRepositoryError(
                    "clone_failed",
                    "The public HTTPS Git repository could not be cloned.",
                ) from error
            commit = _commit(staging)
            # HEAD may move between ls-remote and clone. Bind storage to what
            # was actually cloned, never to a guessed or abbreviated revision.
            identity = _repository_identity(normalized_url, commit)
            final_parent = root / identity
            final_source = final_parent / "source.git"
            if final_source.exists():
                if _commit(final_source) != commit:
                    raise ExternalRepositoryError(
                        "registration_conflict",
                        "Managed repository identity conflicts with a different commit.",
                    )
                _remove_direct_child(staging, root)
            else:
                if final_parent.exists():
                    raise ExternalRepositoryError(
                        "registration_conflict",
                        "Managed repository destination is already occupied.",
                    )
                final_parent.mkdir()
                staging.replace(final_source)
                created_final = True
        tree = _tree_entries(final_source, commit)
        detected = _detect_python_project(final_source, commit, tree)
        if detected is None:
            raise ExternalRepositoryError(
                "unsupported_repository",
                "The repository does not appear to be a supported Python project.",
            )
        discovered_command = explicit_command or _discover_test_command(
            final_source, commit, tree
        )
        metadata = {
            **detected,
            "test_command_source": (
                "explicit"
                if explicit_command is not None
                else "detected"
                if discovered_command is not None
                else "unavailable"
            ),
            "verification_configured": discovered_command is not None,
        }
        return ExternalRepositoryRegistration(
            identity=identity,
            name=_repository_name(normalized_url),
            repository_url=normalized_url,
            source_path=final_source.resolve(strict=True),
            base_commit=commit,
            default_branch=_default_branch(final_source),
            python_version=_python_version(final_source, commit, tree),
            test_command=discovered_command,
            primary_language="Python",
            metadata=metadata,
        )
    except Exception:
        if staging.exists():
            _remove_direct_child(staging, root)
        if created_final and final_parent is not None and final_parent.exists():
            _remove_direct_child(final_parent, root)
        raise


def _remote_head(repository_url: str, root: Path) -> str:
    try:
        output = run_git(
            ["ls-remote", "--exit-code", "--", repository_url, "HEAD"],
            cwd=root,
            timeout_seconds=120,
            max_output_chars=10_000,
        )
    except GitError as error:
        raise ExternalRepositoryError(
            "clone_failed", "The public HTTPS Git repository could not be resolved."
        ) from error
    first = output.splitlines()[0].split(maxsplit=1)[0].casefold() if output else ""
    if _FULL_COMMIT.fullmatch(first) is None:
        raise ExternalRepositoryError(
            "invalid_git_repository", "The repository has no full public HEAD commit."
        )
    return first


def validate_external_git_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 2_000:
        raise ExternalRepositoryError("invalid_git_url", "Repository URL is invalid.")
    try:
        parsed = urlsplit(value.strip())
    except ValueError as error:
        raise ExternalRepositoryError("invalid_git_url", "Repository URL is invalid.") from error
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or not parsed.path
        or parsed.path == "/"
        or any(character in value for character in "\x00\r\n")
    ):
        raise ExternalRepositoryError(
            "invalid_git_url",
            "Use a public HTTPS Git URL without credentials, query parameters, or fragments.",
        )
    hostname = parsed.hostname.casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise ExternalRepositoryError("invalid_git_url", "Local network Git URLs are not allowed.")
    try:
        address = ipaddress.ip_address(hostname.strip("[]"))
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ExternalRepositoryError(
            "invalid_git_url", "Private network Git URLs are not allowed."
        )
    netloc = hostname if parsed.port is None else f"{hostname}:{parsed.port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(SplitResult("https", netloc, path, "", ""))


def _validated_test_command(command: str | None) -> str | None:
    if command is None or not command.strip():
        return None
    normalized = command.strip()
    # Lazy import avoids making the low-level repositories package depend on
    # verification-service initialization.
    from app.verification.gates import StandardGateFactory

    try:
        StandardGateFactory.visible_tests(normalized, timeout_seconds=60)
    except ValueError as error:
        raise ExternalRepositoryError(
            "invalid_test_command",
            "External verification commands must be non-shell pytest commands.",
        ) from error
    return normalized


def _commit(repository: Path) -> str:
    try:
        commit = run_git(
            ["rev-parse", "--verify", "--end-of-options", "HEAD^{commit}"],
            cwd=repository,
        ).casefold()
    except GitError as error:
        raise ExternalRepositoryError(
            "invalid_git_repository", "The cloned repository has no readable HEAD commit."
        ) from error
    if _FULL_COMMIT.fullmatch(commit) is None:
        raise ExternalRepositoryError(
            "invalid_git_repository", "Git did not resolve a full commit SHA."
        )
    return commit


def _tree_entries(repository: Path, commit: str) -> tuple[str, ...]:
    try:
        output = run_git(
            ["ls-tree", "-r", "--name-only", commit],
            cwd=repository,
            timeout_seconds=120,
            max_output_chars=5_000_000,
        )
    except GitError as error:
        raise ExternalRepositoryError(
            "repository_inspection_failed",
            "Repository metadata could not be inspected safely.",
        ) from error
    entries = tuple(line for line in output.splitlines() if line)
    if len(entries) > _MAX_TREE_ENTRIES:
        raise ExternalRepositoryError(
            "unsupported_repository", "Repository contains too many files for this workflow."
        )
    return entries


def _detect_python_project(
    repository: Path, commit: str, tree: tuple[str, ...]
) -> dict[str, object] | None:
    names = set(tree)
    python_files = [path for path in tree if path.casefold().endswith(".py")]
    indicators = {
        "pyproject_toml": "pyproject.toml" in names,
        "requirements_txt": "requirements.txt" in names,
        "setup_py": "setup.py" in names,
        "setup_cfg": "setup.cfg" in names,
        "pytest_ini": "pytest.ini" in names,
        "tox_ini": "tox.ini" in names,
        "tests_directory": any(path.startswith(("tests/", "test/")) for path in tree),
    }
    if not python_files and not any(indicators.values()):
        return None
    top_level_sources = sorted(
        {
            path.split("/", 1)[0]
            for path in python_files
            if not path.startswith(("tests/", "test/", "."))
        }
    )[:30]
    return {
        "file_count": len(tree),
        "python_file_count": len(python_files),
        "python_project_indicators": indicators,
        "suggested_allowed_paths": top_level_sources,
    }


def _discover_test_command(
    repository: Path, commit: str, tree: tuple[str, ...]
) -> str | None:
    names = set(tree)
    if "pytest.ini" in names or any(path.startswith(("tests/", "test/")) for path in tree):
        return "python -m pytest -q"
    recognized = {
        "pyproject.toml": "[tool.pytest",
        "setup.cfg": "[tool:pytest]",
        "tox.ini": "pytest",
    }
    for path, marker in recognized.items():
        if path in names and marker in (_git_text(repository, commit, path) or "").casefold():
            return "python -m pytest -q"
    return None


def _python_version(repository: Path, commit: str, tree: tuple[str, ...]) -> str | None:
    if ".python-version" in tree:
        raw = _git_text(repository, commit, ".python-version")
        if raw:
            value = raw.splitlines()[0].strip()
            if 0 < len(value) <= 50:
                return value
    if "pyproject.toml" not in tree:
        return None
    raw = _git_text(repository, commit, "pyproject.toml")
    if raw is None:
        return None
    try:
        project = tomllib.loads(raw).get("project", {})
    except tomllib.TOMLDecodeError:
        return None
    requires_python = project.get("requires-python") if isinstance(project, dict) else None
    return (
        requires_python
        if isinstance(requires_python, str) and 0 < len(requires_python) <= 50
        else None
    )


def _git_text(repository: Path, commit: str, path: str) -> str | None:
    try:
        value = run_git(
            ["show", f"{commit}:{path}"],
            cwd=repository,
            max_output_chars=_MAX_METADATA_BYTES,
        )
    except GitError:
        return None
    return value


def _default_branch(repository: Path) -> str | None:
    try:
        branch = run_git(["symbolic-ref", "--short", "HEAD"], cwd=repository)
    except GitError:
        return None
    return branch if branch and len(branch) <= 300 else None


def _repository_identity(url: str, commit: str) -> str:
    digest = hashlib.sha256(f"{url}\0{commit}".encode()).hexdigest()
    return f"external-{digest}"


def _repository_name(url: str) -> str:
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    if name.casefold().endswith(".git"):
        name = name[:-4]
    return name[:200] or "external-repository"


def _remove_direct_child(path: Path, root: Path) -> None:
    if not path.exists():
        return
    if path.parent.resolve(strict=True) != root or path.is_symlink():
        raise ExternalRepositoryError(
            "repository_cleanup_failed", "Managed repository cleanup boundary was invalid."
        )
    shutil.rmtree(path)
