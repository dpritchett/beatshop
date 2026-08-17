"""One-shot render path: the `flip` earcon for callscape.

Same rule as the rest of the suite -- everything is measured, never listened to.
The pad's loop-seam test does not apply here, because this one never wraps. What
replaces it is the sweep shape: a whoosh that does not travel is a burst of
static, and that is a defect a number can catch.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest

from scpad.analysis import measure, measure_sweep
from scpad.recipe import load
from scpad.render import render
from scpad.synths import noise_samples
from scpad.wavio import read_wav

RECIPES = Path(__file__).resolve().parents[1] / "recipes"
FLIP = RECIPES / "flip.toml"

# Callscape spins the camera in 0.2s and asked for the whoosh to be over in
# "about half a second". This is the contract, not a preference.
CONTRACT_SECONDS = 0.5

# The render Daniel listened to and signed off on, 2026-08-17. Same role as
# APOLLO_SHA256: the numeric tests below say the sound is the right shape, and
# only this one says it is the right sound. If it changes, the earcon changed.
FLIP_SHA256 = "8777eff0dfd5f698c868ed547d216f001584afb23f0bad53fb03fe0548f61324"


@pytest.fixture(scope="session")
def flip_render(tmp_path_factory) -> Path:
    output = tmp_path_factory.mktemp("whoosh") / "flip.wav"
    return render(load(FLIP), output)


def test_two_renders_are_byte_identical(tmp_path):
    """The determinism contract, re-proved for the buffer path.

    The pad proves it for a graph with no random anything in it. This proves it
    for a graph that is mostly noise, which is the case the rule in synths.py
    looked like it forbade: the samples come from `random.Random(seed)` in
    Python and travel to the server in `/b_setn` requests, so scsynth's own RNG
    is still never consulted.
    """
    recipe = load(FLIP)
    first = render(recipe, tmp_path / "first.wav")
    second = render(recipe, tmp_path / "second.wav")

    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == (
        hashlib.sha256(second.read_bytes()).hexdigest()
    )


def test_seed_actually_drives_the_noise(tmp_path):
    """Unlike the pad recipes, `seed` here changes the output.

    Worth pinning: `test_seed_does_nothing_without_jitter` asserts the opposite
    for the pad, and the two recipe kinds having opposite seed semantics is
    exactly the kind of thing that gets forgotten and then relied on.
    """
    recipe = load(FLIP)
    from dataclasses import replace

    original = render(recipe, tmp_path / "a.wav")
    other = render(replace(recipe, seed=recipe.seed + 1), tmp_path / "b.wav")
    assert original.read_bytes() != other.read_bytes()


def test_flip_still_sounds_like_the_signed_off_render(tmp_path):
    """Golden test against the render Daniel approved by ear.

    Everything else in this file asserts a property -- it travels, it does not
    click, it sits at the right level. A recipe could satisfy all of them and
    still be a different sound. This is the one that notices.
    """
    output = render(load(FLIP), tmp_path / "flip.wav")
    assert hashlib.sha256(output.read_bytes()).hexdigest() == FLIP_SHA256


def test_it_fits_callscape(flip_render):
    """Format and length, both of which are somebody else's requirement."""
    audio = read_wav(flip_render)
    # 22050 Hz / 16-bit / mono is what every other file in callscape's sounds
    # directory is, and a 24-bit 48 kHz stereo version of this would be six
    # times the size to say the same thing.
    assert audio.sample_rate == 22050
    assert audio.bit_depth == 16
    assert audio.channels == 1
    # scsynth renders whole 64-sample blocks, so the output runs a little past
    # the requested duration. At this length the overshoot is about a
    # millisecond, but the contract deserves an assertion rather than an
    # assumption.
    assert audio.duration < CONTRACT_SECONDS, f"{audio.duration:.3f}s is too long"
    assert flip_render.stat().st_size < 32_000


def test_it_sits_at_the_level_of_the_other_earcons(flip_render):
    metrics = measure(read_wav(flip_render))
    assert not metrics.silent
    assert metrics.clipped_samples == 0
    # focus.wav, select.wav and capture.wav all peak at -2.9 dBFS. Matching them
    # matters more than any absolute target: an earcon that is quieter than its
    # neighbours reads as a bug in the app.
    assert -6.0 < metrics.peak_db < -2.0, f"peak {metrics.peak_db:.2f} dBFS"


