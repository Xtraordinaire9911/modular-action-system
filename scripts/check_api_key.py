"""Is the API key configured, and does it work?

    python scripts/check_api_key.py            # check the file and the client
    python scripts/check_api_key.py --call     # also make one real, tiny call

Nothing here prints a key. It reports the shape of the file, which names are set,
which client that resolves to, and - with ``--call`` - whether one minimal
request actually succeeds.

The ``--call`` check exists because "the key is set" and "the key works" are
different facts, and only the second one matters on demo day. It sends a 1x1
image, which is the smallest question a vision model can be asked: a few hundred
tokens, well under a tenth of a cent even at the most expensive provider's rate.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.secrets import configured_key_names, describe_local_env  # noqa: E402

_LINE = "=" * 78

# A 1x1 transparent PNG. Small enough that the call costs nothing worth counting,
# and real enough that a vision endpoint has to accept it as an image.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx" "0gAAAABJRU5ErkJggg=="
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--call", action="store_true", help="Make one minimal real request to prove the key works.")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass

    print(f"\n{_LINE}\n  API KEY CHECK\n{_LINE}")

    print("\n  the key file:")
    for note in describe_local_env():
        print(f"    - {note}")

    names = configured_key_names()
    print(f"\n  names set now : {', '.join(names) if names else 'none'}")

    from src.perception.vlm_observer import available_vision_client
    from src.planner.intent_planner import available_client

    vision = available_vision_client()
    text = available_client()
    print(f"  vision client : {vision.name if vision else 'none - the run will report unavailable'}")
    print(f"  text client   : {text.name if text else 'none - the run will use rule_fallback'}")

    if not args.call:
        print("\n  Nothing was sent anywhere. Add --call to prove the key actually works.\n")
        return 0 if vision is not None else 1

    if vision is None:
        print("\n  No vision client to call. Fix the file first.\n")
        return 1

    print(f"\n  calling {vision.name} with a 1x1 image ...")
    from src.perception.vlm_observer import VlmObserver

    observer = VlmObserver(client=vision, max_calls=1)
    judgement = observer.look(_TINY_PNG, "Is this image blank? Answer from the image only.", region="1x1 probe")

    print(f"    source     : {judgement.source}")
    print(f"    model       : {judgement.model}")
    if judgement.error:
        print(f"    error       : {judgement.error}")
    else:
        print(f"    answer      : {judgement.answer} at confidence {judgement.confidence:.2f}")
        print(f"    latency     : {judgement.latency_ms:.0f} ms")

    if judgement.source in {"vlm", "low_confidence"}:
        print("\n  The key works: a real model answered. Low confidence on a blank 1x1 is expected")
        print("  and is the honest answer, so either source above means the round trip succeeded.\n")
        return 0
    print("\n  The call did not reach a model. See docs_setup/VLM_SETUP.md section 7.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
