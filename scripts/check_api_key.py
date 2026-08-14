"""Is the API key configured, and does it work?

    python scripts/check_api_key.py            # check the file and the client
    python scripts/check_api_key.py --call     # also make one real, tiny call

Nothing here prints a key. It reports the shape of the file, which names are set,
which client that resolves to, and - with ``--call`` - whether one minimal
request actually succeeds.

The ``--call`` check exists because "the key is set" and "the key works" are
different facts, and only the second one matters on demo day.
"""

from __future__ import annotations

import argparse
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config.secrets import configured_key_names, describe_local_env  # noqa: E402

_LINE = "=" * 78

# Errors that prove the key authenticated and the account can be served: the
# request reached the service and was rejected on its contents, not on its
# credentials. Worth separating, because "rejected" and "unauthorised" look
# alike in a log and mean opposite things the day before a demo.
_REACHED_THE_SERVICE = ("invalid_parameter", "InvalidParameter", "must be larger")


def probe_png(size: int = 32) -> bytes:
    """A small solid-white PNG, built from the standard library.

    Deliberately not 1x1: qwen-vl rejects any side under 11 pixels with a 400
    that reads like a broken key and is not one. 32x32 is a couple of image
    tokens - the cheapest question a vision model will actually accept - and
    solid white makes "is this blank" a question with a right answer, so a wrong
    reply is distinguishable from a broken pipe.
    """
    row = bytes([0]) + bytes([255]) * (size * 3)  # filter byte, then RGB pixels
    raw = row * size

    def chunk(kind: bytes, payload: bytes) -> bytes:
        crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)

    header = struct.pack(">2I5B", size, size, 8, 2, 0, 0, 0)  # 8-bit RGB, no interlace
    signature = bytes([137, 80, 78, 71, 13, 10, 26, 10])
    return signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


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

    image = probe_png()
    print(f"\n  calling {vision.name} with a 32x32 image ({len(image)} bytes) ...")
    from src.perception.vlm_observer import VlmObserver

    observer = VlmObserver(client=vision, max_calls=1)
    judgement = observer.look(image, "Is this image completely blank? Answer from the image only.", region="32x32")

    print(f"    source      : {judgement.source}")
    print(f"    model       : {judgement.model}")
    if judgement.error:
        print(f"    error       : {judgement.error}")
    else:
        print(f"    answer      : {judgement.answer} at confidence {judgement.confidence:.2f}")
        print(f"    evidence    : {judgement.evidence}")
        print(f"    latency     : {judgement.latency_ms:.0f} ms")

    if judgement.source in {"vlm", "low_confidence"}:
        print("\n  The key works: a real model answered. Low confidence on a blank square is the")
        print("  honest answer, so either source above means the round trip succeeded.\n")
        return 0
    if any(marker in judgement.error for marker in _REACHED_THE_SERVICE):
        print("\n  The key authenticated and the request reached the service; it was rejected on")
        print("  the contents of this probe rather than on credentials. If you see this, the")
        print("  probe needs fixing, not your key.\n")
        return 0
    print("\n  The call did not reach a model. See docs_setup/VLM_SETUP.md section 7.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
