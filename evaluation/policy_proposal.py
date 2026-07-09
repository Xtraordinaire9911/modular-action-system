"""Build passive policy proposal payloads from mined patterns."""

from __future__ import annotations

from src.adaptation.artifact_writer import _build_policy_proposals
from src.adaptation.pattern_miner import PatternProposal


def build_policy_proposals(proposals: list[PatternProposal]) -> dict:
    return _build_policy_proposals(proposals)
