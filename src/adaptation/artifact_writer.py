"""Write reviewable adaptation artifacts.

These artifacts are intentionally passive: they summarize evidence and produce
policy proposals for review, but never mutate runtime policy by themselves.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.adaptation.pattern_miner import PatternProposal
from src.adaptation.release_gate import ReleaseGate, ReleaseGateInput
from src.adaptation.trace_ledger import TraceLedger


def write_adaptation_artifacts(
    ledger: TraceLedger,
    proposals: list[PatternProposal],
    directory: str | Path,
) -> dict[str, Path]:
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)

    ledger_path = ledger.write_jsonl(target / "trace_ledger.jsonl")
    report_path = target / "adaptation_report.json"
    policy_path = target / "policy_proposals.json"

    report_path.write_text(
        json.dumps(_build_report(ledger, proposals), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    policy_path.write_text(
        json.dumps(_build_policy_proposals(proposals), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return {
        "ledger": ledger_path,
        "report": report_path,
        "policy_proposals": policy_path,
    }


def _build_report(ledger: TraceLedger, proposals: list[PatternProposal]) -> dict:
    return {
        "summary": {
            "total_failure_events": len(ledger.events),
            "pattern_candidates": len(proposals),
            "policy_proposals": len(proposals),
        },
        "patterns": [
            {
                "signature": proposal.signature,
                "proposal_type": proposal.proposal_type,
                "support": proposal.support,
                "recovery_success_rate": proposal.recovery_success_rate,
                "failure_boundary": proposal.analysis.boundary.value,
                "failure_type": proposal.analysis.failure_type,
                "recommendation": _recommendation(proposal),
            }
            for proposal in proposals
        ],
    }


def _build_policy_proposals(proposals: list[PatternProposal]) -> dict:
    gate = ReleaseGate()
    return {
        "proposals": [
            _with_release_gate(
                {
                    "proposal_id": f"policy_{index:03d}",
                    "change_type": proposal.proposal_type,
                    "signature": proposal.signature,
                    "reason": "; ".join(proposal.analysis.evidence),
                    "support": proposal.support,
                    "recovery_success_rate": proposal.recovery_success_rate,
                    "safe_to_auto_apply": False,
                    "needs_human_review": True,
                    "required_checks": [
                        "normal_suite_passes",
                        "failure_suite_non_regression",
                        "safety_non_regression",
                        "conflict_halt_still_blocks_system1",
                        "held_out_cases_improve_or_hold",
                        "cost_within_budget",
                    ],
                },
                gate,
            )
            for index, proposal in enumerate(proposals, start=1)
        ]
    }


def _with_release_gate(proposal: dict, gate: ReleaseGate) -> dict:
    checks = {check: False for check in proposal["required_checks"]}
    gate_result = gate.evaluate(
        ReleaseGateInput(
            proposal_id=proposal["proposal_id"],
            change_type=proposal["change_type"],
            checks=checks,
            human_approved=False,
        )
    )
    proposal["release_gate"] = {
        "approved": gate_result.approved,
        "safe_to_apply": gate_result.safe_to_apply,
        "reasons": gate_result.reasons,
        "required_checks": gate_result.required_checks,
    }
    return proposal


def _recommendation(proposal: PatternProposal) -> str:
    if proposal.proposal_type == "backend_reliability_adjustment":
        return "Review backend routing or reliability policy for this repeated failure signature."
    return "Review runtime policy for this repeated failure signature."
