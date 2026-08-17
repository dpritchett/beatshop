# Decisions

Running log. Includes the things I guessed at, marked as guesses.

## Substrate (step 1)

### SuperCollider packages

Installed on Ubuntu 26.04 (resolute):

    sudo apt-get install -y --no-install-recommends supercollider-server

That pulls exactly six packages: `supercollider-server`, `libscsynth1t64`,
`jackd`, `jackd2`, `libboost-filesystem1.83.0`, `libfftw3-single3`.

Deliberately **not** installed:

- `supercollider-language` — this is `sclang`, and it drags in Qt5 including
  `libqt5webenginewidgets5`. We define SynthDefs in Python via supriya, which
  compiles them to SynthDef bytecode itself. There is no sclang in this project.
- `supercollider-common` — only a dependency of `-language`, not of `-server`.
- The `supercollider` metapackage — pulls the IDE.

`jackd` is a hard `Depends` of `supercollider-server`, so it cannot be avoided.
It is never started. Its postinst offers to grant the daemon realtime scheduling
priority; we answered **no**, which avoids writing `/etc/security/limits.d/` and
changing group membership. Realtime priority only matters to a live JACK daemon
avoiding xruns, and NRT rendering is plain offline compute.

Result: `scsynth 3.13.0` at `/usr/bin/scsynth`, 26 UGen plugin `.so` files under
`/usr/lib/SuperCollider/plugins`.

### Headless confirmation

`scsynth` runs with no jackd process, no X server (`DISPLAY` unset for the test
run), and no audio device. `supercollider-server` does link `libx11-6`, but
linking a library is not the same as needing a display, and rendering with
`DISPLAY` unset proves it.

### Reproducibility — the important finding

The first proof **failed**: two renders of supriya's own hello-world differed in
622,080 bytes. Diagnosis rather than the obvious conclusion:

- Differences began at byte 69, inside the audio data. The 44-byte RIFF header
  was byte-identical, so libsndfile is writing no `LIST`/`INFO` chunk and no
  creation-date tag. Header is not the problem.
- Differing offsets fell on a 24-byte stride, hitting only the two
  audio-bearing channels of an 8-channel file and skipping the six silent ones.
- The deltas were 1–3 LSB.

Cause: SuperCollider's stock `default` SynthDef contains `Rand` UGens (confirmed
by inspecting `supriya.default.ugens`: `Rand` is present, used for detuning and
the filter sweep). scsynth seeds its RNG from the wall clock at boot. So the
hello-world is *designed* to vary.

Re-proved with a SynthDef containing no random UGens: **three consecutive
renders were byte-identical** (sha256 `81129b0b3493…`, 864,428 bytes, 44-byte
header, `[fmt , data]` chunks only).

**Conclusion: the substrate is deterministic. Randomness is opt-in, and we do
not opt in.** Hence the rule in `src/scpad/synths.py`: no `Rand`, no noise
UGens, nothing that reads server RNG state. Variation is computed in Python from
a recipe seed and passed in as a synth parameter.

### Render length rounding

scsynth renders in whole 64-sample control blocks. A 3.0s request at 48 kHz
produced 144,064 frames (3.0013s), which is 2251 blocks exactly. Deterministic,
but output duration is not exactly the requested duration. Anything that depends
on exact sample counts — the loop seam especially — has to account for this.

### Render format

WAV / 24-bit PCM / 48 kHz. supriya defaults to AIFF/int24, overridden. WAV
because it is what the rest of the toolchain and Windows playback expect; 24-bit
because the dither-free headroom is free and we are not shipping these as
distribution masters.

## Playback

`scpad play` hands the file to Windows and never opens a WSL audio device.
Mechanism: convert to 16-bit PCM into `$env:TEMP`, then
`System.Media.SoundPlayer` over `powershell.exe`. No GUI app opens, no WSLg
PulseAudio involvement.

Why 16-bit staging: `SoundPlayer` is only dependable with 16-bit PCM. The
conversion is playback-only and throwaway — it never touches a rendered
artifact, and it uses plain rounding with no dither, which would be wrong for a
master but is irrelevant for listening.

`src/scpad/playback.py` is quarantined by rule: nothing in the render or
verification path imports it, and no test may call it. WSL2 playback is
unreliable, so a test that depended on it would produce worse-than-useless
signal. That was the actual constraint — not that audio devices are forbidden
outright, but that they must not become part of the harness.

Guess I made: hardcoded nothing about the Windows user. The staging directory is
resolved at runtime from `$env:TEMP` via `wslpath`.

## Naming

Repo directory is `beatshop`; the project is `scpad`. Renamed the Python package
and distribution to `scpad` (`src/scpad/`, CLI entry point `scpad`) since the
CLI was specified as `scpad render`. Left the directory and git remote alone —
that is a rename for later.

## Analysis

Wrote a small RIFF reader (`src/scpad/wavio.py`) instead of adding a libsndfile
binding. Two reasons: stdlib `wave` cannot unpack 24-bit samples, and parsing
chunks ourselves lets the analysis assert on header layout — which is how the
"no timestamp chunk" finding above was confirmed.

No limiter anywhere in the signal chain. A limiter would suppress exactly the
clipping the tests are supposed to catch. Levels are set by measuring peak and
scaling a constant, since the chain is entirely linear.

## Open / not yet done

- **Loop seam is not solved.** The current sketch appends the reverb tail rather
  than wrapping it, so the file starts with a 7s fade-in and ends in silence.
  `measure_seam` reports a step of 0.000017 (-95 dBFS) only because both ends
  are silent — that is a trivially continuous splice, not a musical loop. Real
  fix: render longer than the loop and fold the overhang back onto the head.
- Recipe format not yet chosen (JSON vs TOML) — argued in a later section once
  written.
- `scpad render` not yet implemented; `scripts/pad_sketch.py` hardcodes the
  chord table.
