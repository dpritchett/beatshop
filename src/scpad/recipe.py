"""Recipe loading and validation.

TOML, not JSON. The recipes are the product, they are hand-authored, and the
single most useful thing you can put in one is a comment explaining why the
attack is 7 seconds and not 4. JSON cannot hold that. `tomllib` has been in the
stdlib since 3.11, and we only ever read recipes -- they are written by hand,
never generated -- so the read-only limitation costs nothing.

Unknown keys are rejected rather than ignored. A typo in a hand-edited recipe
should fail loudly, not silently render something subtly wrong that then has to
be diagnosed by ear.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any


class RecipeError(ValueError):
    pass


def _take(table: dict[str, Any], cls: type, where: str) -> dict[str, Any]:
    known = {f.name for f in fields(cls)}
    unknown = set(table) - known
    if unknown:
        raise RecipeError(
            f"{where}: unknown key(s) {sorted(unknown)}; known keys are {sorted(known)}"
        )
    return dict(table)


def _tuplify(table: dict[str, Any], *keys: str) -> dict[str, Any]:
    """TOML gives us lists; the specs are frozen, so store tuples."""
    for key in keys:
        if table.get(key) is not None:
            table[key] = tuple(float(value) for value in table[key])
    return table


def _pairs(table: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Same, one level deeper, for TOML arrays of arrays."""
    for key in keys:
        if table.get(key) is not None:
            table[key] = tuple(tuple(float(v) for v in row) for row in table[key])
    return table


# Every table `load()` knows how to read. A recipe declaring anything else is
# almost certainly a typo in a table header, which TOML would otherwise swallow
# silently along with every key underneath it.
_PAD_TABLES = frozenset({"pad", "reverb", "progression"})
_KNOWN_TABLES = _PAD_TABLES | {"render", "whoosh"}


@dataclass(frozen=True)
class RenderSpec:
    sample_rate: int = 48000
    bit_depth: int = 24


@dataclass(frozen=True)
class PadSpec:
    amplitude: float = 0.40
    attack: float = 7.0
    release: float = 10.0
    width: float = 0.32
    cutoff_lo: float = 260.0
    cutoff_hi: float = 1500.0
    # Period, in seconds, of the filter sweep. Kept as a period rather than a
    # frequency because "the filter takes 31 seconds to travel" is the thing
    # you actually reason about when writing a recipe.
    sweep_period: float = 31.0
    detune_cents: tuple[float, ...] = (-7.0, -2.5, 3.5, 8.0)
    pans: tuple[float, ...] = (-0.55, 0.35, -0.25, 0.6)
    # Starting phase per detuned voice. Omit to let scpad space them by golden
    # angle, which generalizes to any voice count. Pin them explicitly when a
    # recipe needs to keep reproducing a specific archived render.
    voice_phases: tuple[float, ...] | None = None
    # Amount, in radians, of seed-driven scatter applied to each note's filter
    # sweep offset. 0.0 means the sweep stagger is purely formulaic and `seed`
    # has no effect on the output.
    phase_jitter: float = 0.0


@dataclass(frozen=True)
class WhooshSpec:
    """A one-shot swept-noise earcon. Mutually exclusive with `[pad]`.

    Every duration here is a fraction of `duration` rather than an absolute time,
    so retuning the length of the gesture does not require rebalancing the
    envelopes underneath it.
    """

    duration: float = 0.35
    amplitude: float = 0.5
    # Fraction of `duration` at which the amplitude envelope peaks.
    attack_fraction: float = 0.35
    # Fraction of `duration` at which the filter is widest open. Later than
    # `attack_fraction` on purpose -- see build_whoosh.
    sweep_peak_at: float = 0.45
    cutoff_start: float = 180.0
    cutoff_peak: float = 5200.0
    cutoff_end: float = 300.0
    # 0.0 is white noise; higher is darker. See synths.noise_samples.
    noise_tilt: float = 0.35
    # (frequency, gain) pairs mixed under the noise, unfiltered.
    partials: tuple[tuple[float, ...], ...] = (
        (90.0, 1.0),
        (180.0, 0.55),
        (270.0, 0.12),
    )
    # Blend between the filtered noise and those partials, 0.0 to 1.0.
    body_mix: float = 0.35


@dataclass(frozen=True)
class ReverbSpec:
    decay: float = 7.0
    damping: float = 2400.0
    mix: float = 0.45
    predelay: float = 0.04


@dataclass(frozen=True)
class ProgressionSpec:
    chord_seconds: float = 24.0
    # Extra time after the last release so the reverb can decay to silence
    # rather than being truncated mid-tail.
    tail_seconds: float = 5.0
    chords: tuple[tuple[float, ...], ...] = ()


