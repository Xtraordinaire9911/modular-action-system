"""Runtime WoT Thing Directory client (W3C WoT Discovery style).

A Thing Directory lets an agent *discover* the Thing Descriptions available in
an environment at runtime instead of hard-coding device names. The node-wot
servient exposes a directory at ``GET /things``; this client fetches that
collection and parses it into ``ThingAffordanceModel`` objects.

This is the mechanism behind "dynamic Thing Description passing between agents":
neither the System-1 perception agent nor the System-2 planning agent needs to
know the device inventory in advance. Each discovers the same TD collection at
runtime and shares the parsed affordance set, so a Thing added to (or removed
from) the directory changes every agent's capabilities with no code edit.

The fetch function is injectable so the client is fully unit-testable offline;
the default uses ``urllib`` (stdlib), matching the rest of the demo runner.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable

from src.perception.td_affordance_parser import TdAffordanceParser, ThingAffordanceModel

JsonFetch = Callable[[str], Any]

DEFAULT_DIRECTORY_URL = "http://localhost:8082"


class ThingDirectoryError(RuntimeError):
    """Raised when the directory is unreachable or exposes no Thing Descriptions."""


def _urllib_get_json(url: str, *, timeout_s: float = 2.0) -> Any:
    with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - local demo endpoint
        return json.loads(response.read().decode("utf-8"))


class ThingDirectoryClient:
    """Discover Thing Descriptions from a runtime WoT directory."""

    def __init__(self, directory_url: str = DEFAULT_DIRECTORY_URL, *, fetch_json: JsonFetch | None = None) -> None:
        base = directory_url.rstrip("/")
        if base.endswith("/things"):
            base = base.removesuffix("/things")
        self._base = base
        self._fetch = fetch_json or _urllib_get_json
        self._parser = TdAffordanceParser()

    def discover_tds(self) -> list[dict[str, Any]]:
        """Return every Thing Description currently registered in the directory."""
        try:
            payload = self._fetch(f"{self._base}/things")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError) as exc:
            raise ThingDirectoryError(f"directory unavailable at {self._base}/things: {exc}") from exc
        tds = self._as_td_list(payload)
        if not tds:
            raise ThingDirectoryError("directory returned no Thing Descriptions")
        return tds

    def discover_models(self) -> list[ThingAffordanceModel]:
        """Discover TDs and parse them into the shared, agent-agnostic affordance view.

        Malformed entries are skipped rather than aborting discovery, mirroring
        ``parse_things`` so one bad TD never blinds an agent to the rest.
        """
        models: list[ThingAffordanceModel] = []
        for td in self.discover_tds():
            try:
                models.append(self._parser.parse(td))
            except Exception:
                continue
        return models

    @staticmethod
    def _as_td_list(payload: Any) -> list[dict[str, Any]]:
        """Accept a bare TD array, a directory collection object, or a single TD."""
        if isinstance(payload, list):
            return [td for td in payload if isinstance(td, dict) and any(k in td for k in ("@context", "title", "id"))]
        if isinstance(payload, dict):
            for key in ("things", "members", "@graph"):
                if isinstance(payload.get(key), list):
                    return [
                        td for td in payload[key] if isinstance(td, dict) and any(k in td for k in ("@context", "title", "id"))
                    ]
            if any(k in payload for k in ("@context", "title", "id")):
                return [payload]
        return []
