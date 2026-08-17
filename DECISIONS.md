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

## Recipe format: TOML, not JSON

The recipes are the product. They are hand-authored and hand-tuned, and the
single most valuable thing you can put in one is a comment explaining *why* the
attack is 7 seconds and not 4, or why the sweep period is 31s against a 24s
chord. JSON cannot hold a comment at all. That alone decides it.

Supporting points:

- `tomllib` has been in the stdlib since 3.11, and we only ever read recipes --
  they are written by hand, never generated -- so TOML's read-only stdlib
  support costs nothing.
- TOML's arrays-of-arrays express chords as MIDI note lists readably.
- JSON's advantage is stable machine round-tripping, which we do not need. The
  render is identified by the hash of its *output*, not of its recipe.

Unknown keys are rejected rather than ignored. A typo like `attck = 3.0` should
fail in a second, not silently render something subtly wrong that then costs a
listening session to diagnose.

## Render speed

NRT rendering runs about **67x realtime**: the full 111-second apollo piece
renders in 1.66s wall clock. This killed a planned slow/fast test split --
there is nothing here worth deferring, so the golden test that guards the
signed-off sound runs on every `pytest` invocation.

## The loop contract we actually committed to

Not seamless wrap-around. The agreed contract is: the piece fades to
near-silence, and the next pass builds back up from near-silence. Daniel's
call, on the grounds that it is good enough to start and the sound was already
worth keeping.

What is asserted numerically (`test_starts_and_ends_quiet_enough_to_repeat`):

- The opening window is at least 20 dB below the body RMS, so it genuinely
  builds from quiet. The window has to be short relative to the attack -- a
  0.5s window against a 0.6s attack measures the swell, not the silence it
  starts from, which is exactly how the first version of this test failed.
- The tail is at least 30 dB below the body RMS.
- The splice between the last and first sample steps by less than 1e-3, because
  a nonzero step at the join is what makes a repeat click.

True seamless wrap-around is still available later: render longer than the loop
and fold the overhang back onto the head. Not done.

## Seed semantics

`seed` drives `pad.phase_jitter`, and nothing else. With jitter at 0 -- which is
what `apollo.toml` ships -- the filter sweep stagger is a pure function of the
chord and voice indices, and the seed has no effect on the output at all. There
is a test asserting exactly that, so a recipe can carry a seed for future use
without it silently perturbing an archived render.

When jitter is on, offsets come from `random.Random(recipe.seed)` and nothing
else. CPython's Mersenne Twister and `uniform()` are stable across versions,
which is what lets a seed mean the same thing on another machine.

## Refactor safety

`apollo.toml` was validated by rendering it through the fully restructured code
path -- factory SynthDef, TOML loader, CLI -- and confirming it reproduces the
archived `apollo-v1.wav` byte for byte (`9a73cf47db94…`). That checksum is now a
test constant. If it changes, the sound changed, and that should be a decision
rather than a surprise.

## WAV header quirk

`flac` warns: `legacy WAVE file has format type 1 but bits-per-sample=24`.
scsynth writes plain `WAVE_FORMAT_PCM` (0x0001) with a 24-bit depth rather than
`WAVE_FORMAT_EXTENSIBLE`, which is technically non-conformant. Our own reader
handles it, and ffmpeg and flac both cope, but a stricter loader elsewhere might
object. Worth knowing before handing raw WAVs to another project.

## Distribution formats

For handing renders to other projects: FLAC is lossless at 6.65 MB (4.8:1 --
slow ambient material compresses very well), MP3 192k is 2.67 MB, against 32 MB
for the 24-bit WAV. Copies live in `out/archive/` and are mirrored into the
Windows Downloads folder for listening. Note `out/` is gitignored, so neither
location survives a clean checkout -- renders are meant to be regenerated.

## The whoosh, and what the determinism rule actually forbids (2026-08-17)

Callscape asked for a `flip` earcon and it landed here rather than in beepboop,
because beatshop already owns a filter that moves. See `HANDOFF.md` for that
argument; this records what came out of doing it.

### Noise without touching server RNG

The rule in `synths.py` is "no noise UGens", and a whoosh is filtered noise, so
the rule appeared to block the work. It did not. The doctrine was already
"variation is computed in Python from the recipe seed and passed in", and a
noise buffer is that same move with an array instead of a scalar: draw the
samples from `random.Random(seed)`, load them with `/b_alloc` plus a run of
`/b_setn`, read them back with `PlayBuf`. scsynth's RNG is never consulted and
the render is byte-identical across runs, which `test_whoosh.py` asserts.

`RandSeed`/`RandID` were deliberately not used. Making SC's own noise UGens
behave depends on server RNG semantics this project has never characterised, and
using them would mean re-proving determinism from scratch.

Two details that cost time:

- `/b_setn` is chunked at 1024 samples. An NRT score is read from a file rather
  than a socket so the usual UDP packet ceiling does not apply, but scsynth
  still parses one packet at a time and a single 16 KB request is not worth
  finding the limit of.
- `PlayBuf` with `loop=0` **holds its final sample** rather than zeroing. Left
  alone that is a DC step at the end of every trigger, so the buffer is
  allocated four control blocks past the end of the render.

### Seed semantics now differ by recipe kind

For a pad recipe the seed does nothing unless `phase_jitter` is on, and there is
a test pinning that. For a whoosh recipe the seed *draws the noise*, so changing
it is a different sound rather than the same sound rearranged. Both behaviours
are now asserted, because two opposite meanings for one key is the sort of thing
that gets forgotten and then relied on.

