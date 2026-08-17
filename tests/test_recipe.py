"""Recipe loading and validation. No rendering here, so these stay fast.

A recipe is hand-edited, so the failure mode worth defending against is a typo
that renders something subtly wrong rather than failing outright. Wrong audio
costs a listening session to diagnose; a rejected key costs a second.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scpad.recipe import RecipeError, load
from scpad.render import sweep_phases

RECIPES = Path(__file__).resolve().parents[1] / "recipes"


def write(directory: Path, body: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "recipe.toml"
    path.write_text(body, encoding="utf-8")
    return path


MINIMAL = """
name = "minimal"
seed = 7

[progression]
chords = [[60, 64]]
"""


def test_shipped_recipes_load():
    for path in sorted(RECIPES.glob("*.toml")):
        recipe = load(path)
        assert recipe.progression.chords
        assert recipe.total_seconds > recipe.loop_seconds


def test_minimal_recipe_uses_defaults(tmp_path):
    recipe = load(write(tmp_path, MINIMAL))
    assert recipe.name == "minimal"
    assert recipe.seed == 7
    assert recipe.render.sample_rate == 48000
    assert recipe.pad.attack == 7.0


def test_unknown_key_is_rejected(tmp_path):
    body = MINIMAL + "\n[pad]\nattck = 3.0\n"
    with pytest.raises(RecipeError, match="unknown key"):
        load(write(tmp_path, body))


def test_unknown_top_level_key_is_rejected(tmp_path):
    # The stray key has to precede every table header, or TOML scopes it into
    # whichever table came last.
    body = 'name = "x"\nseed = 1\ntemp0 = 120\n[progression]\nchords = [[60]]\n'
    with pytest.raises(RecipeError, match="unknown top-level"):
        load(write(tmp_path, body))


def test_missing_name_is_rejected(tmp_path):
    with pytest.raises(RecipeError, match="missing top-level `name`"):
        load(write(tmp_path, "seed = 1\n[progression]\nchords = [[60]]\n"))


def test_empty_progression_is_rejected(tmp_path):
    with pytest.raises(RecipeError, match="chords is empty"):
        load(write(tmp_path, 'name = "x"\nseed = 1\n'))


def test_chord_wider_than_pans_is_rejected(tmp_path):
    body = 'name = "x"\nseed = 1\n[pad]\npans = [0.0]\n[progression]\nchords = [[60, 64]]\n'
    with pytest.raises(RecipeError, match="every note needs a pan"):
        load(write(tmp_path, body))


def test_mismatched_voice_phases_are_rejected(tmp_path):
    body = MINIMAL + "\n[pad]\ndetune_cents = [-5.0, 5.0]\nvoice_phases = [0.0]\n"
    with pytest.raises(RecipeError, match="they must match"):
        load(write(tmp_path, body))


def test_inverted_cutoff_range_is_rejected(tmp_path):
    body = MINIMAL + "\n[pad]\ncutoff_lo = 2000.0\ncutoff_hi = 500.0\n"
    with pytest.raises(RecipeError, match="cutoff_hi"):
        load(write(tmp_path, body))


def test_seed_does_nothing_without_jitter(tmp_path):
    """A recipe with jitter off must ignore the seed entirely.

    This is the property that lets apollo.toml carry a seed for future use
    without that seed silently affecting the archived render.
    """
    template = 'name = "a"\nseed = {seed}\n[progression]\nchords = [[60, 64]]\n'
    one = load(write(tmp_path / "a", template.format(seed=1)))
    two = load(write(tmp_path / "b", template.format(seed=99999)))
    assert sweep_phases(one) == sweep_phases(two)


def test_seed_changes_phases_once_jitter_is_on(tmp_path):
    template = (
        'name = "a"\nseed = {seed}\n[pad]\nphase_jitter = 0.5\n'
        "[progression]\nchords = [[60, 64]]\n"
    )
    one = load(write(tmp_path / "a", template.format(seed=1)))
    two = load(write(tmp_path / "b", template.format(seed=2)))
    assert sweep_phases(one) != sweep_phases(two)
    # Same seed, same phases -- the whole point of seeding.
    again = load(write(tmp_path / "c", template.format(seed=1)))
    assert sweep_phases(one) == sweep_phases(again)
