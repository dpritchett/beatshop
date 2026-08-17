"""Thin CLI. The recipes and the SynthDefs are the product; this is plumbing.

`render` turns a recipe into a WAV and reports its metrics. `measure` re-checks
an existing WAV. `play` hands a file to Windows for listening and is never part
of verification.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import measure, measure_seam, measure_sweep
from .recipe import RecipeError, load
from .wavio import read_wav


def _cmd_render(args: argparse.Namespace) -> int:
    from .render import render

    try:
        recipe = load(args.recipe)
    except RecipeError as exc:
        print(f"recipe error: {exc}", file=sys.stderr)
        return 2

    output = render(recipe, args.output)
    audio = read_wav(output)
    metrics = measure(audio)
    print(f"{output}  {output.stat().st_size} bytes")
    print(metrics.describe())
    if metrics.clipped_samples:
        print("FAIL: output clips", file=sys.stderr)
        return 1
    if metrics.silent:
        print("FAIL: output is silent", file=sys.stderr)
        return 1
    return 0


def _cmd_measure(args: argparse.Namespace) -> int:
    audio = read_wav(args.wav)
    metrics = measure(audio)
    print(f"{args.wav}")
    print(f"header      chunks={list(audio.chunk_ids)} depth={audio.bit_depth}-bit")
    print(metrics.describe())
    if args.seam:
        print()
        print(measure_seam(audio).describe())
    if args.sweep:
        print()
        print(measure_sweep(audio).describe())
    return 1 if metrics.silent or metrics.clipped_samples else 0


def _cmd_play(args: argparse.Namespace) -> int:
    # Imported lazily: playback is quarantined from the render/verify path and
    # should never be a hard dependency of the rest of the CLI.
    from .playback import PlaybackError, play

    try:
        staged = play(args.wav, wait=not args.no_wait)
    except PlaybackError as exc:
        print(f"playback failed: {exc}", file=sys.stderr)
        return 1
    print(f"played via Windows: {staged}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scpad", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_render = sub.add_parser("render", help="render a recipe to a WAV")
    p_render.add_argument("recipe", type=Path)
    p_render.add_argument("output", type=Path)
    p_render.set_defaults(func=_cmd_render)

    p_measure = sub.add_parser("measure", help="report numeric defects in a WAV")
    p_measure.add_argument("wav", type=Path)
    p_measure.add_argument(
        "--seam", action="store_true", help="also report loop-point discontinuity"
    )
    p_measure.add_argument(
        "--sweep",
        action="store_true",
        help="also report where a one-shot peaks and how far its filter travels",
    )
    p_measure.set_defaults(func=_cmd_measure)

    p_play = sub.add_parser(
        "play", help="play a WAV through Windows audio (never a WSL device)"
    )
    p_play.add_argument("wav", type=Path)
    p_play.add_argument(
        "--no-wait", action="store_true", help="return immediately, keep playing"
    )
    p_play.set_defaults(func=_cmd_play)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
