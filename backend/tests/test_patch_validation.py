from __future__ import annotations

from pathlib import Path

import pytest

from app.agent.budgets import AgentBudgets
from app.agent.patches import (
    PatchValidationError,
    PatchValidator,
    apply_validated_patch,
)
from app.repositories.git import run_git
from app.repositories.path_policy import RepositoryPathPolicy

_VALID_PATCH = """diff --git a/src/calc.py b/src/calc.py
--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,2 @@
 def add(left, right):
-    return left - right
+    return left + right
"""


def _validator(tmp_path: Path) -> tuple[Path, PatchValidator]:
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "hidden_tests").mkdir()
    (root / "src" / "calc.py").write_text(
        "def add(left, right):\n    return left - right\n",
        encoding="utf-8",
        newline="\n",
    )
    (root / "tests" / "test_calc.py").write_text("def test_add(): pass\n", encoding="utf-8")
    (root / "hidden_tests" / "test_secret.py").write_text("SECRET = 1\n", encoding="utf-8")
    run_git(["init"], cwd=root)
    policy = RepositoryPathPolicy(
        root,
        allowed_paths=("src/",),
        forbidden_paths=("tests/",),
        hidden_paths=("hidden_tests/",),
    )
    return root, PatchValidator(policy, AgentBudgets())


def test_validates_and_applies_safe_text_patch(tmp_path: Path) -> None:
    root, validator = _validator(tmp_path)

    validation = validator.validate(_VALID_PATCH)
    apply_validated_patch(_VALID_PATCH, validation, root)

    assert validation.files_changed == ["src/calc.py"]
    assert validation.lines_added == 1
    assert validation.lines_removed == 1
    assert "left + right" in (root / "src" / "calc.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "path",
    ["../outside.py", ".git/config", "hidden_tests/test_secret.py", "tests/test_calc.py"],
)
def test_rejects_unsafe_or_unpermitted_changed_paths(tmp_path: Path, path: str) -> None:
    _, validator = _validator(tmp_path)
    patch = _VALID_PATCH.replace("src/calc.py", path)

    with pytest.raises(PatchValidationError):
        validator.validate(patch, check_applies=False)


def test_rejects_symlink_file_mode_patch(tmp_path: Path) -> None:
    _, validator = _validator(tmp_path)
    patch = """diff --git a/src/calc.py b/src/calc.py
index 1111111..2222222 120000
--- a/src/calc.py
+++ b/src/calc.py
@@ -1 +1 @@
-old-target
+new-target
"""

    with pytest.raises(PatchValidationError, match="mode-only"):
        validator.validate(patch, check_applies=False)


def test_application_is_bound_to_the_validated_patch_hash(tmp_path: Path) -> None:
    root, validator = _validator(tmp_path)
    validation = validator.validate(_VALID_PATCH)
    changed = _VALID_PATCH.replace("left + right", "left * right")

    with pytest.raises(PatchValidationError, match="validation record"):
        apply_validated_patch(changed, validation, root)
