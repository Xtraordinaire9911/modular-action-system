"""Dry-run policy proposal application.

This script is intentionally passive by default. It explains what would be
needed to apply a proposal, but it does not mutate runtime policy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def dry_run_policy_application(proposal: dict[str, Any], policy_path: str | Path) -> dict[str, Any]:
    path = Path(policy_path)
    return {
        "proposal_id": proposal.get("proposal_id", ""),
        "change_type": proposal.get("change_type", ""),
        "policy_path": str(path),
        "policy_exists": path.exists(),
        "would_apply": False,
        "requires_human_review": bool(proposal.get("needs_human_review", True)),
        "reason": "dry-run only; policy changes require release gate approval and human review",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run a runtime policy proposal.")
    parser.add_argument("proposal_json", help="Path to policy_proposals.json")
    parser.add_argument("--proposal-id", default="", help="Specific proposal id; defaults to the first proposal")
    parser.add_argument("--policy", default="config/runtime_policy.yaml", help="Runtime policy path")
    args = parser.parse_args()

    payload = json.loads(Path(args.proposal_json).read_text(encoding="utf-8"))
    proposals = payload.get("proposals", [])
    selected = next(
        (proposal for proposal in proposals if not args.proposal_id or proposal.get("proposal_id") == args.proposal_id),
        None,
    )
    if selected is None:
        raise SystemExit("No matching proposal found")
    print(json.dumps(dry_run_policy_application(selected, args.policy), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
