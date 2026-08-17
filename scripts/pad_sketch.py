"""Render the ambient pad sketch, then measure it.

Scratch driver, preapproved as `scripts/*` so it can be edited freely without
re-prompting. This predates the recipe format -- the chord table below moves
into a recipe file once that format lands.

    uv run python scripts/pad_sketch.py out/pad.wav
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

import supriya
from supriya import Score

from scpad.analysis import measure, measure_seam, to_db
from scpad.synths import midi_to_hz, pad, reverb, wrap_phase
from scpad.wavio import read_wav

SAMPLE_RATE = 48000
CHORD_SECONDS = 24.0
ATTACK = 7.0
RELEASE = 10.0
REVERB_DECAY = 7.0
REVERB_BUS = 2
# Set by measurement: at 0.085 the render peaked at -20.6 dBFS. The whole chain
# is linear (no saturation anywhere), so peak scales with this directly. 0.40
# targets roughly -7 dBFS, leaving headroom without touching a limiter -- a
# limiter would mask exactly the clipping we want the tests to catch.
NOTE_AMPLITUDE = 0.40

# Dmaj7 -> Bm7 -> Gmaj7 -> A6sus. Voiced to descend then lift, so wrapping from
# the last chord back to the first is a step rather than a jump.
CHORDS = (
    (50, 57, 66, 73),
    (47, 54, 62, 69),
    (43, 50, 59, 66),
    (45, 52, 61, 71),
)
# Fixed pan positions per voice index. Constant, not random.
PANS = (-0.55, 0.35, -0.25, 0.6)


def build_score() -> tuple[Score, float]:
    score = Score(input_bus_channel_count=0, output_bus_channel_count=2)
    loop_seconds = CHORD_SECONDS * len(CHORDS)
    # Let the final release and reverb tail ring out instead of truncating.
    # This is NOT a loop-safe render: the tail is appended, not wrapped.
    # Measured: the tail is under -60 dBFS ~5s after the release ends, so we
    # stop short of the full RT60 rather than write seconds of dead air.
    total = loop_seconds + RELEASE + 5.0

    with score.at(0):
        score.add_synthdefs(pad, reverb)
        sources = score.add_group()
        effects = score.add_group(
            add_action=supriya.AddAction.ADD_AFTER, target_node=sources
        )
        score.add_synth(
            reverb,
            target_node=effects,
            in_bus=REVERB_BUS,
            out=0,
            decay=REVERB_DECAY,
        )

    gates: list[tuple[float, object]] = []
    for chord_index, chord in enumerate(CHORDS):
        start = chord_index * CHORD_SECONDS
        with score.at(start):
            for voice_index, note in enumerate(chord):
                synth = score.add_synth(
                    pad,
                    target_node=sources,
                    out=REVERB_BUS,
                    frequency=midi_to_hz(note),
                    amplitude=NOTE_AMPLITUDE,
                    pan=PANS[voice_index],
                    attack=ATTACK,
                    release=RELEASE,
                    # Stagger the sweep per voice so filter motion smears across
                    # the chord instead of pumping in lockstep.
                    sweep_phase=wrap_phase(chord_index * 0.7 + voice_index * 1.6),
                )
                gates.append((start + CHORD_SECONDS, synth))

    # Releasing at the next chord's downbeat means a 10s release overlaps a 7s
    # attack: the crossfade is the overlap, not a separate fade.
    for release_at, synth in gates:
        with score.at(release_at):
            synth.set(gate=0.0)

    with score.at(total):
        score.do_nothing()
    return score, total


async def main() -> int:
    out_path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "out/pad.wav")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)

    score, total = build_score()
    print(f"rendering {total:.1f}s -> {out_path}")
    _, exit_code = await score.render(
        out_path,
        duration=total,
        header_format=supriya.HeaderFormat.WAV,
        sample_format=supriya.SampleFormat.INT24,
        sample_rate=SAMPLE_RATE,
    )
    if exit_code != 0 or not out_path.exists():
        print(f"FAIL: scsynth exit={exit_code} exists={out_path.exists()}")
        return 1

    audio = read_wav(out_path)
    metrics = measure(audio)
    print()
    print(metrics.describe())
    print()
    print(measure_seam(audio).describe())
    print()
    profile = metrics.rms_profile
    print("rms per second (dBFS):")
    for offset in range(0, len(profile), 10):
        chunk = profile[offset : offset + 10]
        cells = " ".join(f"{to_db(v):6.1f}" for v in chunk)
        print(f"  {offset:>4}s {cells}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
