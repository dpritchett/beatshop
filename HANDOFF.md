# Handoff

## Done: the whoosh is built (2026-08-17)

`recipes/flip.toml` renders `out/flip.wav`, 15,532 bytes, 22050 Hz / 16-bit /
mono, 0.351s. `uv run doit deliver` copies it into
`callscape/web/public/sounds/`. Full reasoning in `DECISIONS.md`; the three
questions this handoff asked to decide early were answered as:

- **Output format:** callscape's, not beatshop's. 22050/16/mono like every other
  earcon in that directory. Mono is not a dial -- one noise buffer has no stereo
  information to preserve.
- **Block rounding:** confirmed, not assumed. 0.35s rounds up to 0.3512s, a
  1.2ms overshoot, and there is a test asserting the total stays under 0.5s.
- **Delivery:** manual, as predicted. It is a `doit` task rather than a note.

The determinism rule held without being changed: noise is drawn in Python from
the recipe seed, loaded as a `Buffer`, read with `PlayBuf`, and two renders are
byte-identical. `RandSeed`/`RandID` were not touched.

The predicted cost was real. The family resemblance to the flight beds did not
survive; what landed is the 90/180/270 Hz partial layer, measured off
`flight-slow.wav` rather than guessed. It is a cousin.

**Signed off by ear, same day.** The numbers said the right shape -- filter
opens and closes, brightest instant 35ms after the loudest, 34% of energy under
400 Hz, peak -3.6 dBFS like its neighbours -- and Daniel confirmed the sound.
`FLIP_SHA256` is pinned in `tests/test_whoosh.py`, so a change to this earcon is
now a decision rather than a surprise.

    uv run doit tune        # render it and chart the shape
    uv run doit audition    # singles, a retrigger, and one over the flight bed

---

## Callscape wants a whoosh, and it lands here rather than in beepboop (2026-08-17)

Written from the beepboop side. Beepboop bakes callscape's earcons and speech;
beatshop renders its music. This is the first piece of work that does not sit
cleanly on either side of that line, and Daniel put it here.

### What callscape asked for

A slug named `flip`, for the half-turn that looks behind you. On a stick press,
`C`, or a cue. In Daniel's words, "something wooshy that evokes movement and
maybe thrusters".

The contract is small:

- One-shot, **under about half a second**. The spin itself is 0.2s, so anything
  much longer is still whooshing after you have arrived.
- Nothing changes on the app side when it lands. `web/src/sound.ts` already
  wires the slug and logs `voice.missing {cue: "flip"}` when the file is absent.

### Why it did not stay in beepboop

Beepboop has the closer material -- seeded noise, a multi-pole tone filter, and
the `flight-slow`/`flight-fast` beds that share seed `1102` -- and callscape
asked for the whoosh to come from that same family so it reads as *those*
engines rather than a stock effect.

What beepboop does not have is a filter that moves. Beatshop does: `synths.py`
already runs an LFO through `LinExp.kr` into two cascaded `LPF.ar`, with
`cutoff_lo`, `cutoff_hi`, and the sweep period exposed as recipe dials. That
primitive is the whole job, and building a second copy of it in Go to avoid
using this one is the worse trade.

**Known cost, stated plainly:** the family resemblance to the flight beds does
not survive the move. Different noise generator, different filter topology, no
shared seed lineage. Partial mitigation is to put the beds' own partials --
90 Hz, 180 Hz, 270 Hz -- under the noise, since that is where most of the
perceived character of those beds actually lives. It will be a cousin at best.

### The determinism rule needs extending, not breaking

`synths.py` forbids noise UGens, and it is right to: `scsynth` seeds its RNG from
the wall clock, which is the whole finding in `DECISIONS.md`. A whoosh is
filtered noise, so the rule appears to block the work.

It does not. The existing doctrine is already "variation is computed in Python
from the recipe seed and passed in". `sweep_phases()` does exactly that with a
`random.Random(recipe.seed)`. A noise buffer is the same move with an array
instead of a scalar: generate the samples in Python from the seed, load them as
a `Buffer`, and read them with `PlayBuf`. Server RNG is still never touched, the
render stays byte-identical, and the rule stands as written.

Do not reach for `RandSeed`/`RandID` to make SC's own noise UGens behave. That
depends on server RNG semantics the project has deliberately not characterised,
and it would mean re-proving determinism from scratch.

### Shape of the sound

Swept noise with cutoff and gain both travelling over the duration, opening and
then closing. Opening alone is a burst of static -- the close is what makes it a
movement rather than a noise.

The music path uses an LFO because a pad breathes on a cycle. A 0.35s one-shot
should not: it wants a single traversal, so drive the cutoff from an `EnvGen.kr`
over an `Envelope` with a peak partway through, and give amplitude its own
envelope rather than reusing the cutoff's.

Same mechanism as the pad at a different time scale, which is the argument for
it being here.

### Three things worth deciding early

- **Output format.** Beatshop renders 24-bit/48 kHz. Callscape's other cues are
  16-bit at 22.05 or 44.1 kHz, its sounds directory is already 764K, and MP3
  export is on beepboop's list as the next real need for exactly that reason.
  A 0.35s 24-bit 48 kHz stereo file is around 100K for one flick of the camera.
  16-bit, and possibly mono, is probably the right call here.
- **Block rounding.** `DECISIONS.md` records that `scsynth` renders whole
  64-sample blocks, so the output runs slightly longer than requested. At 0.35s
  the overshoot is about a millisecond and harmless, but the contract says
  "under about half a second" and somebody should confirm rather than assume.
- **Delivery.** `make sounds` in callscape bakes the beepboop recipe and is
  explicitly forbidden from touching the music, so it will not carry `flip`.
  The path is a manual copy into `web/public/sounds/`, the same way
  `apollo-v1.flac` got there.

### Tests

Determinism and the level checks apply as usual. The seam test does not -- this
one never wraps.
