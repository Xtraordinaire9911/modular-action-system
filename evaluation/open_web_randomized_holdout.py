"""Seeded dev/locked-holdout variants for the six controlled web failures."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Literal

from evaluation.open_web_mock_failure_suite import OpenWebMockFailureCase, build_open_web_mock_failure_suite

OpenWebVariantSplit = Literal["dev", "holdout"]

DEFAULT_DEV_SEED_START = 12_000
DEFAULT_HOLDOUT_SEED_START = 22_000


@dataclass(frozen=True)
class OpenWebFailureVariant:
    """One reproducible parameterization of a labelled failure fixture."""

    case: OpenWebMockFailureCase
    split: OpenWebVariantSplit
    repetition: int
    variant_id: str
    parameters: dict[str, Any]
    signature: str

    @property
    def seed(self) -> int:
        return self.case.seed

    @property
    def failure_class(self) -> str:
        return self.case.failure_class

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_open_web_failure_variants(
    split: OpenWebVariantSplit,
    *,
    repetitions: int = 3,
    seed_start: int | None = None,
) -> list[OpenWebFailureVariant]:
    """Create deterministic variants whose parameter domains differ by split."""

    if repetitions <= 0:
        raise ValueError("repetitions must be positive")
    first_seed = seed_start
    if first_seed is None:
        first_seed = DEFAULT_DEV_SEED_START if split == "dev" else DEFAULT_HOLDOUT_SEED_START
    base_cases = build_open_web_mock_failure_suite(seed_start=first_seed)
    variants: list[OpenWebFailureVariant] = []
    seed = first_seed
    for case in base_cases:
        for repetition in range(repetitions):
            parameters = _parameters_for(case.case_id, split, repetition, seed)
            signature = _variant_signature(case.failure_class, parameters)
            variant_id = f"{split}-{case.case_id}-{repetition:02d}-seed-{seed}"
            variant_case = replace(
                case,
                seed=seed,
                episode_id=variant_id,
                coverage_level=f"controlled_randomized_{split}_evidence",
            )
            variants.append(
                OpenWebFailureVariant(
                    case=variant_case,
                    split=split,
                    repetition=repetition,
                    variant_id=variant_id,
                    parameters=parameters,
                    signature=signature,
                )
            )
            seed += 1
    return variants


def build_open_web_randomized_holdout_plan(
    *,
    dev_repetitions: int = 3,
    holdout_repetitions: int = 3,
    dev_seed_start: int = DEFAULT_DEV_SEED_START,
    holdout_seed_start: int = DEFAULT_HOLDOUT_SEED_START,
) -> dict[str, Any]:
    """Lock both splits and reject seed/signature leakage before execution."""

    dev = build_open_web_failure_variants("dev", repetitions=dev_repetitions, seed_start=dev_seed_start)
    holdout = build_open_web_failure_variants(
        "holdout",
        repetitions=holdout_repetitions,
        seed_start=holdout_seed_start,
    )
    dev_signatures = {variant.signature for variant in dev}
    holdout_signatures = {variant.signature for variant in holdout}
    dev_seeds = {variant.seed for variant in dev}
    holdout_seeds = {variant.seed for variant in holdout}
    signatures_disjoint = dev_signatures.isdisjoint(holdout_signatures)
    seeds_disjoint = dev_seeds.isdisjoint(holdout_seeds)
    if not signatures_disjoint or not seeds_disjoint:
        raise ValueError("dev and holdout variants must be disjoint before execution")
    return {
        "data_source": "open_web_randomized_holdout_plan",
        "protocol": {
            "locked_before_execution": True,
            "holdout_used_for_tuning": False,
            "failure_family_count": len({variant.failure_class for variant in dev + holdout}),
            "dev_repetitions_per_family": dev_repetitions,
            "holdout_repetitions_per_family": holdout_repetitions,
            "dev_seed_start": dev_seed_start,
            "holdout_seed_start": holdout_seed_start,
        },
        "leakage_checks": {
            "seeds_disjoint": seeds_disjoint,
            "variant_signatures_disjoint": signatures_disjoint,
            "dev_unique_signatures": len(dev_signatures) == len(dev),
            "holdout_unique_signatures": len(holdout_signatures) == len(holdout),
        },
        "splits": {
            "dev": [variant.to_dict() for variant in dev],
            "holdout": [variant.to_dict() for variant in holdout],
        },
    }


def run_open_web_randomized_holdout_suite(
    output_dir: str | Path,
    *,
    dev_repetitions: int = 3,
    holdout_repetitions: int = 3,
    dev_seed_start: int = DEFAULT_DEV_SEED_START,
    holdout_seed_start: int = DEFAULT_HOLDOUT_SEED_START,
    headless: bool = True,
    action_timeout_ms: int = 750,
    capture_screenshots: bool = True,
    session_factory: Any = None,
) -> dict[str, str]:
    """Write the locked plan, execute both splits, and aggregate evidence."""

    from evaluation.open_web_playwright_fixture_runner import run_open_web_playwright_fixture_suite

    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    plan = build_open_web_randomized_holdout_plan(
        dev_repetitions=dev_repetitions,
        holdout_repetitions=holdout_repetitions,
        dev_seed_start=dev_seed_start,
        holdout_seed_start=holdout_seed_start,
    )
    plan_path = target / "open_web_randomized_holdout_plan.json"
    plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    variants_by_split = {split: [_variant_from_dict(row) for row in rows] for split, rows in plan["splits"].items()}
    split_reports: dict[str, dict[str, Any]] = {}
    split_paths: dict[str, str] = {}
    for split in ("dev", "holdout"):
        split_dir = target / split
        paths = run_open_web_playwright_fixture_suite(
            split_dir,
            headless=headless,
            action_timeout_ms=action_timeout_ms,
            capture_screenshots=capture_screenshots,
            session_factory=session_factory,
            variants=variants_by_split[split],
        )
        report_path = Path(paths["open_web_playwright_fixture_report"])
        split_reports[split] = json.loads(report_path.read_text(encoding="utf-8"))
        split_paths[split] = str(report_path)

    report = {
        "data_source": "open_web_randomized_locked_holdout",
        "protocol": {
            **plan["protocol"],
            "browser_execution": True,
            "runtime_entrypoint": "RuntimeEpisodeRunner.run_skill_episode",
            "oracle_source": "fresh fixture data-oracle-state after seeded parameter injection",
            "claim_boundary": "controlled randomized local-browser evidence; not real open-web evidence",
            "real_open_web_evidence": False,
        },
        "leakage_checks": plan["leakage_checks"],
        "summary": _aggregate_split_summaries(split_reports),
        "splits": {
            split: {
                "summary": split_reports[split]["summary"],
                "metrics": split_reports[split]["metrics"],
                "per_family": _per_family(split_reports[split]),
                "report": split_paths[split],
            }
            for split in ("dev", "holdout")
        },
        "artifacts": {
            "locked_plan": str(plan_path),
            "dev_report": split_paths["dev"],
            "holdout_report": split_paths["holdout"],
        },
    }
    report_path = target / "open_web_randomized_holdout_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "open_web_randomized_holdout_plan": str(plan_path),
        "open_web_randomized_holdout_report": str(report_path),
        "dev_report": split_paths["dev"],
        "holdout_report": split_paths["holdout"],
    }


def _parameters_for(
    case_id: str,
    split: OpenWebVariantSplit,
    repetition: int,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    if case_id == "openweb-overlay-obstruction":
        opacity = (0.42, 0.52, 0.62) if split == "dev" else (0.74, 0.84, 0.94)
        offset_base = -18 if split == "dev" else 42
        labels = (
            ("Accept preferences", "Continue", "Acknowledge")
            if split == "dev"
            else ("Carry on with browsing", "Understood", "Got it")
        )
        return {
            "overlay_opacity": opacity[repetition % len(opacity)],
            "modal_offset_px": offset_base + repetition * 7 + rng.randint(0, 3),
            "z_index": (1000 if split == "dev" else 4000) + repetition,
            "remediation_label": labels[repetition % len(labels)],
            "remediation_control_id": f"gate-{split}-{rng.randint(1000, 9999)}",
            "modal_padding_px": (18 if split == "dev" else 31) + repetition * 3,
        }
    if case_id == "openweb-session-expiry":
        age_base = 10 if split == "dev" else 180
        return {
            "session_age_s": age_base + repetition * (17 if split == "dev" else 91) + rng.randint(0, 5),
            "auth_code": (401, 419, 440)[repetition % 3] if split == "dev" else (403, 498, 499)[repetition % 3],
        }
    if case_id == "openweb-autocomplete-validation":
        submitted = (
            ("New York, NY", "NEW YORK", "New York City")
            if split == "dev"
            else ("New York, USA", "New York (validated)", "NYC — New York")
        )
        return {
            "requested_city": "New York",
            "submitted_city": submitted[repetition % len(submitted)],
            "validator_revision": f"{split}-v{repetition + 1}-{rng.randint(10, 99)}",
        }
    if case_id == "openweb-optimistic-rollback":
        codes = (500, 502, 503) if split == "dev" else (409, 429, 504)
        delay_base = 20 if split == "dev" else 140
        return {
            "backend_status_code": codes[repetition % len(codes)],
            "rollback_delay_ms": delay_base + repetition * 25 + rng.randint(0, 9),
        }
    if case_id == "openweb-dom-visual-disagreement":
        dom_plan, visual_plan = ("premium", "basic") if split == "dev" else ("basic", "premium")
        widths = (3, 5, 7) if split == "dev" else (8, 10, 12)
        return {
            "dom_selected_plan": dom_plan,
            "visual_highlighted_plan": visual_plan,
            "highlight_width_px": widths[repetition % len(widths)],
            "highlight_hue": (260 + rng.randint(0, 15)) if split == "dev" else (15 + rng.randint(0, 15)),
        }
    if case_id == "openweb-visible-ineffective-affordance":
        code_base = 110 if split == "dev" else 710
        return {
            "ack_code": f"ACK-{code_base + repetition}-{rng.randint(0, 9)}",
            "reported_clicks": repetition + 1,
            "control_revision": f"{split}-toggle-{rng.randint(100, 999)}",
        }
    raise ValueError(f"unsupported open-web failure case: {case_id}")


def _variant_signature(failure_class: str, parameters: dict[str, Any]) -> str:
    encoded = json.dumps(
        {"failure_class": failure_class, "parameters": parameters},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _variant_from_dict(row: dict[str, Any]) -> OpenWebFailureVariant:
    return OpenWebFailureVariant(
        case=OpenWebMockFailureCase(**row["case"]),
        split=row["split"],
        repetition=int(row["repetition"]),
        variant_id=str(row["variant_id"]),
        parameters=dict(row["parameters"]),
        signature=str(row["signature"]),
    )


def _aggregate_split_summaries(reports: dict[str, dict[str, Any]]) -> dict[str, Any]:
    summaries = {split: report["summary"] for split, report in reports.items()}
    return {
        "variant_count": sum(int(summary["case_count"]) for summary in summaries.values()),
        "dev_variant_count": int(summaries["dev"]["case_count"]),
        "holdout_variant_count": int(summaries["holdout"]["case_count"]),
        "dev_failure_detection_rate": _rate(summaries["dev"]),
        "holdout_failure_detection_rate": _rate(summaries["holdout"]),
        "dev_final_success_count": int(summaries["dev"]["final_success_count"]),
        "holdout_final_success_count": int(summaries["holdout"]["final_success_count"]),
        "holdout_failure_families_passed": sum(
            1 for family in _per_family(reports["holdout"]).values() if family["failure_detection_rate"] == 1.0
        ),
        "holdout_passed": (
            int(summaries["holdout"]["postcondition_failures_detected"]) == int(summaries["holdout"]["case_count"])
            and int(summaries["holdout"]["final_success_count"]) == 0
        ),
    }


def _per_family(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in report["cases"]:
        grouped.setdefault(row["case"]["failure_class"], []).append(row)
    return {
        family: {
            "variant_count": len(rows),
            "failures_detected": sum(
                1
                for row in rows
                if row["runtime"]["postcondition_passed"] and row["runtime"]["postcondition_passed"][0] is False
            ),
            "failure_detection_rate": (
                sum(
                    1
                    for row in rows
                    if row["runtime"]["postcondition_passed"] and row["runtime"]["postcondition_passed"][0] is False
                )
                / len(rows)
            ),
        }
        for family, rows in sorted(grouped.items())
    }


def _rate(summary: dict[str, Any]) -> float:
    count = int(summary["case_count"])
    return int(summary["postcondition_failures_detected"]) / count if count else 0.0


__all__ = [
    "DEFAULT_DEV_SEED_START",
    "DEFAULT_HOLDOUT_SEED_START",
    "OpenWebFailureVariant",
    "build_open_web_failure_variants",
    "build_open_web_randomized_holdout_plan",
    "run_open_web_randomized_holdout_suite",
]
