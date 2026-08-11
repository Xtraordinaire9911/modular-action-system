"""Print the component map for walking a reviewer through the code.

    python scripts/code_walkthrough.py            grouped by loop phase
    python scripts/code_walkthrough.py --owner    grouped by who built it
    python scripts/code_walkthrough.py --check    only report drift, exit 1 if any

Answers "where is the planner, where is the thing that reads affordances, where
is the screenshot taken" by naming the file and the symbol for each, verified
against the working tree so the map cannot describe code that is not there.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.demos.component_map import COMPONENTS, by_owner, layers, missing  # noqa: E402

_W = 78


def _entry(component: object, indent: str = "    ") -> None:
    mark = " " if component.exists else "!"  # type: ignore[attr-defined]
    print(f"{indent}{mark} {component.name}")  # type: ignore[attr-defined]
    print(f"{indent}    {component.path}")  # type: ignore[attr-defined]
    print(f"{indent}    -> {component.symbol}")  # type: ignore[attr-defined]
    for line in _wrap(component.does, _W - len(indent) - 4):  # type: ignore[attr-defined]
        print(f"{indent}    {line}")
    print()


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Component map for a code walkthrough.")
    parser.add_argument("--owner", action="store_true", help="Group by contributor instead of loop phase.")
    parser.add_argument("--check", action="store_true", help="Only verify the map matches the tree.")
    args = parser.parse_args()

    drift = missing()

    if args.check:
        if drift:
            print("Component map is out of date; these files are missing:")
            for component in drift:
                print(f"  {component.path}  ({component.name})")
            return 1
        print(f"Component map is accurate: all {len(COMPONENTS)} entries exist.")
        return 0

    print(f"\n{'=' * _W}")
    print("  COMPONENT MAP - where each part of the agent lives")
    print(f"{'=' * _W}")

    if args.owner:
        for owner, components in sorted(by_owner().items()):
            print(f"\n  {owner}  ({len(components)} components)")
            print(f"  {'-' * (_W - 4)}")
            for component in components:
                _entry(component)
    else:
        for layer in layers():
            print(f"\n  {layer}")
            print(f"  {'-' * (_W - 4)}")
            for component in [c for c in COMPONENTS if c.layer == layer]:
                _entry(component)

    print(f"{'=' * _W}")
    if drift:
        print(f"  {len(drift)} entries point at files that do not exist:")
        for component in drift:
            print(f"    {component.path}")
    else:
        print(f"  All {len(COMPONENTS)} entries verified against the working tree.")
    print(f"{'=' * _W}\n")
    return 1 if drift else 0


if __name__ == "__main__":
    raise SystemExit(main())
