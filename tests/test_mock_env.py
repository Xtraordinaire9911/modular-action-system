"""Unit tests for mock Flask apps (no running server — uses test client)."""

import json

import pytest
from env.node_wot_server.app import app as wot_app
from env.react_dashboard.app import app as dashboard_app


@pytest.fixture
def dashboard():
    dashboard_app.config["TESTING"] = True
    with dashboard_app.test_client() as client:
        client.post("/api/reset")
        yield client


@pytest.fixture
def wot():
    wot_app.config["TESTING"] = True
    with wot_app.test_client() as client:
        client.post("/api/reset")
        yield client


# ── Dashboard tests ───────────────────────────────────────────────────────────


def test_dashboard_index_returns_200(dashboard):
    resp = dashboard.get("/")
    assert resp.status_code == 200
    assert b"Book Room" in resp.data


def test_dashboard_booking_sets_confirmed_class(dashboard):
    resp = dashboard.post("/book", data={"room": "A", "time": "14:00"})
    assert resp.status_code == 200
    assert b"booking-confirmed" in resp.data


def test_dashboard_unknown_room_returns_error(dashboard):
    resp = dashboard.post("/book", data={"room": "Z", "time": "14:00"})
    assert b"booking-error" in resp.data


def test_dashboard_status_endpoint(dashboard):
    dashboard.post("/book", data={"room": "A", "time": "14:00"})
    resp = dashboard.get("/status/A")
    data = json.loads(resp.data)
    assert data["booked"] is True


def test_dashboard_reset(dashboard):
    dashboard.post("/book", data={"room": "A", "time": "14:00"})
    dashboard.post("/api/reset")
    resp = dashboard.get("/status/A")
    data = json.loads(resp.data)
    assert data["booked"] is False


# ── WoT server tests ──────────────────────────────────────────────────────────


def test_wot_set_temperature_success(wot):
    resp = wot.post(
        "/thermostat/actions/setTargetTemperature",
        json={"targetTemperature": 22},
    )
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["targetTemperature"] == 22


def test_wot_temperature_out_of_range(wot):
    resp = wot.post(
        "/thermostat/actions/setTargetTemperature",
        json={"targetTemperature": 50},
    )
    assert resp.status_code == 422


def test_wot_set_brightness(wot):
    resp = wot.post("/lights/actions/setBrightness", json={"brightness": 40})
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data["brightness"] == 40


def test_wot_projector_on(wot):
    resp = wot.post("/projector/actions/setPower", json={"power": "on"})
    assert resp.status_code == 200


def test_wot_readiness_after_full_setup(wot):
    wot.post("/projector/actions/setPower", json={"power": "on"})
    wot.post("/thermostat/actions/setTargetTemperature", json={"targetTemperature": 22})
    wot.post("/lights/actions/setBrightness", json={"brightness": 40})
    resp = wot.get("/readiness")
    data = json.loads(resp.data)
    assert data["ready"] is True


def test_wot_reset(wot):
    wot.post("/projector/actions/setPower", json={"power": "on"})
    wot.post("/api/reset")
    resp = wot.get("/projector/properties/power")
    data = json.loads(resp.data)
    assert data == "off"