@dataclass(frozen=True)
class Recipe:
    name: str
    seed: int
    render: RenderSpec = field(default_factory=RenderSpec)
    pad: PadSpec = field(default_factory=PadSpec)
    reverb: ReverbSpec = field(default_factory=ReverbSpec)
    progression: ProgressionSpec = field(default_factory=ProgressionSpec)
    # Present only for one-shot recipes. `load()` rejects a file that declares
    # both this and any of the pad tables, so the two kinds never overlap.
    whoosh: WhooshSpec | None = None
    source_path: Path | None = None

    @property
    def is_whoosh(self) -> bool:
        return self.whoosh is not None

    @property
    def loop_seconds(self) -> float:
        return self.progression.chord_seconds * len(self.progression.chords)

    @property
    def total_seconds(self) -> float:
        if self.whoosh is not None:
            return self.whoosh.duration
        return self.loop_seconds + self.pad.release + self.progression.tail_seconds

    def validate(self) -> None:
        if self.render.bit_depth not in (16, 24, 32):
            raise RecipeError("render.bit_depth must be 16, 24 or 32")
        if self.render.sample_rate <= 0:
            raise RecipeError("render.sample_rate must be positive")
        if self.whoosh is not None:
            self._validate_whoosh(self.whoosh)
        else:
            self._validate_pad()

    @staticmethod
    def _validate_whoosh(whoosh: WhooshSpec) -> None:
        if whoosh.duration <= 0:
            raise RecipeError("whoosh.duration must be positive")
        for name in ("attack_fraction", "sweep_peak_at"):
            value = getattr(whoosh, name)
            if not 0.0 < value < 1.0:
                raise RecipeError(f"whoosh.{name} must be between 0 and 1, exclusive")
        # EnvelopeShape.EXPONENTIAL cannot cross or touch zero, and every one of
        # these is a filter frequency, so this is a hard requirement rather than
        # a taste check.
        for name in ("cutoff_start", "cutoff_peak", "cutoff_end"):
            if getattr(whoosh, name) <= 0:
                raise RecipeError(f"whoosh.{name} must be positive")
        if whoosh.cutoff_peak <= max(whoosh.cutoff_start, whoosh.cutoff_end):
            raise RecipeError(
                "whoosh.cutoff_peak must be above both cutoff_start and cutoff_end; "
                "the filter has to open and then close, or this is not a whoosh"
            )
        if not 0.0 <= whoosh.noise_tilt < 1.0:
            raise RecipeError("whoosh.noise_tilt must be at least 0 and below 1")
        if not 0.0 <= whoosh.body_mix <= 1.0:
            raise RecipeError("whoosh.body_mix must be between 0 and 1")
        for pair in whoosh.partials:
            if len(pair) != 2:
                raise RecipeError(
                    "whoosh.partials entries must be [frequency, gain] pairs, "
                    f"got {list(pair)}"
                )
            if pair[0] <= 0:
                raise RecipeError("whoosh.partials frequencies must be positive")

    def _validate_pad(self) -> None:
        if not self.progression.chords:
            raise RecipeError("progression.chords is empty; nothing to render")
        if len(self.pad.detune_cents) == 0:
            raise RecipeError("pad.detune_cents is empty; a voice needs oscillators")
        widest = max(len(chord) for chord in self.progression.chords)
        if len(self.pad.pans) < widest:
            raise RecipeError(
                f"pad.pans has {len(self.pad.pans)} entries but the widest chord has "
                f"{widest} notes; every note needs a pan position"
            )
        if self.pad.cutoff_lo <= 0 or self.pad.cutoff_hi <= self.pad.cutoff_lo:
            raise RecipeError("pad.cutoff_hi must be greater than a positive cutoff_lo")
        if self.pad.sweep_period <= 0:
            raise RecipeError("pad.sweep_period must be positive")
        if self.pad.voice_phases is not None and len(self.pad.voice_phases) != len(
            self.pad.detune_cents
        ):
            raise RecipeError(
                f"pad.voice_phases has {len(self.pad.voice_phases)} entries but "
                f"pad.detune_cents has {len(self.pad.detune_cents)}; they must match"
            )
        if self.pad.phase_jitter < 0:
            raise RecipeError("pad.phase_jitter must not be negative")
        if self.progression.chord_seconds <= 0:
            raise RecipeError("progression.chord_seconds must be positive")
        if not 0.0 <= self.reverb.mix <= 1.0:
            raise RecipeError("reverb.mix must be between 0 and 1")


def load(path: str | Path) -> Recipe:
    path = Path(path)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise RecipeError(f"{path}: invalid TOML: {exc}") from exc

    top = {k: v for k, v in raw.items() if not isinstance(v, dict)}
    if "name" not in top:
        raise RecipeError(f"{path}: missing top-level `name`")
    if "seed" not in top:
        raise RecipeError(f"{path}: missing top-level `seed`")

    unknown_top = set(top) - {"name", "seed"}
    if unknown_top:
        raise RecipeError(f"{path}: unknown top-level key(s) {sorted(unknown_top)}")

    unknown_tables = set(raw) - set(top) - _KNOWN_TABLES
    if unknown_tables:
        raise RecipeError(f"{path}: unknown table(s) {sorted(unknown_tables)}")

    if "whoosh" in raw:
        collided = sorted(_PAD_TABLES & set(raw))
        if collided:
            raise RecipeError(
                f"{path}: a [whoosh] recipe cannot also declare {collided}; "
                "one recipe renders one thing"
            )
        recipe = Recipe(
            name=str(top["name"]),
            seed=int(top["seed"]),
            render=RenderSpec(
                **_take(raw.get("render", {}), RenderSpec, f"{path} [render]")
            ),
            whoosh=WhooshSpec(
                **_pairs(
                    _take(raw["whoosh"], WhooshSpec, f"{path} [whoosh]"), "partials"
                )
            ),
            source_path=path,
        )
        recipe.validate()
        return recipe

    progression_table = _take(
        raw.get("progression", {}), ProgressionSpec, f"{path} [progression]"
    )
    chords = progression_table.pop("chords", ())
    recipe = Recipe(
        name=str(top["name"]),
        seed=int(top["seed"]),
        render=RenderSpec(
            **_take(raw.get("render", {}), RenderSpec, f"{path} [render]")
        ),
        pad=PadSpec(
            **_tuplify(
                _take(raw.get("pad", {}), PadSpec, f"{path} [pad]"),
                "detune_cents",
                "pans",
                "voice_phases",
            )
        ),
        reverb=ReverbSpec(
            **_take(raw.get("reverb", {}), ReverbSpec, f"{path} [reverb]")
        ),
        progression=ProgressionSpec(
            chords=tuple(tuple(float(n) for n in chord) for chord in chords),
            **progression_table,
        ),
        source_path=path,
    )
    recipe.validate()
    return recipe
