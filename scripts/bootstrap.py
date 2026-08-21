"""One documented path from a clean clone to a running demo (Member B).

Setup instructions were spread across the README and the external-env runbook,
each assuming a different tool and a different starting point, so "install it
and show me" was not something a new machine could follow in one go.

This script is that single path. It uses the standard library only, because it
has to run *before* the project dependencies exist.

  python scripts/bootstrap.py            check, install, verify
  python scripts/bootstrap.py --demo     ... then run the visual demo
  python scripts/bootstrap.py --check    report the environment and stop

Every step prints what it ran, so a failure points at a command you can repeat
by hand rather than at this script.
"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MIN_PYTHON = (3, 11)

_OK = "  OK   "
_FAIL = " FAIL  "
_SKIP = " SKIP  "


def _say(status: str, message: str) -> None:
    print(f"[{status}] {message}", flush=True)


def _run(cmd: list[str], *, why: str) -> bool:
    """Run a command from the repo root, echoing it so failures are repeatable."""
    print(f"\n$ {' '.join(cmd)}", flush=True)
    try:
        completed = subprocess.run(cmd, cwd=REPO, check=False)
    except FileNotFoundError:
        _say(_FAIL, f"{why}: '{cmd[0]}' not found on PATH")
        return False
    if completed.returncode != 0:
        _say(_FAIL, f"{why} (exit {completed.returncode})")
        return False
    _say(_OK, why)
    return True


def _uv() -> str | None:
    return shutil.which("uv")


def _pip_available() -> bool:
    """Match the command used by :func:`install`: ``python -m pip``.

    A virtual environment can contain the pip module even when its ``bin``
    directory is not on ``PATH``.  Looking only for a ``pip`` executable made a
    usable checkout fail the preflight check even though installation would
    work with the current interpreter.
    """

    return importlib.util.find_spec("pip") is not None


def check() -> bool:
    """Report the environment without changing anything."""
    ok = True

    version = sys.version_info
    if version[:2] >= MIN_PYTHON:
        _say(_OK, f"python {version.major}.{version.minor}.{version.micro}")
    else:
        need = f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
        _say(_FAIL, f"python {version.major}.{version.minor} is too old; need >= {need}")
        ok = False

    if (REPO / "pyproject.toml").is_file():
        _say(_OK, f"repository root: {REPO}")
    else:
        _say(_FAIL, f"pyproject.toml not found under {REPO}")
        ok = False

    if _uv():
        _say(_OK, "uv found (used for install)")
    elif _pip_available():
        _say(_OK, "uv not found; falling back to pip")
    else:
        _say(_FAIL, "neither uv nor pip is available")
        ok = False

    # Optional: only the MiniWoB++ half of the demo needs this clone.
    if (REPO / ".external_envs" / "miniwob-plusplus").is_dir():
        _say(_OK, "MiniWoB++ clone present")
    else:
        _say(_SKIP, "MiniWoB++ clone absent — the demo will run mock envs only")

    return ok


def install() -> bool:
    """Install project + dev dependencies, then the one browser the demo needs."""
    uv = _uv()
    if uv:
        if not _run([uv, "pip", "install", "-e", ".[dev]"], why="install dependencies"):
            return False
        playwright = [uv, "run", "playwright", "install", "chromium"]
    else:
        if not _run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], why="install dependencies"):
            return False
        playwright = [sys.executable, "-m", "playwright", "install", "chromium"]
    # Chromium is a large download; skipping it only breaks the browser demos,
    # so a failure here is reported but does not abort setup.
    if not _run(playwright, why="install chromium"):
        _say(_SKIP, "continuing without chromium; browser demos will not run")
    return True


def verify() -> bool:
    """Run the same test suite CI runs."""
    return _run([sys.executable, "-m", "pytest", "--tb=short", "-q"], why="test suite")


def demo(headed: bool) -> bool:
    """Run the demos that exist in this checkout."""
    ran_any = False

    smoke = REPO / "scripts" / "run_visual_grounding_smoke.py"
    if smoke.is_file():
        ran_any = True
        if not _run([sys.executable, str(smoke)], why="visual grounding smoke trace"):
            return False

    fancy = REPO / "scripts" / "run_fancy_demo.py"
    if fancy.is_file():
        ran_any = True
        cmd = [sys.executable, str(fancy), "--step-delay", "1.0"]
        cmd.append("--headed" if headed else "--headless")
        if not (REPO / ".external_envs" / "miniwob-plusplus").is_dir():
            cmd.append("--skip-miniwob")
        if not _run(cmd, why="cross-environment demo"):
            return False

    if not ran_any:
        _say(_SKIP, "no demo scripts found in this checkout")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Clean-clone setup, verification and demo.")
    parser.add_argument("--check", action="store_true", help="Report the environment and stop.")
    parser.add_argument("--demo", action="store_true", help="Run the demo after verifying.")
    parser.add_argument("--headed", action="store_true", help="Show the browser during the demo.")
    parser.add_argument("--skip-install", action="store_true", help="Assume dependencies are present.")
    args = parser.parse_args()

    print("=" * 60)
    print("  Modular Action System - clean clone bootstrap")
    print("=" * 60)

    if not check():
        print("\nEnvironment is not usable yet; fix the FAIL lines above.")
        return 1
    if args.check:
        return 0

    if not args.skip_install and not install():
        return 1
    if not verify():
        print("\nTests failed. The environment installed correctly, so this is a code issue.")
        return 1
    if args.demo and not demo(args.headed):
        return 1

    print("\n" + "=" * 60)
    print("  Ready." if not args.demo else "  Ready, demo complete.")
    if not args.demo:
        print("  Next: python scripts/bootstrap.py --demo --headed")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
