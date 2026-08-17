"""Assemble a one-shot into something you can actually judge.

A 0.35s earcon played once tells you almost nothing. Three questions decide
whether it ships, and none of them survive a single trigger in isolation:

  1. Does it read as one gesture, or as a click followed by some noise?
  2. Does it survive a fast retrigger? Callscape fires this off a stick press,
     and a stick gets pressed twice.
  3. Does it cut through the flight bed it will usually land on top of?

So this stitches all three into one file and plays it. Output is throwaway --
it is a listening aid, not an artifact, and nothing in the render or test path
knows it exists.

    uv run doit audition
"""

from __future__ import annotations

import sys
import wave
from pathlib import Path

import numpy as np

from scpad.wavio import read_wav

OUT = Path("out")
BED = (
    Path.home()
    / "Projects"
    / "callscape"
    / "web"
    / "public"
    / "sounds"
    / "flight-slow.wav"
)


def silence(seconds: float, rate: int) -> np.ndarray:
    return np.zeros(int(seconds * rate))


def write_wav(path: Path, mono: np.ndarray, rate: int) -> Path:
    # Clip rather than normalize: the point of the bed section is to hear the
    # real summed level, and quietly scaling it down would hide a problem.
    clipped = np.clip(mono, -1.0, 1.0)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((clipped * 32767.0).astype("<i2").tobytes())
    return path


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else OUT / "flip.wav"
    audio = read_wav(source)
    rate = audio.sample_rate
    flip = audio.samples.mean(axis=1)

    parts = [
        silence(0.3, rate),
        # Three singles, spaced far enough apart to hear each as a whole.
        flip,
        silence(0.7, rate),
        flip,
        silence(0.7, rate),
        flip,
        silence(1.0, rate),
    ]

    # Retrigger: two 0.25s apart, which is faster than the 0.2s camera spin can
    # realistically be repeated but not by much.
    gap = int(0.25 * rate)
    burst = np.zeros(gap + len(flip))
    burst[: len(flip)] += flip
    burst[gap : gap + len(flip)] += flip
    parts += [burst, silence(1.2, rate)]

    if BED.exists():
        bed_audio = read_wav(BED)
        if bed_audio.sample_rate != rate:
            print(f"skipping bed section: {BED.name} is {bed_audio.sample_rate}Hz")
        else:
            bed = np.tile(bed_audio.samples.mean(axis=1), 2)
            mixed = bed.copy()
            # Land the flip a second in, so there is bed before and after it.
            at = int(1.0 * rate)
            mixed[at : at + len(flip)] += flip
            parts += [mixed, silence(0.5, rate)]
    else:
        print(f"no bed at {BED}; skipping the in-context section")

    montage = np.concatenate(parts)
    output = write_wav(OUT / "flip-audition.wav", montage, rate)
    print(f"{output}  {len(montage) / rate:.2f}s")
    print(f"peak        {np.abs(montage).max():.3f}")
    print("layout      3 singles, a 0.25s retrigger, then one over the flight bed")

    from scpad.playback import play

    print(f"played via Windows: {play(output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
