from __future__ import annotations

import asyncio

from scripts.run_supervised_smartroom_demo import (
    DEFAULT_UTTERANCE,
    SmartRoomEpisodeAdapter,
    build_runtime_goal,
    integrated_bindings,
)
from src.isolation import EpisodeIsolationSession, IsolationState
from src.runtime.episode import EpisodeContext, ObservationRequest
from src.runtime.episode_runner import RuntimeEpisodeRunner, RuntimeEpisodeSpec
from src.runtime.live_observation import bind_live_observation_to_request, observation_from_live_sources


def test_combined_utterance_selects_the_shared_composite_skill() -> None:
    plan, goal, selection = build_runtime_goal(DEFAULT_UTTERANCE, use_model=False)

    assert plan.source == "rule_fallback"
    assert goal.goal_id == "room_session_prepared"
    assert goal.goal_state == "room_session_prepared"
    assert goal.parameters == {"room": "C", "time": "15:30"}
    assert selection.skill_call.params == {
        "room": "C",
        "time": "15:30",
        "brightness": 30,
        "power": "on",
        "target_temperature": 21,
    }
    assert selection.skill_tuple.skill_id == "prepare_and_confirm_room"
    assert selection.skill_tuple.allowed_backends == ["dom", "wot"]


def test_integrated_bindings_cover_both_surfaces_and_protect_the_commit() -> None:
    bindings = integrated_bindings()

    assert {binding.source for binding in bindings} == {"DOM", "WOT"}
    assert {binding.binds_parameter for binding in bindings if binding.binds_parameter} == {
        "room",
        "time",
        "brightness",
        "power",
        "target_temperature",
    }
    commit = next(binding for binding in bindings if binding.completion_for)
    assert commit.completion_for == "prepare_and_confirm_room"
    assert commit.safety_level == "high"


class _ReusableIsolation:
    def __init__(self) -> None:
        self.sessions: list[EpisodeIsolationSession] = []

    async def provision(self, episode: EpisodeContext) -> EpisodeIsolationSession:
        session = EpisodeIsolationSession(
            task_id=episode.task_id,
            episode_id=episode.episode_id,
            checkpoint={},
            state=IsolationState.ACTIVE,
        )
        self.sessions.append(session)
        return session

    async def checkpoint(self, session: EpisodeIsolationSession) -> dict[str, object]:
        return dict(session.checkpoint)

    async def pause(self, session: EpisodeIsolationSession) -> None:
        session.state = IsolationState.PAUSED

    async def resume(self, session: EpisodeIsolationSession) -> None:
        session.state = IsolationState.ACTIVE

    async def restore(self, session: EpisodeIsolationSession) -> None:
        session.restored = True
        session.state = IsolationState.RESTORED

    async def dispose(self, session: EpisodeIsolationSession) -> None:
        session.restored = True
        session.disposed = True
        session.state = IsolationState.DISPOSED


class _PerceptionEnvironment:
    def __init__(self) -> None:
        self.begun_episode_ids: list[str] = []
        self.cache: list[str] = ["stale-before-first-run"]
        self.cache_seen_at_observation: list[list[str]] = []

    def begin_episode(self, episode_id: str) -> None:
        self.begun_episode_ids.append(episode_id)
        self.cache.clear()

    async def observe(self, request: ObservationRequest):
        self.cache_seen_at_observation.append(list(self.cache))
        self.cache.append(request.episode_id)
        observed = observation_from_live_sources(page_state={"task": {"done": True}})
        return bind_live_observation_to_request(observed, request_id=request.request_id)


def test_canonical_adapter_resets_perception_at_each_of_two_isolated_runner_episodes() -> None:
    async def scenario() -> tuple[object, object, _PerceptionEnvironment]:
        environment = _PerceptionEnvironment()
        isolation = _ReusableIsolation()
        adapter = SmartRoomEpisodeAdapter(environment, object(), {})  # type: ignore[arg-type]
        runner = RuntimeEpisodeRunner(isolation_provider=isolation)
        spec = RuntimeEpisodeSpec(
            task_id="two-isolated-smartroom-runs",
            goal_id="already_done",
            goal_state="task.done == true",
        )

        first = await runner.run_goal_episode(adapter, spec)
        environment.cache.append("stale-between-runs")
        second = await runner.run_goal_episode(adapter, spec)
        return first, second, environment

    first, second, environment = asyncio.run(scenario())

    assert first.result.episode_id != second.result.episode_id
    assert environment.begun_episode_ids == [first.result.episode_id, second.result.episode_id]
    assert environment.cache_seen_at_observation == [[], []]
