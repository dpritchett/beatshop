"""Scratch driver for tuning a one-shot recipe by measurement.

`scpad measure --sweep` prints the same numbers; this renders first and draws the
envelope, which is the part you actually stare at while moving a dial. Ears are
not available to the test suite, so the shape has to be read off a chart.

    uv run doit tune
    uv run python scripts/whoosh_tune.py recipes/flip.toml
"""

from __future__ import annotations

import sys
from pathlib import Path

from scpad.analysis import measure, measure_sweep, to_db
from scpad.recipe import load
from scpad.render import render
from scpad.wavio import read_wav


def main(argv: list[str]) -> int:
    recipe_path = Path(argv[1]) if len(argv) > 1 else Path("recipes/flip.toml")
    recipe = load(recipe_path)
    output = render(recipe, Path("out") / f"{recipe.name}.wav")

    audio = read_wav(output)
    metrics = measure(audio)
    sweep = measure_sweep(audio)

    print(f"{output}  {output.stat().st_size} bytes")
    print(f"format      {audio.sample_rate}Hz {audio.bit_depth}-bit x{audio.channels}")
    print(metrics.describe())
    print()
    print(sweep.describe())
    print(
        f"peak offset    {sweep.peak_centroid_at - sweep.peak_level_at:+.3f}s "
        "(brightest minus loudest; positive means it goes past you)"
    )

    loudest = max(rms for _, rms, _ in sweep.frames)
    print()
    print("  time     rms        centroid")
    for time, rms, centroid in sweep.frames:
        bar = "#" * int(40 * rms / loudest)
        print(f"  {time:5.3f}  {to_db(rms):7.1f}dB  {centroid:6.0f}Hz  {bar}")

    # The one-shot equivalent of the loop-seam check: a transient that does not
    # start and land on zero clicks every time it is triggered.
    mono = audio.samples.mean(axis=1)
    print()
    print(f"first/last  {mono[0]:+.2e} / {mono[-1]:+.2e}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
