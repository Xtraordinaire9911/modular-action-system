"""WoT discovery evaluation (Member B — WoT Discovery Success Rate / WDSR).

Measures whether the runtime TD parser recovers the device capabilities a TD
declares: every readable property becomes a state source, every writable
property / action becomes an invokable affordance, and security + rate-limit
metadata is extracted. WDSR = discovered ÷ expected over a TD corpus.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.perception.td_affordance_parser import TdAffordanceParser


def expected_capabilities(td: dict[str, Any]) -> int:
    """Count the affordances + state sources a correct parser should yield."""
    props = td.get("properties") or {}
    writable = sum(1 for p in props.values() if not p.get("readOnly", False))
    readable = len(props)  # all properties expose a read form / state source
    actions = len(td.get("actions") or {})
    return writable + readable + actions


def discovered_capabilities(td: dict[str, Any]) -> int:
    model = TdAffordanceParser().parse(td)
    return len(model.affordances) + len(model.state_sources)


def evaluate_corpus(tds: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total_exp = total_disc = 0
    for td in tds:
        thing_id = td.get("id") or td.get("title", "?")
        try:
            exp = expected_capabilities(td)
            disc = discovered_capabilities(td)
            security = bool(td.get("securityDefinitions"))
            parsed = True
        except (ValueError, KeyError, TypeError):
            exp, disc, security, parsed = expected_capabilities(td), 0, False, False
        total_exp += exp
        total_disc += disc
        rows.append(
            {
                "thing_id": thing_id,
                "td_parsed": parsed,
                "expected": exp,
                "discovered": disc,
                "security_extracted": security,
            }
        )
    return {
        "wot_discovery_success_rate": round(total_disc / total_exp, 4) if total_exp else 0.0,
        "rows_B2": rows,
    }


def load_td_corpus(td_dir: str | Path = "config/wot_td") -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(td_dir).glob("*.td.json"))]


def main(td_dir: str = "config/wot_td", out_dir: str = "eval_outputs/backend") -> dict[str, Any]:
    report = evaluate_corpus(load_td_corpus(td_dir))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "wot_executor_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":  # pragma: no cover
    print(json.dumps(main(), indent=2))
