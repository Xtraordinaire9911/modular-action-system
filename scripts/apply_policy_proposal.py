"""Inspect or apply a runtime policy proposal.

Default mode is a passive dry-run: it explains what would change without
mutating anything. With ``--apply`` it performs the bounded closed-loop step —
but only for a proposal the release gate already approved and marked
``safe_to_apply``, and only for a low-risk change type. Approved changes are
written to a persistent policy overlay that the runtime backend router loads on
the next episode. ``--revert <proposal_id>`` undoes a previously applied change.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adaptation.policy_store import PolicyStore, apply_proposal, revert_proposal  # noqa: E402


def dry_run_policy_application(proposal: dict[str, Any], policy_path: str | Path) -> dict[str, Any]:
    path = Path(policy_path)
    gate = proposal.get("release_gate") or {}
    return {
        "proposal_id": proposal.get("proposal_id", ""),
        "change_type": proposal.get("change_type", ""),
        "policy_path": str(path),
        "policy_exists": path.exists(),
        "would_apply": bool(
            gate.get("approved")
            and gate.get("safe_to_apply")
            and proposal.get("change_type")
            in {
                "backend_reliability_adjustment",
                "failure_profile_weight",
                "preferred_backend_order_change",
            }
        ),
        "requires_human_review": bool(proposal.get("needs_human_review", True)),
        "reason": "dry-run only; use --apply to persist an approved low-risk change",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or apply a runtime policy proposal.")
    parser.add_argument("proposal_json", help="Path to policy_proposals.json")
    parser.add_argument("--proposal-id", default="", help="Specific proposal id; defaults to the first proposal")
    parser.add_argument("--policy", default="config/adaptation_policy.json", help="Runtime policy overlay path")
    parser.add_argument("--apply", action="store_true", help="Apply the approved low-risk change (closes the loop)")
    parser.add_argument("--revert", default="", metavar="PROPOSAL_ID", help="Revert a previously applied proposal")
    args = parser.parse_args()

    store = PolicyStore(args.policy)

    if args.revert:
        overlay = store.load()
        changed = revert_proposal(overlay, args.revert)
        if changed:
            store.save(overlay)
        print(json.dumps({"proposal_id": args.revert, "reverted": changed}, indent=2, sort_keys=True))
        return

    payload = json.loads(Path(args.proposal_json).read_text(encoding="utf-8"))
    proposals = payload.get("proposals", [])
    selected = next(
        (proposal for proposal in proposals if not args.proposal_id or proposal.get("proposal_id") == args.proposal_id),
        None,
    )
    if selected is None:
        raise SystemExit("No matching proposal found")

    if not args.apply:
        print(json.dumps(dry_run_policy_application(selected, args.policy), indent=2, sort_keys=True))
        return

    overlay = store.load()
    outcome = apply_proposal(overlay, selected)
    if outcome.applied:
        store.save(overlay)
    print(
        json.dumps(
            {"proposal_id": outcome.proposal_id, "applied": outcome.applied, "reason": outcome.reason},
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
