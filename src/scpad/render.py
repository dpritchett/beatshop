"""Recipe -> Score -> WAV.

Nothing here reads the clock, and nothing calls the global `random` module.
Every value that varies is either a constant, derived from the note/voice index,
or drawn from a `random.Random` seeded solely from the recipe.
"""

from __future__ import annotations

import asyncio
import math
import random
from pathlib import Path

import supriya
from supriya import Score

from .recipe import Recipe
from .synths import TWO_PI, build_pad, build_whoosh, midi_to_hz, noise_samples, reverb

# Private audio bus where pad voices sum before the reverb reads them. Buses 0/1
# are the stereo output, and the score declares zero inputs, so 2 is the first
# bus that is ours.
REVERB_BUS = 2

# Samples per `/b_setn`. The noise buffer is far too large for one request, and
# while an NRT score is read from a file rather than a socket, scsynth still
# parses one OSC packet at a time. 1024 floats is about 4 KB, comfortably inside
# anything scsynth will accept, and the chunk count never reaches three digits.
BUFFER_CHUNK = 1024

# Extra buffer beyond the requested duration, in control blocks. scsynth renders
# whole 64-sample blocks, so the output always runs slightly long; PlayBuf with
# loop=0 holds its final sample rather than zeroing, and holding a nonzero noise
# sample past the end of the envelope is a DC step. Cheaper to over-allocate.
BUFFER_OVERSHOOT_BLOCKS = 4
CONTROL_BLOCK = 64

# Filter-sweep stagger. Offsetting per chord and per voice smears the filter
# motion across the stack instead of letting every note pump in lockstep. These
# are irregular on purpose -- round numbers would resynchronize.
SWEEP_STAGGER_CHORD = 0.7
SWEEP_STAGGER_VOICE = 1.6

_SAMPLE_FORMATS = {
    16: supriya.SampleFormat.INT16,
    24: supriya.SampleFormat.INT24,
    32: supriya.SampleFormat.INT32,
}


def sweep_phases(recipe: Recipe) -> list[list[float]]:
    """Per-chord, per-voice filter sweep offsets.

    With `phase_jitter` at 0 this is a pure function of the indices and the seed
    is unused. Above 0 the scatter comes from a `random.Random` seeded only by
    the recipe, so a given seed always produces the same offsets. CPython's
    Mersenne Twister and `uniform()` are stable across versions, which is what
    lets a seed mean the same thing on another machine.
    """
    rng = random.Random(recipe.seed)
    jitter = recipe.pad.phase_jitter
    phases = []
    for chord_index, chord in enumerate(recipe.progression.chords):
        row = []
        for voice_index in range(len(chord)):
            offset = (
                chord_index * SWEEP_STAGGER_CHORD + voice_index * SWEEP_STAGGER_VOICE
            )
            if jitter:
                offset += rng.uniform(-jitter, jitter)
            row.append(offset % TWO_PI)
        phases.append(row)
    return phases


def build_whoosh_score(recipe: Recipe) -> Score:
    """Score for a one-shot recipe: one noise buffer, one synth, mono out.

    Mono is not a dial. The source is a single noise buffer with no stereo
    information in it, callscape's earcons are all mono, and a Pan2 at centre
    would only double the file size to say the same thing twice.
    """
    spec = recipe.whoosh
    assert spec is not None  # build_score dispatches on this
    whoosh = build_whoosh(
        duration=spec.duration,
        attack_fraction=spec.attack_fraction,
        sweep_peak_at=spec.sweep_peak_at,
        cutoff_start=spec.cutoff_start,
        cutoff_peak=spec.cutoff_peak,
        cutoff_end=spec.cutoff_end,
        partials=spec.partials,
    )
    frame_count = (
        math.ceil(spec.duration * recipe.render.sample_rate / CONTROL_BLOCK)
        + BUFFER_OVERSHOOT_BLOCKS
    ) * CONTROL_BLOCK
    samples = noise_samples(recipe.seed, frame_count, spec.noise_tilt)

    score = Score(input_bus_channel_count=0, output_bus_channel_count=1)
    with score.at(0):
        score.add_synthdefs(whoosh)
        buffer_ = score.add_buffer(channel_count=1, frame_count=frame_count)
        for start in range(0, frame_count, BUFFER_CHUNK):
            score.set_buffer_range(
                buffer_, start, samples[start : start + BUFFER_CHUNK]
            )
        score.add_synth(
            whoosh,
            out=0,
            buffer_id=buffer_,
            amplitude=spec.amplitude,
            body_mix=spec.body_mix,
        )
    with score.at(recipe.total_seconds):
        score.do_nothing()
    return score


def build_score(recipe: Recipe) -> Score:
    recipe.validate()
    if recipe.is_whoosh:
        return build_whoosh_score(recipe)
    pad = build_pad(recipe.pad.detune_cents, recipe.pad.voice_phases)
    phases = sweep_phases(recipe)
    score = Score(input_bus_channel_count=0, output_bus_channel_count=2)

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
            decay=recipe.reverb.decay,
            damping=recipe.reverb.damping,
            mix=recipe.reverb.mix,
            predelay=recipe.reverb.predelay,
        )

    gates = []
    for chord_index, chord in enumerate(recipe.progression.chords):
        start = chord_index * recipe.progression.chord_seconds
        with score.at(start):
            for voice_index, note in enumerate(chord):
                synth = score.add_synth(
                    pad,
                    target_node=sources,
                    out=REVERB_BUS,
                    frequency=midi_to_hz(note),
                    amplitude=recipe.pad.amplitude,
                    pan=recipe.pad.pans[voice_index],
                    attack=recipe.pad.attack,
                    release=recipe.pad.release,
                    cutoff_lo=recipe.pad.cutoff_lo,
                    cutoff_hi=recipe.pad.cutoff_hi,
                    sweep_frequency=1.0 / recipe.pad.sweep_period,
                    sweep_phase=phases[chord_index][voice_index],
                    width=recipe.pad.width,
                )
                gates.append((start + recipe.progression.chord_seconds, synth))

    # Releasing on the next chord's downbeat makes the overlap itself the
    # crossfade: a long release runs underneath the next chord's slow attack,
    # so there is never a moment where nothing is sounding.
    for release_at, synth in gates:
        with score.at(release_at):
            synth.set(gate=0.0)

    with score.at(recipe.total_seconds):
        score.do_nothing()
    return score


async def render_async(recipe: Recipe, output_path: str | Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # scsynth appends to an existing file rather than truncating it, so a stale
    # render must be removed or the output silently grows.
    output_path.unlink(missing_ok=True)

    _, exit_code = await build_score(recipe).render(
        output_path,
        duration=recipe.total_seconds,
        header_format=supriya.HeaderFormat.WAV,
        sample_format=_SAMPLE_FORMATS[recipe.render.bit_depth],
        sample_rate=recipe.render.sample_rate,
    )
    if exit_code != 0:
        raise RuntimeError(f"scsynth exited {exit_code} rendering {recipe.name}")
    if not output_path.exists():
        raise RuntimeError(f"scsynth wrote no output for {recipe.name}")
    return output_path


def render(recipe: Recipe, output_path: str | Path) -> Path:
    return asyncio.run(render_async(recipe, output_path))
