"""SynthDefs, defined in Python. No sclang anywhere in this project.

Determinism rule for everything in this module: no Rand, no noise UGens, no
UGen whose output depends on server RNG state. scsynth seeds its RNG from the
wall clock, so a single Rand() would make renders non-reproducible -- that is
exactly why SuperCollider's own stock `default` SynthDef cannot be rendered
twice to the same bytes. Any variation we want is computed in Python from the
recipe seed and passed in as a synth parameter.

`noise_samples()` extends that rule rather than breaking it. A whoosh is
filtered noise, and the rule appears to forbid one; the way through is the move
the rule already describes, with an array instead of a scalar. The samples are
drawn in Python from the recipe seed, loaded into a Buffer, and read back with
PlayBuf. Server RNG is never touched.

Both instruments are factories rather than module-level SynthDefs because their
graph shape depends on the recipe: the pad's on how many detuned voices it
stacks, the whoosh's on its envelope times, which have to be literals in the
serialized Envelope rather than parameters set at note-on.
"""

from __future__ import annotations

import functools
import math
import operator
import random
from collections.abc import Sequence

from supriya import Envelope, synthdef
from supriya.enums import EnvelopeShape
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
    PlayBuf,
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


def noise_samples(seed: int, count: int, tilt: float) -> list[float]:
    """Seeded noise, generated here instead of on the server.

    `tilt` is 0.0 for white and approaches 1.0 for progressively darker noise,
    applied as a one-pole lowpass whose coefficient is the tilt itself. Lowpassing
    costs level, so the result is de-meaned and normalized to unit peak; that way
    `tilt` changes the colour of the noise and nothing else, and the recipe's
    amplitude dial keeps meaning the same thing across the range.

    `random.Random` rather than numpy because the seed has to mean the same thing
    on another machine, and CPython's Mersenne Twister with `uniform()` is the
    generator this project already relies on for that.
    """
    if not 0.0 <= tilt < 1.0:
        raise ValueError(f"noise tilt must be in [0.0, 1.0), got {tilt}")
    rng = random.Random(seed)
    previous = 0.0
    samples = []
    for _ in range(count):
        previous = tilt * previous + (1.0 - tilt) * rng.uniform(-1.0, 1.0)
        samples.append(previous)

    mean = sum(samples) / len(samples)
    samples = [value - mean for value in samples]
    peak = max(abs(value) for value in samples)
    return [value / peak for value in samples] if peak else samples


def build_whoosh(
    duration: float,
    attack_fraction: float,
    sweep_peak_at: float,
    cutoff_start: float,
    cutoff_peak: float,
    cutoff_end: float,
    partials: Sequence[Sequence[float]],
):
    """Build the whoosh SynthDef: one-shot swept noise over a fixed low body.

    Same mechanism as the pad's moving filter at a different time scale. The pad
    drives its cutoff from an LFO because a pad breathes on a cycle; a sub-second
    one-shot wants a single traversal instead, so the cutoff comes from an
    envelope that opens and then closes. Opening alone is a burst of static --
    the close is what makes it movement rather than noise.

    Amplitude gets its own envelope rather than reusing the cutoff's. The two
    peaks are deliberately offset: `sweep_peak_at` sits later than
    `attack_fraction`, so the brightest instant lands just after the loudest one
    and the thing reads as having gone past you.

    `partials` are (frequency, gain) pairs summed under the noise, unfiltered.
    They exist to buy back some of the family resemblance to callscape's flight
    beds, whose character is mostly a 90 Hz fundamental and its first two
    harmonics. The sweep would gut them -- it starts below 270 Hz -- so they
    bypass the filter and take only the amplitude envelope.
    """
    rise = duration * attack_fraction
    open_time = duration * sweep_peak_at
    frequencies = tuple(float(frequency) for frequency, _ in partials)
    gains = tuple(float(gain) for _, gain in partials)
    # Reuse the pad's golden-angle spacing. Harmonically related sines all
    # starting at phase 0 sum to a peaky waveform that wastes headroom for no
    # audible gain; scattering the phases flattens the crest factor.
    phases = default_voice_phases(len(frequencies))

    @synthdef()
    def whoosh(out=0, buffer_id=0, amplitude=0.5, body_mix=0.35):
        amplitude_envelope = EnvGen.kr(
            envelope=Envelope(
                amplitudes=[0.0, 1.0, 0.0],
                durations=[rise, duration - rise],
                # Convex in, concave out: the rise accelerates into its peak and
                # the fall drops away immediately. A symmetric envelope reads as
                # a swell, which is the wrong gesture for a half-turn.
                curves=[2.0, -2.5],
            ),
            done_action=2,
        )
        # Exponential segments for the same reason the pad runs its LFO through
        # LinExp: a linear travel in Hz spends most of its time sounding like it
        # is already at the top.
        cutoff = EnvGen.kr(
            envelope=Envelope(
                amplitudes=[cutoff_start, cutoff_peak, cutoff_end],
                durations=[open_time, duration - open_time],
                curves=[EnvelopeShape.EXPONENTIAL, EnvelopeShape.EXPONENTIAL],
            )
        )
        # rate defaults to 1.0, which is already correct: the buffer is allocated
        # empty rather than read from a file, so it carries the server's own
        # sample rate and BufRateScale would return 1.0.
        air = PlayBuf.ar(buffer_id=buffer_id, channel_count=1, loop=0)
        # Two cascaded 2-pole sections, matching the pad. No resonance: a moving
        # formant would make this a laser rather than a whoosh.
        air = LPF.ar(source=LPF.ar(source=air, frequency=cutoff), frequency=cutoff)

        body = _sum(
            SinOsc.ar(frequency=frequency, phase=phase) * gain
            for frequency, gain, phase in zip(frequencies, gains, phases)
        )
        signal = air * (1.0 - body_mix) + body * body_mix
        # LeakDC because the filter passes DC and the amplitude envelope
        # multiplies whatever offset survives into an audible thump.
        Out.ar(bus=out, source=LeakDC.ar(source=signal * amplitude_envelope * amplitude))

    return whoosh


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