### Block rounding against the "under half a second" contract

Confirmed rather than assumed, since the handoff asked. 0.35s at 22050 Hz is
7717.5 frames, which rounds up to 7744 (121 whole control blocks) for a rendered
0.3512s. The overshoot is 1.2ms and the test asserts the total stays under 0.5s.

### Output format is callscape's, not ours

22050 Hz / 16-bit / mono, which is what every other file in
`web/public/sounds/` already is. The whole earcon is 15,532 bytes against
roughly 100K for a 24-bit 48 kHz stereo version of the same 0.35s. Mono is not a
recipe dial: the source is one noise buffer with no stereo information in it, so
a centred `Pan2` would double the file to say the same thing twice.

### What the family resemblance cost

The handoff predicted the resemblance to the `flight-slow`/`flight-fast` beds
would not survive, and it did not. Different noise generator, different filter
topology, no shared seed lineage. The mitigation that did land: an FFT of
`flight-slow.wav` puts its character at 90 Hz (0 dB), 180 Hz (-5.4 dB) and
270 Hz (-20.7 dB), and those three partials sit under the noise at those
relative gains. They bypass the sweep, because the sweep starts at 180 Hz and
would gut them.

First balance attempt had `body_mix` at 0.35 and 75% of the output energy under
400 Hz -- a thump with some noise on it. At 0.16 it is 34%, which is the number
`test_it_is_air_and_not_a_thump` now guards.

### Measuring a gesture

`measure_seam` answers "does this loop", which a one-shot never does. The
replacement is `measure_sweep`: where the level peaks, where the spectral
centroid peaks, how far the centroid travels, and what share of energy sits
under 400 Hz. That turns three things you would otherwise judge by ear into
assertions -- the filter opens, the filter closes, and the brightest instant
lands *after* the loudest one, which is what makes it read as going past you
rather than arriving.

The checksum was deliberately left unpinned until the listen, so retuning stayed
cheap. Daniel signed off the same day and `FLIP_SHA256` is now a test constant,
same role as `APOLLO_SHA256`.

### Auditioning a one-shot

A 0.35s earcon played once tells you nothing, which is why the sign-off needed
`scripts/audition.py` rather than `scpad play`. It stitches three questions into
one file: three spaced singles (is it one gesture or a click with noise after
it), a 0.25s retrigger (a stick gets pressed twice), and one landing on top of
the flight bed (does it cut through the thing it will usually sit over). The
retrigger sums to 0.715 peak with no clipping. That file is a listening aid and
nothing in the render or test path knows it exists.

### doit

Added `dodo.py`. Renders are pure functions of a recipe plus the code that reads
it, which is exactly what doit's file-dependency tracking is for, and the tuning
loop for a new sound is otherwise three commands. Also fixes a workflow problem
that had nothing to do with audio: throwaway `python -c` strings each need their
own approval, and a task runner plus `scripts/` gives one preapprovable surface.

## Licensing: MIT code, CC BY-NC renders (2026-08-17)

Going public at `github.com/dpritchett/beatshop`. The intent was "code is free,
the sounds are not", and the interesting part was discovering that the obvious
way to write that down does not work.

`out/` is gitignored, so **no audio ships in this repo at all.** What ships is
`recipes/*.toml`, and the renderer is deterministic to the byte. So a licence
that frees the recipes while reserving the audio reserves nothing: MIT grants
the right to run the software, and the output of running MIT software is not
governed by MIT. Anyone can clone, render, and walk away with identical bytes.

Three ways to resolve it were on the table:

1. Treat the recipes as the composition and reserve them along with the audio.
   The only version with teeth, since the score *is* the sound here. Rejected --
   the recipes are the most interesting thing in the repo to read, and the whole
   point of publishing is that people read them.
2. MIT everything and reserve nothing in practice. Honest, but it is not what
   Daniel wanted.
3. MIT the code *and* the recipes, CC BY-NC 4.0 the rendered audio the author
   distributes. Chosen.

NC rather than ND or SA. ND would block trimming and looping, which is the first
thing anyone does with an ambient bed; SA imposes terms on other people's
derivative work, which was not the goal. NC says the actual thing: use it,
credit me, ask before you sell something with it in it.

`LICENSE-SOUNDS.md` names the regeneration hole explicitly rather than papering
over it. Pretending a licence is stronger than it is reads worse than saying
"the method is free, the recordings are mine, and if you want the sound badly
enough to rebuild it, you have earned it."

Layout is `LICENSE` with verbatim MIT text, so GitHub's licence detection picks
it up cleanly, plus `LICENSE-SOUNDS.md` for the carve-out. A single `LICENSE.md`
mixing both would likely have defeated detection.

Also fixed while in `pyproject.toml`: numpy was in the dev dependency group but
`wavio.py` and `analysis.py` import it at runtime, so an actual install produced
a CLI that imported and then died. Moved to `dependencies`.

## Open / not yet done

- **Seamless wrap-around**, if the fade-to-silence contract stops being good
  enough. Approach: render past the loop point and fold the overhang onto the
  head. Complicated by scsynth's 64-sample block rounding, so the fold has to
  align to a block boundary rather than an arbitrary sample.
- **Two instruments now**, the pad and the whoosh, and nothing shares between
  them except the render path and the golden-angle phase helper. A third would
  be the point at which the `[pad]`/`[whoosh]` table discrimination in
  `recipe.py` wants to become a real `kind` field rather than a table lookup.
- **No `scpad play` test coverage**, by design. It is quarantined from the
  harness and exercised by hand.
