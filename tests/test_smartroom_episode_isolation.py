"""A second episode must start from the room, not from what the first one left.

This is the last of the assigned demo requirements that had never been checked
end to end. The isolation machinery existed and was unit-tested against fakes;
what was missing was evidence that a real episode, on the real servient and a
real browser, inherits nothing from the one before it.

It matters for a reason that is not theoretical: a rehearsal followed by a live
demo is exactly two episodes back to back. If the second inherits the first's
device state, every device verification in it confirms a value the rehearsal
already put there, and the demo proves nothing while looking identical to one
that proves everything.

Three kinds of state, because "isolated" is three separate claims:

* **device** - a Thing written during the first episode
* **browser** - storage written into the first episode's context
* **episode identity** - two runs must be distinguishable in a trace

Marked ``smartroom``: none of this can be asserted without the servient, and a
fake that returned whatever it was told would pass all of it.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

import pytest

from src.isolation.episode import BrowserWotIsolationProvider
from src.runtime.episode import EpisodeContext, EpisodePolicy
from src.runtime.live_environment import SmartRoomControlClient, ThreadedBrowserSession

DASHBOARD = "http://localhost:3000"
WOT = "http://localhost:8080"
CONTROL = "http://localhost:8081"
TIMEOUT = 3.0

pytestmark = pytest.mark.smartroom


def _read_device(thing: str, prop: str):
    request = urllib.request.Request(f"{WOT}/{thing}/properties/{prop}", headers={"X-API-Key": "demo"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - local demo
        return json.loads(response.read().decode("utf-8"))


def _write_device(thing: str, prop: str, value) -> None:
    request = urllib.request.Request(
        f"{WOT}/{thing}/properties/{prop}",
        method="PUT",
        data=json.dumps(value).encode("utf-8"),
        headers={"Content-Type": "application/json", "X-API-Key": "demo"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT):  # noqa: S310 - local demo
        pass


@pytest.fixture
def room_is_up():
    try:
        urllib.request.urlopen(f"{CONTROL}/state", timeout=TIMEOUT).close()  # noqa: S310
        urllib.request.urlopen(DASHBOARD, timeout=TIMEOUT).close()  # noqa: S310
    except (urllib.error.URLError, OSError) as exc:  # pragma: no cover - env guard
        pytest.skip(f"the smart room is not running: {exc}")


def test_a_second_episode_inherits_no_device_or_browser_state(room_is_up):
    browser = ThreadedBrowserSession(DASHBOARD, headless=True)
    provider = BrowserWotIsolationProvider(browser, SmartRoomControlClient(CONTROL))

    async def scenario() -> dict[str, object]:
        observed: dict[str, object] = {}

        # ── episode one: leave a mark on all three ────────────────────────────
        first = EpisodeContext(task_id="isolation-1", policy=EpisodePolicy())
        session_one = await provider.provision(first)
        try:
            observed["first_episode_id"] = session_one.episode_id
            # Provisioning resets the room, so this is the value it starts from.
            observed["device_at_start_of_first"] = _read_device("thermostat", "targetTemperature")

            _write_device("thermostat", "targetTemperature", 27)
            observed["device_after_first_wrote"] = _read_device("thermostat", "targetTemperature")

            await browser.evaluate("() => localStorage.setItem('left-behind-by', 'episode-one')")
            observed["storage_in_first"] = await browser.evaluate("() => localStorage.getItem('left-behind-by')")
        finally:
            await provider.dispose(session_one)

        # ── episode two: nothing above may still be true ──────────────────────
        second = EpisodeContext(task_id="isolation-2", policy=EpisodePolicy())
        session_two = await provider.provision(second)
        try:
            observed["second_episode_id"] = session_two.episode_id
            observed["device_at_start_of_second"] = _read_device("thermostat", "targetTemperature")
            observed["storage_in_second"] = await browser.evaluate("() => localStorage.getItem('left-behind-by')")
        finally:
            await provider.dispose(session_two)

        return observed

    try:
        seen = asyncio.run(scenario())
    finally:
        asyncio.run(browser.close())

    # The first episode really did change the device, or the rest proves nothing.
    assert seen["device_after_first_wrote"] == 27, "the first episode never actually wrote to the device"
    assert seen["storage_in_first"] == "episode-one", "the first episode never actually wrote to storage"

    # And the second episode starts from the room rather than from that.
    assert seen["device_at_start_of_second"] == seen["device_at_start_of_first"], (
        f"the second episode inherited device state: started at "
        f"{seen['device_at_start_of_second']!r}, first started at {seen['device_at_start_of_first']!r}"
    )
    assert seen["device_at_start_of_second"] != 27, "the second episode inherited the first episode's setpoint"
    assert seen["storage_in_second"] is None, "the second episode inherited the first episode's browser storage"

    # Two runs have to be tellable apart in a trace, or the ledger cannot show
    # which episode any given transition belonged to.
    assert seen["first_episode_id"] != seen["second_episode_id"]


def test_the_room_is_restored_after_an_episode_rather_than_left_as_the_episode_ended(room_is_up):
    """Dispose restores the state captured when the episode was provisioned.

    Separate from the test above, which is about what an episode *starts* from.
    This is about what it leaves behind for anything that is not an isolated
    episode - a demo run, a manual look at the dashboard - so an episode cannot
    quietly reconfigure the room for whatever comes next.
    """
    _write_device("thermostat", "targetTemperature", 19)
    before = _read_device("thermostat", "targetTemperature")
    assert before == 19

    browser = ThreadedBrowserSession(DASHBOARD, headless=True)
    provider = BrowserWotIsolationProvider(browser, SmartRoomControlClient(CONTROL))

    async def scenario() -> None:
        episode = EpisodeContext(task_id="isolation-restore", policy=EpisodePolicy())
        session = await provider.provision(episode)
        try:
            _write_device("thermostat", "targetTemperature", 28)
        finally:
            await provider.dispose(session)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(browser.close())

    assert (
        _read_device("thermostat", "targetTemperature") == before
    ), "the episode left the room reconfigured instead of restoring what it found"
