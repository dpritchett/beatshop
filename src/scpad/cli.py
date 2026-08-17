"""Thin CLI. The recipes and the SynthDefs are the product; this is plumbing.

`render` arrives with the recipe format. `measure` and `play` exist now because
verification and listening are needed from the first sound onward.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .analysis import measure, measure_seam
from .wavio import read_wav


def _cmd_measure(args: argparse.Namespace) -> int:
    audio = read_wav(args.wav)
    metrics = measure(audio)
    print(f"{args.wav}")
    print(f"header      chunks={list(audio.chunk_ids)} depth={audio.bit_depth}-bit")
    print(metrics.describe())
    if args.seam:
        print()
        print(measure_seam(audio).describe())
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

    p_measure = sub.add_parser("measure", help="report numeric defects in a WAV")
    p_measure.add_argument("wav", type=Path)
    p_measure.add_argument(
        "--seam", action="store_true", help="also report loop-point discontinuity"
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
