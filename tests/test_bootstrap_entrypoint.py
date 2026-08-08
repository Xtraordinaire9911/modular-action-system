"""The clean-clone entrypoint must work before dependencies exist (Member B).

bootstrap.py is the one documented path from a fresh clone to a running demo, so
it runs *before* `pip install`. If it ever imports a third-party package — or
anything from src/ — it fails at the exact moment it is supposed to help, and
the failure looks like a broken repository rather than a missing dependency.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

BOOTSTRAP = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap.py"


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names imported anywhere in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_entrypoint_exists_and_parses():
    assert BOOTSTRAP.is_file(), "the documented clean-clone path must exist"
    ast.parse(BOOTSTRAP.read_text(encoding="utf-8"))


def test_entrypoint_imports_only_the_standard_library():
    roots = _imported_roots(BOOTSTRAP)
    assert roots, "expected at least one import"
    non_stdlib = {name for name in roots if name not in sys.stdlib_module_names}
    assert not non_stdlib, f"bootstrap must not need installed packages, found: {sorted(non_stdlib)}"


def test_entrypoint_does_not_import_the_project():
    roots = _imported_roots(BOOTSTRAP)
    assert "src" not in roots and "scripts" not in roots and "evaluation" not in roots


def test_check_reports_this_repository_as_usable():
    spec = importlib.util.spec_from_file_location("_bootstrap_under_test", BOOTSTRAP)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.check() is True
    # The guard that actually matters on an old interpreter.
    assert module.MIN_PYTHON == (3, 11)
