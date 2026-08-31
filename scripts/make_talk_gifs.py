"""Cut the recorded model demo into one looping GIF per section of the talk.

    python scripts/make_talk_gifs.py            # 960px, 10 fps, ~2.4 MB per scene
    python scripts/make_talk_gifs.py 720 8      # smaller, for a slide deck upload limit

A 1m46s recording is the wrong unit for a presentation: a section that needs
thirty seconds of explanation cannot be scrubbed to mid-sentence, and a slide
that autoplays a whole video outruns the speaker. One looping GIF per section
plays for exactly as long as that part of the talk lasts.

Two passes per clip: ``palettegen`` over the clip's own frames with
``stats_mode=diff``, so the 256 colours are spent on what changes rather than on
the static panel, then ``paletteuse`` with Bayer dithering, which for flat UI
colour is both smaller and cleaner than error diffusion.

**The boundaries below belong to one specific recording.** They were read off the
panel header frame by frame, not guessed. Re-record the demo and they are wrong;
re-derive them with:

    ffmpeg -i <video> -filter:v "select='gt(scene,0.2)',showinfo" -f null -

which prints a timestamp per page reload, and then check each candidate by
cropping the header (``crop=640:96:640:0``) at that second.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "artifacts" / "llm_demo" / "llm-vs-rules-smartroom.mp4"
OUT = REPO / "artifacts" / "talk_gifs"

# (start, end, file name) for artifacts/llm_demo/llm-vs-rules-smartroom.mp4.
SECTIONS: tuple[tuple[float, float, str], ...] = (
    (0.6, 27.0, "1-control-rules-and-model-agree"),
    (27.2, 51.0, "2-model-interprets-what-rules-cannot"),
    (51.2, 78.3, "3-goal-the-page-cannot-reach-wot"),
    (78.6, 103.0, "4-dashboard-lies-vision-catches-it"),
    (103.0, 106.3, "5-run-complete-scoreboard"),
)


def make(start: float, end: float, name: str, *, width: int, fps: int) -> Path:
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    OUT.mkdir(parents=True, exist_ok=True)
    palette = OUT / f".{name}.png"
    target = OUT / f"{name}.gif"
    span = ["-ss", str(start), "-t", str(round(end - start, 2)), "-i", str(SRC)]
    chain = f"fps={fps},scale={width}:-1:flags=lanczos"
    subprocess.run(
        [exe, "-y", *span, "-vf", f"{chain},palettegen=stats_mode=diff", str(palette)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            exe,
            "-y",
            *span,
            "-i",
            str(palette),
            "-lavfi",
            f"{chain}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle",
            "-loop",
            "0",
            str(target),
        ],
        check=True,
        capture_output=True,
    )
    palette.unlink(missing_ok=True)
    return target


def main() -> int:
    if not SRC.is_file():
        print(f"no recording at {SRC.relative_to(REPO)}; run scripts/run_llm_demo.py --record first")
        return 2
    width = int(sys.argv[1]) if len(sys.argv) > 1 else 960
    fps = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    for start, end, name in SECTIONS:
        path = make(start, end, name, width=width, fps=fps)
        print(f"{path.name:<44} {end - start:5.1f}s  {path.stat().st_size / 1_048_576:5.2f} MB")
    print(f"\n  {OUT.relative_to(REPO)}  - see its README.md for what each one is for")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
