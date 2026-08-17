# beatshop

Deterministic offline audio rendering. A recipe goes in, a reproducible WAV
comes out, and defects are caught by measurement rather than by listening.

The repo is `beatshop`. The Python package and the CLI it installs are both
`scpad`, because the command was specified as `scpad render` before the repo had
a name. Two names for one thing is a wart, and it is cheaper to say so here than
to break everyone's muscle memory renaming it.

Two kinds of recipe. A `[pad]` recipe is a slow ambient piece, minutes long. A
`[whoosh]` recipe is a sub-second one-shot earcon. They share the render path,
the determinism contract, and the measurement suite.

It rents SuperCollider's DSP -- `scsynth` in non-realtime mode, driven from
Python via [supriya](https://github.com/supriya-project/supriya). No sclang, no
realtime server, no DAW, no GUI, and no audio device is opened during rendering.

## Setup

    sudo apt-get install -y --no-install-recommends supercollider-server
    uv sync

That is six packages and no Qt. `jackd` comes along as a hard dependency of the
server package but is never started; decline its realtime-priority prompt.

## Use

    uv run scpad render recipes/apollo.toml out/apollo.wav
    uv run scpad measure out/apollo.wav --seam
    uv run scpad measure out/flip.wav --sweep
    uv run scpad play out/apollo.wav --no-wait

`render` prints peak, RMS, DC offset, clipped sample count and silence spans,
and exits nonzero if the output clips or is silent. `--seam` adds the
loop-point discontinuity, which only means something for a piece that repeats.
`--sweep` adds where a one-shot peaks and how far its filter travels, which only
means something for a gesture.

There is a task runner for the parts you do more than once:

    uv run doit list         # the menu
    uv run doit render       # every recipe in recipes/, skipping what is current
    uv run doit tune         # render a one-shot and chart its shape
    uv run doit test

## What determinism means here

Two renders of the same recipe are byte-identical, and there is a test that
asserts it. That holds only because nothing in the signal path is random:
no `Rand`, no noise UGens, nothing that reads server RNG state or the clock.
SuperCollider seeds its RNG from wall-clock time, so a single `Rand()` would
break the contract -- which is why SC's own stock `default` SynthDef cannot be
rendered twice to the same bytes.

Variation that a recipe wants is computed in Python from the recipe's `seed`
and passed in as a synth parameter.

A whoosh is filtered noise, so the rule looks like it forbids one. It does not:
the noise samples are drawn in Python from the seed, loaded into a `Buffer`, and
read back with `PlayBuf`. Same move as everything else here, with an array
instead of a scalar, and there is a test asserting that path renders twice to
the same bytes.

## Listening

`scpad play` converts to 16-bit and hands the file to Windows via
`System.Media.SoundPlayer`. It never touches WSL2's own audio stack, which is
unreliable enough that no test is allowed to depend on it. `src/scpad/playback.py`
is quarantined: nothing in the render or verification path imports it.

## Layout

    recipes/        the pieces, hand-authored TOML with comments
    src/scpad/
      synths.py     SynthDefs, defined in Python
      recipe.py     recipe loading and validation
      render.py     recipe -> Score -> WAV
      analysis.py   numeric defect detection
      wavio.py      RIFF reader
      playback.py   quarantined Windows playback
      cli.py        argument plumbing
    scripts/        scratch drivers
    dodo.py         task runner
    tests/

See `DECISIONS.md` for the reasoning, including what had to be guessed.

## License

Code and recipes are [MIT](LICENSE). Audio files rendered from these recipes and
distributed by me are [CC BY-NC 4.0](LICENSE-SOUNDS.md) — use them, credit me,
ask before you sell something with them in it.

The renderer is deterministic, so you can always just build your own. That is
the intended escape hatch, not a loophole.
