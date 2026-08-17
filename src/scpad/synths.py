"""SynthDefs, defined in Python. No sclang anywhere in this project.

Determinism rule for everything in this module: no Rand, no noise UGens, no
UGen whose output depends on server RNG state. scsynth seeds its RNG from the
wall clock, so a single Rand() would make renders non-reproducible -- that is
exactly why SuperCollider's own stock `default` SynthDef cannot be rendered
twice to the same bytes. Any variation we want is computed in Python from a
recipe seed and passed in as a parameter.
"""

from __future__ import annotations

import functools
import operator

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

# Detune spread in cents. Irregular spacing so the voices never settle into a
# single fat unison; a symmetric spread beats audibly, which reads as rhythm.
DETUNE_CENTS = (-7.0, -2.5, 3.5, 8.0)
# Fixed starting phases, one per detuned voice. Constant, not random: this is
# what decorrelates the stack without touching the server RNG.
VOICE_PHASES = (0.0, 0.87, 1.93, 2.71)

_TWO_PI = 6.283185307179586


def _sum(items):
    return functools.reduce(operator.add, items)


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
    """One pad note: four detuned voices under a slowly swept 4-pole lowpass."""
    env = EnvGen.kr(
        envelope=Envelope.asr(attack_time=attack, release_time=release, curve=-2.5),
        gate=gate,
        done_action=2,
    )
    # Cutoff drifts on its own period, deliberately coprime-ish with the chord
    # grid so the filter never lands the same way twice inside one loop.
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
            frequency=frequency * (2.0 ** (cents / 1200.0)),
            initial_phase=phase,
            width=width,
        )
        for cents, phase in zip(DETUNE_CENTS, VOICE_PHASES)
    ]
    sig = _sum(voices) * (1.0 / len(voices))
    # Two cascaded 2-pole sections give a 4-pole slope with no resonance peak.
    # Resonance would put a moving formant in the sound, which draws attention.
    sig = LPF.ar(source=LPF.ar(source=sig, frequency=cutoff), frequency=cutoff)
    Out.ar(bus=out, source=Pan2.ar(source=sig * env * amplitude, position=pan))


# Schroeder tank delay times, in seconds. Mutually non-harmonic so the comb
# resonances do not stack into a pitched ring; the L/R offset is what makes the
# tail wide rather than a point source between the speakers.
_COMB_L = (0.0297, 0.0371, 0.0411, 0.0437, 0.0532, 0.0619)
_COMB_R = (0.0313, 0.0389, 0.0427, 0.0451, 0.0551, 0.0641)
_ALLPASS_L = (0.0050, 0.0121, 0.0170)
_ALLPASS_R = (0.0057, 0.0131, 0.0186)


@synthdef()
def reverb(in_bus=2, out=0, decay=7.0, damping=2400.0, mix=0.45, predelay=0.04):
    """Stereo Schroeder reverb.

    Hand-rolled rather than FreeVerb because we need a multi-second tail to
    blur chord boundaries, and FreeVerb tops out well short of that. CombC
    takes RT60 directly, so tail length is a dial rather than a guess.
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
    # LeakDC because a long feedback tank will accumulate a DC term, and DC
    # offset is one of the defects we assert against.
    Out.ar(
        bus=out,
        source=[
            LeakDC.ar(source=d * (1.0 - mix) + w * mix) for d, w in zip(dry, wet)
        ],
    )


def midi_to_hz(note: float) -> float:
    return 440.0 * (2.0 ** ((note - 69.0) / 12.0))


def wrap_phase(value: float) -> float:
    return value % _TWO_PI
