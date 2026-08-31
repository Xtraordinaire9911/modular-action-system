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

import codecs
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
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_MODEL_ID",
    "LLM_BASE_URL",
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


def configured_key_names(path: str | Path = DEFAULT_ENV_FILE) -> list[str]:
    """Which known keys are set, after loading the local file. Names only.

    Loads first on purpose. An earlier version only read the environment, so it
    answered "nothing is configured" for a correctly written file and sent the
    reader looking for a problem that was not there.
    """
    load_local_env(path)
    return [name for name in KNOWN_KEYS if os.environ.get(name)]


def describe_local_env(path: str | Path = DEFAULT_ENV_FILE) -> list[str]:
    """Explain what is wrong with the key file, revealing nothing from it.

    Every message names the line and the shape, never the contents. The check
    that matters most is the one this was written for: a file holding a bare key
    with no ``NAME=`` in front of it, which is silently ignored and looks
    identical to a missing file from the outside.
    """
    target = Path(path)
    if not target.is_file():
        return [f"{target} does not exist. Create it with one line: DASHSCOPE_API_KEY=sk-..."]

    try:
        raw = target.read_bytes()
    except OSError as exc:
        return [f"{target} could not be read: {exc}"]

    notes: list[str] = []
    if raw[:3] == codecs.BOM_UTF8:
        notes.append("the file starts with a byte order mark; save it as UTF-8 without BOM")

    lines = raw.decode("utf-8-sig", errors="replace").splitlines()
    meaningful = [
        (n, line.strip()) for n, line in enumerate(lines, 1) if line.strip() and not line.strip().startswith("#")
    ]
    if not meaningful:
        notes.append(f"{target} has no settings in it, only blank or commented lines")

    for number, line in meaningful:
        if "=" not in line:
            notes.append(
                f"line {number} is {len(line)} characters with no '=' in it. This looks like a bare "
                "key. It needs the variable name in front, for example: DASHSCOPE_API_KEY=<the key>"
            )
            continue
        name = line.partition("=")[0].strip()
        value = line.partition("=")[2].strip().strip("'\"")
        if not value:
            notes.append(f"line {number} sets {name} to nothing")
        elif name not in KNOWN_KEYS:
            notes.append(
                f"line {number} sets {name!r}, which this project does not read. Known names: {', '.join(KNOWN_KEYS)}"
            )

    if not notes:
        names = configured_key_names(path)
        notes.append(f"{target} looks correct. Configured: {', '.join(names) if names else 'nothing'}")
    return notes


__all__ = [
    "DEFAULT_ENV_FILE",
    "KNOWN_KEYS",
    "configured_key_names",
    "describe_local_env",
    "load_local_env",
]
