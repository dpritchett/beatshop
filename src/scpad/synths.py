"""SynthDefs, defined in Python. No sclang anywhere in this project.

Determinism rule for everything in this module: no Rand, no noise UGens, no
UGen whose output depends on server RNG state. scsynth seeds its RNG from the
wall clock, so a single Rand() would make renders non-reproducible -- that is
exactly why SuperCollider's own stock `default` SynthDef cannot be rendered
twice to the same bytes. Any variation we want is computed in Python from the
recipe seed and passed in as a synth parameter.

The pad is a factory rather than a module-level SynthDef because the number of
detuned voices changes the shape of the UGen graph, so it has to be fixed when
the graph is built rather than passed in at note-on.
"""

from __future__ import annotations

import functools
import math
import operator
from collections.abc import Sequence

from supriya import Envelope, synthdef
from supriya.ugens import (
    LPF,
    AllpassC,
    CombC,
    DelayC,
    EnvGen,
    In,
    LeakDC,
    LinExp,
    Out,
    Pan2,
    SinOsc,
    VarSaw,
)

TWO_PI = 2.0 * math.pi
# Golden angle. Successive multiples never repeat and spread as evenly as an
# irrational rotation can, which decorrelates any number of voices without
# reaching for a random number generator.
GOLDEN_ANGLE = TWO_PI * (1.0 - 1.0 / ((1.0 + 5.0**0.5) / 2.0))


def _sum(items):
    return functools.reduce(operator.add, items)


def default_voice_phases(count: int) -> tuple[float, ...]:
    """Starting phase per detuned voice, deterministic in the voice index."""
    return tuple((index * GOLDEN_ANGLE) % TWO_PI for index in range(count))


def build_pad(
    detune_cents: Sequence[float],
    voice_phases: Sequence[float] | None = None,
):
    """Build the pad SynthDef for a given detune stack.

    `detune_cents` is in cents, not semitones: the voices must beat against each
    other slowly enough to read as one thickened tone rather than as a chord.
    """
    cents = tuple(float(value) for value in detune_cents)
    phases = tuple(
        float(value) for value in (voice_phases or default_voice_phases(len(cents)))
    )
    if len(phases) != len(cents):
        raise ValueError(
            f"{len(cents)} detune values but {len(phases)} voice phases; they must match"
        )

    @synthdef()
    def pad(
        out=0,
        frequency=220.0,
        amplitude=0.08,
        gate=1.0,
        attack=7.0,
        release=10.0,
        pan=0.0,
        cutoff_lo=260.0,
        cutoff_hi=1500.0,
        sweep_frequency=1.0 / 31.0,
        sweep_phase=0.0,
        width=0.32,
    ):
        env = EnvGen.kr(
            envelope=Envelope.asr(attack_time=attack, release_time=release, curve=-2.5),
            gate=gate,
            done_action=2,
        )
        # The cutoff drifts on its own period, deliberately not a factor of the
        # chord length, so the filter never lands the same way twice in a loop.
        lfo = SinOsc.kr(frequency=sweep_frequency, phase=sweep_phase)
        cutoff = LinExp.kr(
            source=lfo,
            input_minimum=-1.0,
            input_maximum=1.0,
            output_minimum=cutoff_lo,
            output_maximum=cutoff_hi,
        )
        voices = [
            VarSaw.ar(
                frequency=frequency * (2.0 ** (detune / 1200.0)),
                initial_phase=phase,
                width=width,
            )
            for detune, phase in zip(cents, phases)
        ]
        signal = _sum(voices) * (1.0 / len(voices))
        # Two cascaded 2-pole sections: a 4-pole slope with no resonance peak.
        # Resonance would put a moving formant in the sound, which draws the ear
        # exactly where this music does not want it.
        signal = LPF.ar(source=LPF.ar(source=signal, frequency=cutoff), frequency=cutoff)
        Out.ar(bus=out, source=Pan2.ar(source=signal * env * amplitude, position=pan))

    return pad


# Schroeder tank delay times, in seconds. Mutually non-harmonic so the comb
# resonances do not stack into an audible pitch; the L/R offset is what makes
# the tail wide rather than a point source between the speakers.
_COMB_L = (0.0297, 0.0371, 0.0411, 0.0437, 0.0532, 0.0619)
_COMB_R = (0.0313, 0.0389, 0.0427, 0.0451, 0.0551, 0.0641)
_ALLPASS_L = (0.0050, 0.0121, 0.0170)
_ALLPASS_R = (0.0057, 0.0131, 0.0186)


@synthdef()
def reverb(in_bus=2, out=0, decay=7.0, damping=2400.0, mix=0.45, predelay=0.04):
    """Stereo Schroeder reverb.

    Hand-rolled rather than FreeVerb because blurring the boundary between
    chords needs a multi-second tail and FreeVerb tops out well short of that.
    CombC takes RT60 directly, so tail length is a dial and not a guess.
    """
    dry = In.ar(bus=in_bus, channel_count=2)
    source = DelayC.ar(
        source=LPF.ar(source=(dry[0] + dry[1]) * 0.5, frequency=damping),
        maximum_delay_time=0.2,
        delay_time=predelay,
    )

    def tank(comb_times, allpass_times):
        combs = [
            CombC.ar(
                source=source,
                maximum_delay_time=0.12,
                delay_time=time,
                decay_time=decay,
            )
            for time in comb_times
        ]
        wet = _sum(combs) * (1.0 / len(combs))
        for time in allpass_times:
            wet = AllpassC.ar(
                source=wet, maximum_delay_time=0.06, delay_time=time, decay_time=0.9
            )
        return LPF.ar(source=wet, frequency=damping)

    wet = (tank(_COMB_L, _ALLPASS_L), tank(_COMB_R, _ALLPASS_R))
    # LeakDC because a long feedback tank accumulates a DC term, and DC offset
    # is one of the defects the tests assert against.
    Out.ar(
        bus=out,
        source=[LeakDC.ar(source=d * (1.0 - mix) + w * mix) for d, w in zip(dry, wet)],
    )


def midi_to_hz(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))
