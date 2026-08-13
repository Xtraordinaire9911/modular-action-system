"""Read API keys from a local file that is never committed and never printed.

A key pasted into a chat window, a terminal, or a commit is a key that has to be
rotated. So the only place this project looks for one is a file you create
yourself, on your own machine, which git is configured to ignore:

    .env.local

    DASHSCOPE_API_KEY=sk-...

Nothing here returns the value to a caller that might log it. ``load_local_env``
puts what it finds into ``os.environ`` and reports only which names were set, so
a run can say "the key is configured" without the key appearing anywhere.

An environment variable that is already set always wins, so CI secrets and a
one-off ``VLM_API_KEY=... python ...`` still take precedence over the file.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_ENV_FILE = Path(".env.local")

# Only these are read out of the file. An allowlist rather than "every line",
# so a stray line in a local file cannot quietly set PATH or PYTHONPATH.
KNOWN_KEYS = (
    "DASHSCOPE_API_KEY",
    "ZHIPU_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "VLM_API_KEY",
    "VLM_MODEL",
    "VLM_BASE_URL",
    "OPENAI_BASE_URL",
)


def load_local_env(path: str | Path = DEFAULT_ENV_FILE) -> list[str]:
    """Load known keys from ``path`` into the environment, without revealing them.

    Returns the names that were set from the file, in file order. Names already
    present in the environment are left alone and are not reported, so the
    caller can tell "the file supplied this" from "the shell already had it".
    """
    target = Path(path)
    if not target.is_file():
        return []

    applied: list[str] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        # Quotes are stripped because every guide tells people to add them and
        # a key with a stray quote fails with an authentication error that
        # points nowhere near the cause.
        value = value.strip().strip("'\"")
        if name not in KNOWN_KEYS or not value:
            continue
        if os.environ.get(name):
            continue  # an explicit environment variable outranks the file
        os.environ[name] = value
        applied.append(name)
    return applied


def configured_key_names() -> list[str]:
    """Which known keys are set right now. Names only, never values."""
    return [name for name in KNOWN_KEYS if os.environ.get(name)]


__all__ = ["DEFAULT_ENV_FILE", "KNOWN_KEYS", "configured_key_names", "load_local_env"]
