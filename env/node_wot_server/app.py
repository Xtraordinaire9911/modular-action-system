"""Minimal Flask mock for the WoT device server.

Exposes stateful HTTP endpoints that match the hrefs in config/wot_td/*.td.json.
Environment state resets to a configurable initial condition via POST /api/reset.

Run with:  python env/node_wot_server/app.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request  # type: ignore

app = Flask(__name__)

_INITIAL_STATE: dict[str, Any] = {
    "thermostat": {"targetTemperature": 20, "currentTemperature": 19},
    "lights": {"brightness": 100},
    "projector": {"power": "off"},
    "readiness": {"ready": False},
}

_state: dict[str, Any] = copy.deepcopy(_INITIAL_STATE)

# ── Reset ─────────────────────────────────────────────────────────────────────


@app.post("/api/reset")
def reset():
    global _state
    override = request.get_json(silent=True) or {}
    _state = copy.deepcopy(_INITIAL_STATE)
    for device, values in override.items():
        if device in _state:
            _state[device].update(values)
    _update_readiness()
    return jsonify({"status": "reset", "state": _state})


# ── Thermostat ────────────────────────────────────────────────────────────────


@app.get("/thermostat/properties/targetTemperature")
def get_target_temperature():
    return jsonify(_state["thermostat"]["targetTemperature"])


@app.get("/thermostat/properties/currentTemperature")
def get_current_temperature():
    return jsonify(_state["thermostat"]["currentTemperature"])


@app.post("/thermostat/actions/setTargetTemperature")
def set_target_temperature():
    body = request.get_json(force=True)
    value = body.get("targetTemperature", body.get("value"))
    if value is None or not (16 <= float(value) <= 30):
        return jsonify({"error": "value out of range [16, 30]"}), 422
    _state["thermostat"]["targetTemperature"] = float(value)
    _state["thermostat"]["currentTemperature"] = float(value)
    _update_readiness()
    return jsonify({"targetTemperature": _state["thermostat"]["targetTemperature"]})


# ── Lighting ──────────────────────────────────────────────────────────────────


@app.get("/lights/properties/brightness")
def get_brightness():
    return jsonify(_state["lights"]["brightness"])


@app.post("/lights/actions/setBrightness")
def set_brightness():
    body = request.get_json(force=True)
    value = body.get("brightness", body.get("value"))
    if value is None or not (0 <= int(value) <= 100):
        return jsonify({"error": "brightness must be 0–100"}), 422
    _state["lights"]["brightness"] = int(value)
    _update_readiness()
    return jsonify({"brightness": _state["lights"]["brightness"]})


# ── Projector ─────────────────────────────────────────────────────────────────


@app.get("/projector/properties/power")
def get_projector_power():
    return jsonify(_state["projector"]["power"])


@app.post("/projector/actions/setPower")
def set_projector_power():
    body = request.get_json(force=True)
    value = body.get("power", body.get("value"))
    if value not in ("on", "off"):
        return jsonify({"error": "power must be 'on' or 'off'"}), 422
    _state["projector"]["power"] = value
    _update_readiness()
    return jsonify({"power": _state["projector"]["power"]})


# ── Readiness checker ─────────────────────────────────────────────────────────


@app.get("/readiness")
def get_readiness():
    _update_readiness()
    return jsonify(_state["readiness"])


def _update_readiness() -> None:
    _state["readiness"]["ready"] = (
        _state["projector"]["power"] == "on"
        and 20 <= _state["thermostat"]["targetTemperature"] <= 24
        and _state["lights"]["brightness"] <= 60
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