def test_no_meaningful_dc_offset(flip_render):
    """A swept lowpass passes DC, and the amplitude envelope turns any surviving
    offset into an audible thump on every trigger. LeakDC in the SynthDef is
    what keeps this small."""
    for channel, offset in enumerate(measure(read_wav(flip_render)).dc_offset):
        assert abs(offset) < 1e-4, f"channel {channel} DC offset {offset:.2e}"


def test_it_does_not_click_on_either_edge(flip_render):
    """A one-shot fires repeatedly, so both edges have to land on zero.

    The tail end is the one at risk: PlayBuf with loop=0 holds its final sample
    rather than zeroing, and the buffer is deliberately allocated past the end of
    the render so that held sample never reaches the output.
    """
    audio = read_wav(flip_render)
    mono = audio.samples.mean(axis=1)
    assert abs(mono[0]) < 1e-4, f"starts at {mono[0]:.2e}"
    assert abs(mono[-1]) < 1e-4, f"ends at {mono[-1]:.2e}"
    tail = mono[-int(0.005 * audio.sample_rate) :]
    assert abs(tail).max() < 1e-3, f"last 5ms peaks at {abs(tail).max():.2e}"


def test_the_filter_opens_and_then_closes(flip_render):
    """The shape that makes it a whoosh rather than a burst of static.

    Opening alone is static. The close is the movement, so both halves of the
    travel get asserted.
    """
    sweep = measure_sweep(read_wav(flip_render))
    assert sweep.centroid_peak > sweep.centroid_start * 4.0, (
        f"filter barely opens: {sweep.centroid_start:.0f} -> "
        f"{sweep.centroid_peak:.0f} Hz"
    )
    assert sweep.centroid_end < sweep.centroid_peak * 0.4, (
        f"filter does not close: peaks at {sweep.centroid_peak:.0f}, "
        f"ends at {sweep.centroid_end:.0f} Hz"
    )


def test_it_goes_past_you_rather_than_arriving(flip_render):
    """Brightest after loudest, which is the whole gesture.

    If the two peaks coincide the sound reads as an impact. The offset is what
    makes it a half-turn that carries on past the camera.
    """
    sweep = measure_sweep(read_wav(flip_render))
    assert sweep.peak_centroid_at > sweep.peak_level_at, (
        f"brightest at {sweep.peak_centroid_at:.3f}s is not after "
        f"loudest at {sweep.peak_level_at:.3f}s"
    )


def test_it_is_air_and_not_a_thump(flip_render):
    """The 90/180/270 Hz partials are a garnish, not the dish.

    They are there for family resemblance to the flight beds. Let them dominate
    and the earcon becomes a low thud with some noise on it, which is a
    different sound with the same envelope.
    """
    sweep = measure_sweep(read_wav(flip_render))
    assert sweep.low_fraction < 0.5, (
        f"{sweep.low_fraction:.0%} of the energy is under 400 Hz"
    )


def test_noise_is_reproducible_and_normalized():
    first = noise_samples(seed=1102, count=512, tilt=0.22)
    assert first == noise_samples(seed=1102, count=512, tilt=0.22)
    assert first != noise_samples(seed=1103, count=512, tilt=0.22)
    assert len(first) == 512
    # Unit peak and no DC, so `tilt` changes colour and nothing else and the
    # recipe's amplitude dial keeps meaning the same thing across the range.
    assert max(abs(value) for value in first) == pytest.approx(1.0)
    assert abs(sum(first) / len(first)) < 1e-12


def test_tilt_darkens_the_noise():
    """Higher tilt means more correlation between neighbouring samples, which is
    the same statement as less high-frequency content."""

    def roughness(samples):
        return sum(abs(b - a) for a, b in itertools.pairwise(samples)) / len(samples)

    white = noise_samples(seed=1102, count=4096, tilt=0.0)
    dark = noise_samples(seed=1102, count=4096, tilt=0.6)
    assert roughness(dark) < roughness(white) * 0.5


@pytest.mark.parametrize("tilt", [-0.1, 1.0, 1.5])
def test_out_of_range_tilt_is_rejected(tilt):
    with pytest.raises(ValueError, match="tilt"):
        noise_samples(seed=1, count=8, tilt=tilt)
