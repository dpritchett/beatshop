# scpad

Deterministic offline ambient music rendering. A recipe goes in, a reproducible
WAV comes out, and defects are caught by measurement rather than by listening.

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
    uv run scpad play out/apollo.wav --no-wait

`render` prints peak, RMS, DC offset, clipped sample count and silence spans,
and exits nonzero if the output clips or is silent.

## What determinism means here

Two renders of the same recipe are byte-identical, and there is a test that
asserts it. That holds only because nothing in the signal path is random:
no `Rand`, no noise UGens, nothing that reads server RNG state or the clock.
SuperCollider seeds its RNG from wall-clock time, so a single `Rand()` would
break the contract -- which is why SC's own stock `default` SynthDef cannot be
rendered twice to the same bytes.

Variation that a recipe wants is computed in Python from the recipe's `seed`
and passed in as a synth parameter.

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
    tests/

See `DECISIONS.md` for the reasoning, including what had to be guessed.
