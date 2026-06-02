"""Unit tests for DOM and WoT System-1 executors (no live server required).

All network calls are patched so these run cleanly in CI.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.contracts.types import Observation, SkillCall
from src.effectors.dom_executor import DomExecutor
from src.effectors.wot_executor import WotExecutor


_THERMOSTAT_TD = {
    "@context": ["https://www.w3.org/2019/wot/td/v1"],
    "id": "thermostat_A",
    "title": "Thermostat",
    "securityDefinitions": {"nosec_sc": {"scheme": "nosec"}},
    "security": "nosec_sc",
    "properties": {
        "targetTemperature": {
            "type": "number",
            "readOnly": False,
            "forms": [{"href": "http://localhost:5001/thermostat/properties/targetTemperature"}],
        }
    },
    "actions": {
        "setTargetTemperature": {
            "input": {"type": "number"},
            "forms": [
                {
                    "href": "http://localhost:5001/thermostat/actions/setTargetTemperature",
                    "htv:methodName": "POST",
                }
            ],
        }
    },
}


# ── DomExecutor tests ─────────────────────────────────────────────────────────

class TestDomExecutorNoPlaywright:
    """Tests that run when Playwright is not installed."""

    def _make_obs(self) -> Observation:
        return Observation()

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_failure(self):
        exec_ = DomExecutor()
        result = await exec_.execute(
            SkillCall(skill_id="unknown_skill", params={}),
            self._make_obs(),
        )
        assert not result.success
        assert result.backend_used == "dom"
        assert "no DOM steps" in result.failure_reason

    @pytest.mark.asyncio
    async def test_known_skill_without_playwright_returns_failure(self):
        exec_ = DomExecutor()
        result = await exec_.execute(
            SkillCall(skill_id="confirm_booking", params={"room": "A", "time": "14:00"}),
            self._make_obs(),
        )
        assert not result.success
        assert "Playwright" in result.failure_reason or "no DOM steps" not in result.failure_reason

    @pytest.mark.asyncio
    async def test_probe_availability_returns_false_without_playwright(self):
        exec_ = DomExecutor()
        result = await exec_.probe_availability()
        assert result is False


# ── WotExecutor tests ─────────────────────────────────────────────────────────

class TestWotExecutor:

    def _make_obs(self) -> Observation:
        return Observation()

    def test_load_tds_indexes_affordances(self):
        exec_ = WotExecutor(tds=[_THERMOSTAT_TD])
        aff = exec_.get_affordance("wot_thermostat_A_setTargetTemperature")
        assert aff is not None
        assert aff.action == "invoke"

    @pytest.mark.asyncio
    async def test_unknown_skill_returns_failure(self):
        exec_ = WotExecutor(tds=[_THERMOSTAT_TD])
        result = await exec_.execute(
            SkillCall(skill_id="unknown_skill", params={}),
            self._make_obs(),
        )
        assert not result.success
        assert result.backend_used == "wot"

    @pytest.mark.asyncio
    async def test_skill_without_loaded_td_returns_failure(self):
        exec_ = WotExecutor()  # no TDs loaded
        result = await exec_.execute(
            SkillCall(skill_id="set_temperature", params={"room": "A", "target": 22}),
            self._make_obs(),
        )
        assert not result.success
        assert "not loaded" in result.failure_reason

    @pytest.mark.asyncio
    async def test_successful_http_execution(self):
        exec_ = WotExecutor(tds=[_THERMOSTAT_TD])

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"targetTemperature": 22}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("src.effectors.wot_executor.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            # also make _HTTPX_AVAILABLE True
            with patch("src.effectors.wot_executor._HTTPX_AVAILABLE", True):
                result = await exec_.execute(
                    SkillCall(skill_id="set_temperature", params={"room": "A", "target": 22}),
                    self._make_obs(),
                )

        assert result.success
        assert result.backend_used == "wot"
        assert result.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_http_error_returns_failure(self):
        exec_ = WotExecutor(tds=[_THERMOSTAT_TD])

        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.text = "Service Unavailable"

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("src.effectors.wot_executor.httpx") as mock_httpx:
            mock_httpx.AsyncClient.return_value = mock_client
            with patch("src.effectors.wot_executor._HTTPX_AVAILABLE", True):
                result = await exec_.execute(
                    SkillCall(skill_id="set_temperature", params={"room": "A", "target": 22}),
                    self._make_obs(),
                )

        assert not result.success
        assert "503" in result.failure_reason
