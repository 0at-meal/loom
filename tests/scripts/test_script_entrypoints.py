"""Scripts documented as ``python scripts/<name>.py`` must import when run as a file."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _is_file_script(path: Path) -> bool:
    """An entry point (has a main block) that imports the ``scripts`` package."""
    text = path.read_text(encoding="utf-8")
    return "from scripts." in text and 'if __name__ == "__main__":' in text


# ``scripts`` is not installed by ``pip install -e``, so these need the repo root on sys.path.
FILE_SCRIPTS = sorted(p for p in SCRIPTS.rglob("*.py") if _is_file_script(p))


def test_found_the_scripts_that_import_the_scripts_package() -> None:
    """The scan finds the known cases, so the parametrized test below is not vacuous."""
    names = {p.name for p in FILE_SCRIPTS}
    assert {"steady_state_share.py", "whitepaper_examples.py", "run_suite.py"} <= names


@pytest.mark.parametrize("script", FILE_SCRIPTS, ids=lambda p: p.name)
def test_script_imports_when_run_as_a_file(script: Path, tmp_path: Path) -> None:
    """Load the file the way ``python path/to/script.py`` does, from outside the repo root."""
    code = (
        "import runpy, sys; "
        "sys.path[0] = sys.argv[1].rsplit('/', 1)[0]; "
        "runpy.run_path(sys.argv[1], run_name='__entrypoint_check__')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, script.as_posix()],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
