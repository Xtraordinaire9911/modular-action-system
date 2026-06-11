"""Minimal Flask mock for the React booking dashboard.

Exposes server-rendered HTML with semantic elements and ARIA attributes
so both DOM and visual grounding are feasible. Environment state resets
via POST /api/reset (shared with the WoT server via a shared in-process
state dict when both run in the same process; otherwise hit /api/reset
independently).

Run with:  python env/react_dashboard/app.py
"""

from __future__ import annotations

import copy
from typing import Any

from flask import Flask, jsonify, redirect, render_template_string, request  # type: ignore

app = Flask(__name__)

_INITIAL_BOOKINGS: dict[str, dict[str, Any]] = {
    "A": {"booked": False, "time": None},
    "B": {"booked": False, "time": None},
    "C": {"booked": False, "time": None},
}

_bookings: dict[str, dict[str, Any]] = copy.deepcopy(_INITIAL_BOOKINGS)

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Room Booking</title></head>
<body>
  <h1>Smart Room Booking</h1>
  <form id="booking-form" method="post" action="/book">
    <label for="room-input">Room</label>
    <input id="room-input" name="room" type="text" placeholder="Room ID"
           aria-label="Room ID" required />
    <label for="time-input">Time</label>
    <input id="time-input" name="time" type="text" placeholder="HH:MM"
           aria-label="Time slot" required />
    <button id="book-room" type="submit" aria-label="Book Room">Book Room</button>
  </form>
  {% if message %}
  <div id="booking-status" class="{{ status_class }}">{{ message }}</div>
  {% endif %}
  <table id="room-status">
    <thead><tr><th>Room</th><th>Booked</th><th>Time</th></tr></thead>
    <tbody>
    {% for room, info in bookings.items() %}
      <tr>
        <td>{{ room }}</td>
        <td>{{ "Yes" if info.booked else "No" }}</td>
        <td>{{ info.time or "-" }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</body>
</html>"""


@app.get("/")
def index():
    return render_template_string(_DASHBOARD_HTML, bookings=_bookings, message=None)


@app.post("/book")
def book():
    room = request.form.get("room", "").strip().upper()
    time_slot = request.form.get("time", "").strip()
    if room not in _bookings:
        return render_template_string(
            _DASHBOARD_HTML,
            bookings=_bookings,
            message=f"Room {room!r} does not exist.",
            status_class="booking-error",
        )
    _bookings[room]["booked"] = True
    _bookings[room]["time"] = time_slot
    return render_template_string(
        _DASHBOARD_HTML,
        bookings=_bookings,
        message=f"Room {room} booked for {time_slot}.",
        status_class="booking-confirmed",
    )


@app.get("/status/<room>")
def room_status(room: str):
    room = room.upper()
    if room not in _bookings:
        return jsonify({"error": "unknown room"}), 404
    return jsonify(_bookings[room])


@app.post("/api/reset")
def reset():
    global _bookings
    _bookings = copy.deepcopy(_INITIAL_BOOKINGS)
    override = request.get_json(silent=True) or {}
    for room, values in override.get("bookings", {}).items():
        if room in _bookings:
            _bookings[room].update(values)
    return jsonify({"status": "reset", "bookings": _bookings})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
