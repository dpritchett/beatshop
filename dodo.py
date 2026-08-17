"""Task runner. `uv run doit list` for the menu.

Two reasons this exists rather than a Makefile. Renders are the expensive step
and they are pure functions of a recipe plus the code that reads it, so doit's
file-dependency tracking means `doit render` after editing one recipe rebuilds
one WAV. And the tuning loop for a new sound is "change a number, render,
measure, read the numbers" -- worth one command rather than three.

    uv run doit             # everything that is out of date
    uv run doit render      # every recipe in recipes/ to out/
    uv run doit tune        # render flip.toml and print its shape
    uv run doit test        # pytest
    uv run doit deliver     # copy flip.wav into callscape
"""

from pathlib import Path

DOIT_CONFIG = {"default_tasks": ["test"], "verbosity": 2}

ROOT = Path(__file__).parent
RECIPES = ROOT / "recipes"
OUT = ROOT / "out"
SOURCES = sorted((ROOT / "src" / "scpad").glob("*.py"))

# Where callscape looks for its earcons. `make sounds` over in that repo bakes
# the beepboop recipes and is explicitly forbidden from touching the music, so
# it will never carry this file -- the copy is manual, the same way
# apollo-v1.flac got there.
CALLSCAPE_SOUNDS = Path.home() / "Projects" / "callscape" / "web" / "public" / "sounds"


def task_render():
    """Render every recipe to out/, skipping ones already up to date."""
    for recipe in sorted(RECIPES.glob("*.toml")):
        target = OUT / f"{recipe.stem}.wav"
        yield {
            "name": recipe.stem,
            "actions": [f"scpad render {recipe} {target}"],
            "file_dep": [recipe, *SOURCES],
            "targets": [target],
            "clean": True,
        }


def task_tune():
    """Render a one-shot recipe and print its envelope and spectral travel."""
    return {
        "actions": ["python scripts/whoosh_tune.py %(recipe)s"],
        "params": [
            {
                "name": "recipe",
                "short": "r",
                "default": str(RECIPES / "flip.toml"),
                "help": "recipe to tune (default: recipes/flip.toml)",
            }
        ],
        "uptodate": [False],  # always re-render; this is the read-the-numbers loop
    }


def task_audition():
    """Stitch a one-shot into repeats, a retrigger and a bed mix, then play it."""
    return {
        "actions": ["python scripts/audition.py %(wav)s"],
        "params": [
            {
                "name": "wav",
                "short": "w",
                "default": str(OUT / "flip.wav"),
                "help": "rendered one-shot to audition",
            }
        ],
        "task_dep": ["render:flip"],
        "uptodate": [False],
    }


def task_measure():
    """Re-check an already-rendered WAV for defects."""
    return {
        "actions": ["scpad measure %(wav)s"],
        "params": [
            {
                "name": "wav",
                "short": "w",
                "default": str(OUT / "apollo.wav"),
                "help": "WAV to measure",
            }
        ],
        "uptodate": [False],
    }


def task_test():
    """Run the suite. Renders happen inside it, so nothing here is cached."""
    return {"actions": ["pytest -q"], "uptodate": [False]}


def task_deliver():
    """Copy the flip earcon into callscape's sounds directory."""
    source = OUT / "flip.wav"

    def copy():
        if not CALLSCAPE_SOUNDS.is_dir():
            # Only useful on a machine that has callscape checked out beside
            # this repo. Everywhere else the render in out/ is the deliverable.
            print(f"no callscape checkout at {CALLSCAPE_SOUNDS}; nothing to deliver")
            return True
        (CALLSCAPE_SOUNDS / "flip.wav").write_bytes(source.read_bytes())
        print(f"copied {source} -> {CALLSCAPE_SOUNDS / 'flip.wav'}")
        return True

    return {"actions": [copy], "file_dep": [source], "uptodate": [False]}
