"""Render-path tests.

Everything here is measured, never listened to. No test in this suite may
import `scpad.playback` or otherwise touch an audio device -- WSL2 playback is
unreliable enough that a test depending on it would be worse than no test.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from scpad.analysis import measure, measure_seam, to_db
from scpad.recipe import load
from scpad.render import render
from scpad.wavio import read_wav

RECIPES = Path(__file__).resolve().parents[1] / "recipes"
TINY = RECIPES / "tiny.toml"
APOLLO = RECIPES / "apollo.toml"

# The archived pad-v1-preloop render -- the first one that sounded right. If
# this changes, the sound changed, and that should be a deliberate decision
# rather than a surprise.
APOLLO_SHA256 = "9a73cf47db94aef4f58e64966d53496284550a79e5976c96db0e4379a5251585"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def tiny_render(tmp_path_factory) -> Path:
    output = tmp_path_factory.mktemp("render") / "tiny.wav"
    return render(load(TINY), output)


def test_two_renders_are_byte_identical(tmp_path):
    """The core contract: same recipe in, same bytes out.

    This is the test that catches an accidental Rand UGen, a stray call to the
    global `random` module, or anything that reads the clock.
    """
    recipe = load(TINY)
    first = render(recipe, tmp_path / "first.wav")
    second = render(recipe, tmp_path / "second.wav")

    assert first.read_bytes() == second.read_bytes()
    assert sha256(first) == sha256(second)


def test_render_is_neither_silent_nor_clipped(tiny_render):
    audio = read_wav(tiny_render)
    metrics = measure(audio)
    assert not metrics.silent, "render produced no signal"
    assert metrics.clipped_samples == 0, f"{metrics.clipped_samples} samples at full scale"
    # Loud enough to be worth listening to, quiet enough to leave headroom.
    assert -20.0 < metrics.peak_db < -3.0, f"peak {metrics.peak_db:.2f} dBFS"
    assert audio.channels == 2


def test_no_meaningful_dc_offset(tiny_render):
    """DC offset wastes headroom and thumps on playback.

    The reverb tank is a long feedback path, so this is a real risk rather than
    a theoretical one -- LeakDC in the reverb SynthDef is what keeps it small.
    """
    metrics = measure(read_wav(tiny_render))
    for channel, offset in enumerate(metrics.dc_offset):
        assert abs(offset) < 1e-4, f"channel {channel} DC offset {offset:.2e}"


def test_no_dropouts_inside_the_body(tiny_render):
    """Nothing should fall silent while the piece is still playing.

    A gap here would mean a chord released before the next one arrived, which is
    the failure mode the release/attack overlap exists to prevent.
    """
    recipe = load(TINY)
    audio = read_wav(tiny_render)
    body_frames = int(recipe.loop_seconds * audio.sample_rate)
    body = audio.samples[:body_frames]
    # Once past the initial attack, every 100ms window should carry signal.
    window = int(0.1 * audio.sample_rate)
    start = int(recipe.pad.attack * audio.sample_rate)
    for offset in range(start, len(body) - window, window):
        chunk = body[offset : offset + window]
        assert abs(chunk).max() > 1e-4, f"dropout at {offset / audio.sample_rate:.2f}s"


def test_starts_and_ends_quiet_enough_to_repeat(tiny_render):
    """The loop contract we actually committed to.

    Not seamless wrap-around: the piece fades to near-silence and the next pass
    builds back up from near-silence. That only sounds right if both ends are
    genuinely quiet relative to the body, and if neither end starts mid-waveform
    -- a nonzero first or last sample is what makes a repeat click.
    """
    recipe = load(TINY)
    audio = read_wav(tiny_render)
    metrics = measure(audio)
    # The window has to be short relative to the attack, or it measures the
    # swell rather than the silence the swell starts from.
    seam = measure_seam(audio, window_seconds=recipe.pad.attack * 0.1)

    body_db = to_db(metrics.rms)
    assert to_db(seam.head_rms) < body_db - 20.0, "opening does not build from quiet"
    assert to_db(seam.tail_rms) < body_db - 30.0, "tail does not decay far enough"
    # A splice between the last and first sample must not step, or it clicks.
    assert seam.step < 1e-3, f"loop seam steps by {seam.step:.6f}"


def test_recipe_render_matches_cli_path(tmp_path):
    """`scpad render` and the library API must produce the same bytes."""
    from scpad.cli import main

    output = tmp_path / "cli.wav"
    assert main(["render", str(TINY), str(output)]) == 0

    direct = render(load(TINY), tmp_path / "direct.wav")
    assert output.read_bytes() == direct.read_bytes()


def test_apollo_still_sounds_like_the_archived_render(tmp_path):
    """Golden test against the render Daniel signed off on.

    Renders the full 111s piece, which costs under two seconds because NRT runs
    far faster than realtime -- cheap enough to guard the sound on every run.
    """
    output = render(load(APOLLO), tmp_path / "apollo.wav")
    assert sha256(output) == APOLLO_SHA256
