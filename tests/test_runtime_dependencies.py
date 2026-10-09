"""Every third-party module the installed packages import is a runtime dependency (AUDIT F-14).

A plain ``pip install -e .`` installs only ``[project].dependencies``; anything the served
packages import from ``[project.optional-dependencies]`` fails with ModuleNotFoundError on a
clean machine.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PACKAGES = ("router_core", "acquirer_sim", "data_layer", "baseline_router")
# Import name -> distribution name, where they differ.
DISTRIBUTION = {"pydantic_settings": "pydantic-settings"}


def _declared_runtime() -> set[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return {re.split(r"[\[<>=!~ ;]", dep, maxsplit=1)[0].lower() for dep in project["dependencies"]}


def _third_party_imports() -> dict[str, set[str]]:
    """Top-level third-party module -> files importing it, across the runtime packages."""
    local = set(RUNTIME_PACKAGES)
    found: dict[str, set[str]] = {}
    for package in RUNTIME_PACKAGES:
        for path in (ROOT / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    top = name.split(".", 1)[0]
                    if top not in local and top not in sys.stdlib_module_names:
                        found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


def test_runtime_imports_are_runtime_dependencies() -> None:
    declared = _declared_runtime()
    missing = {
        module: sorted(files)
        for module, files in _third_party_imports().items()
        if DISTRIBUTION.get(module, module).lower() not in declared
    }
    assert missing == {}
