from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.agent.provider import ModelMessage, ModelRequest
from app.configurations.models import ModelConfiguration
from app.repositories.path_policy import PathPolicyError, RepositoryPathPolicy
from app.traces.redaction import TraceRedactor
from app.verification.junit import TestInventory as JUnitInventory
from app.verification.junit import read_junit
from app.verification.service import NormalizedGate, VerificationService


def test_live_dotenv_files_are_never_agent_readable_or_patchable(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    (root / ".env").write_text("API_KEY=private\n", encoding="utf-8", newline="\n")
    (root / ".env.production").write_text(
        "TOKEN=private\n", encoding="utf-8", newline="\n"
    )
    (root / ".env.example").write_text("API_KEY=\n", encoding="utf-8", newline="\n")
    policy = RepositoryPathPolicy(root, allowed_paths=(".env", ".env.production"))

    with pytest.raises(PathPolicyError, match="secret files"):
        policy.read_text(".env")
    with pytest.raises(PathPolicyError, match="secret files"):
        policy.resolve(".env.production", access="write")
    assert policy.read_text(".env.example") == "API_KEY=\n"
    assert {entry.path for entry in policy.list_tree()} == {".env.example"}


def test_redactor_handles_camel_case_fields_and_common_credential_shapes() -> None:
    redactor = TraceRedactor()
    value = redactor.redact(
        {
            "clientSecret": "do-not-store",
            "nested": {"openaiApiKey": "do-not-store"},
            "max_tokens": 200,
            "log": "key ghp_abcdefghijklmnopqrstuvwxyz1234567890 and AKIAABCDEFGHIJKLMNOP",
        }
    )

    assert isinstance(value, dict)
    nested = value["nested"]
    log = value["log"]
    assert isinstance(nested, dict)
    assert isinstance(log, str)
    assert value["clientSecret"] == "[REDACTED:SECRET]"
    assert nested["openaiApiKey"] == "[REDACTED:SECRET]"
    assert value["max_tokens"] == 200
    assert "ghp_" not in log
    assert "AKIA" not in log


def test_credentials_cannot_enter_provider_or_frozen_model_parameters() -> None:
    message = ModelMessage(role="user", content="task")

    with pytest.raises(ValidationError, match="credential fields"):
        ModelRequest(
            model_identifier="fixture",
            model_parameters={"headers": {"authorizationHeader": "Bearer secret"}},
            messages=[message],
            timeout_seconds=1,
        )
    with pytest.raises(ValidationError, match="cannot contain credentials"):
        ModelConfiguration(
            provider="fake",
            model="fixture",
            model_version="v1",
            temperature=0,
            parameters={"clientSecret": "secret"},
        )


def test_candidate_cannot_pass_without_complete_junit_evidence(tmp_path: Path) -> None:
    missing = read_junit(tmp_path / "absent.xml")
    baseline = JUnitInventory(("tests::test_behavior",), (), ())
    passed_process = NormalizedGate(
        "visible_tests",
        True,
        "passed",
        0,
        1,
        "process exited successfully",
        {"passed": 0, "failed": 0, "skipped": 0},
    )

    normalized = VerificationService._require_candidate_test_evidence(
        passed_process, missing, baseline
    )

    assert normalized.status == "failed"
    assert normalized.baseline_difference is not None
    assert normalized.baseline_difference["result_evidence"] == "missing_or_non_regular"
    assert normalized.baseline_difference["missing_tests"] == ["tests::test_behavior"]


def test_junit_reader_bounds_before_parsing(tmp_path: Path) -> None:
    oversized = tmp_path / "results.xml"
    oversized.write_bytes(b"x" * 65)

    inventory = read_junit(oversized, max_bytes=64)

    assert inventory.evidence_valid is False
    assert inventory.evidence_error == "oversized"
