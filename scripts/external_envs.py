"""Bootstrap and inspect external CUA/Web benchmark environments.

The project keeps third-party benchmark checkouts out of git. This script reads
``env/external_benchmarks.yaml`` and installs or checks them under
``.external_envs/`` so the local smart-room demo can coexist with real
benchmark environments such as WebArena, VisualWebArena, MiniWoB++, and
OSWorld.
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "env" / "external_benchmarks.yaml"


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def run_command(command: str, *, execute: bool, cwd: Path = ROOT) -> int:
    if not execute:
        print(f"[dry-run] {command}")
        return 0
    print(f"[run] {command}")
    completed = subprocess.run(command, cwd=cwd, shell=True, check=False)  # noqa: S602 - user-facing bootstrap tool
    return completed.returncode


def env_names(manifest: dict[str, Any]) -> list[str]:
    return sorted(manifest["envs"].keys())


def list_envs(manifest: dict[str, Any]) -> None:
    for name in env_names(manifest):
        spec = manifest["envs"][name]
        print(f"{name:18} {spec['role']}")
        print(f"  homepage: {spec['homepage']}")
        if spec.get("repo"):
            print(f"  repo:     {spec['repo']}")


def bootstrap(manifest: dict[str, Any], selected: list[str], *, execute: bool) -> int:
    rc = 0
    for name in selected:
        spec = manifest["envs"][name]
        print(f"\n== {name}: {spec['role']} ==")
        for command in spec.get("install", []):
            rc = run_command(command, execute=execute) or rc
            if rc:
                return rc
    return rc


def check(manifest: dict[str, Any], selected: list[str]) -> int:
    root = ROOT / manifest.get("root", ".external_envs")
    rc = 0
    for name in selected:
        spec = manifest["envs"][name]
        print(f"\n== {name}: {spec['role']} ==")
        for command in spec.get("smoke", []):
            if command.startswith("test-path "):
                target = ROOT / command.removeprefix("test-path ").strip()
                ok = target.exists()
                print(f"[{'ok' if ok else 'missing'}] {target.relative_to(ROOT)}")
                rc = rc or (0 if ok else 1)
            else:
                rc = run_command(command, execute=True) or rc
    print(f"\nexternal env root: {root.relative_to(ROOT)}")
    return rc


def resolve_selection(manifest: dict[str, Any], names: list[str], *, all_envs: bool) -> list[str]:
    available = env_names(manifest)
    if all_envs:
        return available
    if not names:
        raise SystemExit("Select at least one env or pass --all.")
    missing = sorted(set(names) - set(available))
    if missing:
        raise SystemExit(f"Unknown env(s): {', '.join(missing)}. Available: {', '.join(available)}")
    return names


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage external CUA/Web benchmark environments.")
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List supported external environments.")

    boot = sub.add_parser("bootstrap", help="Print or run install/bootstrap commands.")
    boot.add_argument("env", nargs="*", help="Environment names from the manifest.")
    boot.add_argument("--all", action="store_true", help="Select all environments.")
    boot.add_argument("--execute", action="store_true", help="Actually run commands. Omit for dry-run.")

    chk = sub.add_parser("check", help="Run lightweight smoke checks.")
    chk.add_argument("env", nargs="*", help="Environment names from the manifest.")
    chk.add_argument("--all", action="store_true", help="Select all environments.")

    args = parser.parse_args()
    manifest = load_manifest(args.manifest)
    if args.cmd == "list":
        list_envs(manifest)
        return
    selected = resolve_selection(manifest, args.env, all_envs=args.all)
    if args.cmd == "bootstrap":
        raise SystemExit(bootstrap(manifest, selected, execute=args.execute))
    if args.cmd == "check":
        raise SystemExit(check(manifest, selected))


if __name__ == "__main__":
    main()
