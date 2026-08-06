"""Offline tests for the external-benchmark bridge (Member B, B-109).

No browser or network: a stateful fake page stands in for a benchmark site,
driven through the real BrowserSession + DOM Transducer + DomExecutor path.
"""

from __future__ import annotations

import asyncio

from evaluation.cross_env_eval import aggregate
from src.benchmarks.task_spec import BenchmarkRunResult, BenchmarkTask
from src.benchmarks.runtime_web_adapter import RuntimeWebEnvironmentAdapter
from src.benchmarks.web_benchmark_adapter import WebBenchmarkAdapter
from src.perception.browser_session import BrowserSession
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec
from src.runtime.state_machine import RuntimeState

_HTML = """
<html><body>
  <input name="q" />
  <button data-testid="submit">Submit</button>
</body></html>
"""


class FakeBenchPage:
    """Minimal benchmark page: clicking Submit makes the body report success."""

    def __init__(self) -> None:
        self._submitted = False
        self.typed = ""
        self.calls: list[tuple] = []

    def goto(self, url: str) -> None:
        self._submitted = False  # reset() returns to the start state
        self.calls.append(("goto", url))

    def content(self) -> str:
        return _HTML

    def click(self, selector: str) -> None:
        self.calls.append(("click", selector))
        if "submit" in selector:
            self._submitted = True

    def fill(self, selector: str, value: str) -> None:
        self.typed = value
        self.calls.append(("fill", selector, value))

    def text_content(self, selector: str) -> str | None:
        if selector == "body":
            return "Submitted!" if self._submitted else "Search page"
        return None


def _session() -> BrowserSession:
    return BrowserSession(FakeBenchPage(), url="http://bench/start")


def test_adapter_observes_external_page_as_pam():
    adapter = WebBenchmarkAdapter(_session())
    task = BenchmarkTask(env="miniwob", task_id="click-submit", start_url="http://bench/start", goal="Submit")
    pam = adapter.reset(task)
    assert pam.find_by_label("Submit") is not None
    assert pam.page_id == "miniwob:click-submit"


def test_adapter_runs_and_scores_success_via_text_proxy():
    adapter = WebBenchmarkAdapter(_session())
    task = BenchmarkTask(
        env="miniwob",
        task_id="click-submit",
        start_url="http://bench/start",
        goal="Submit the form",
        success_text=["submitted"],
    )
    result = adapter.run(task, steps=[("Submit", None)])
    assert result.success and result.steps == 1
    assert result.backend_counts == {"dom": 1} and result.failure_reason is None


def test_adapter_reports_unresolved_label():
    adapter = WebBenchmarkAdapter(_session())
    task = BenchmarkTask(env="webarena", task_id="missing", start_url="http://bench/start", goal="x")
    result = adapter.run(task, steps=[("Nonexistent Button", None)])
    assert not result.success and "not found" in result.failure_reason


def test_custom_success_check_overrides_text_proxy():
    adapter = WebBenchmarkAdapter(_session())
    task = BenchmarkTask(
        env="webarena",
        task_id="custom",
        start_url="http://bench/start",
        goal="Submit",
        success_check=lambda a: "submitted" in a.page_text().lower(),
    )
    assert adapter.run(task, steps=[("Submit", None)]).success


def test_cross_env_aggregate_m1():
    runs = [
        BenchmarkRunResult("miniwob", "t1", True, 1, 10.0, {"dom": 1}),
        BenchmarkRunResult("miniwob", "t2", False, 1, 12.0, {"dom": 1}),
        BenchmarkRunResult("webarena", "t3", True, 2, 30.0, {"dom": 2}),
    ]
    report = aggregate(runs)
    by_env = {row["env"]: row for row in report["per_env_M1"]}
    assert by_env["miniwob"]["success_rate"] == 0.5
    assert by_env["webarena"]["success_rate"] == 1.0
    assert report["overall_success_rate"] == round(2 / 3, 4)
    assert report["n_tasks"] == 3 and report["n_envs"] == 2


def test_runtime_web_adapter_runs_external_page_through_episode_runner():
    benchmark = WebBenchmarkAdapter(_session())
    task = BenchmarkTask(
        env="external",
        task_id="submit-form",
        start_url="http://bench/start",
        goal="submit form",
        success_text=["submitted"],
    )
    runtime_adapter = RuntimeWebEnvironmentAdapter(
        benchmark,
        task,
        completions={"dom_button_1"},
        goal_state="benchmark.solved == true",
    )

    outcome = asyncio.run(
        RuntimeEpisodeRunner().run_goal_episode(
            runtime_adapter,
            RuntimeEpisodeSpec(
                task_id="external:submit-form",
                goal_id="submit-form",
                goal_state="benchmark.solved == true",
                data_source="external_web_runtime",
            ),
        )
    )

    assert outcome.result.state == RuntimeState.COMPLETED
    assert outcome.result.final_outcome_verified
    assert outcome.metrics.metadata["data_source"] == "external_web_runtime"
    assert outcome.transition_ledger.records[0].params["affordance_id"] == "dom_button_1"
