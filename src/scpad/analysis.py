"""Numeric defect detection.

The contract is that defects get caught by measurement, never by listening.
Every function here answers one question with a number, so tests can assert on
it and so a render can be judged without opening an audio device.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .wavio import Audio

# A 24-bit sample one LSB below full scale. Anything at or above this is
# indistinguishable from a clipped sample once quantized.
FULL_SCALE_EPSILON = 1.0 - (1.0 / (2**23))


def to_db(value: float) -> float:
    """Linear amplitude to dBFS, floored so digital silence stays printable."""
    return 20.0 * math.log10(value) if value > 1e-12 else -math.inf


@dataclass(frozen=True)
class Metrics:
    peak: float
    peak_db: float
    rms: float
    rms_db: float
    dc_offset: tuple[float, ...]
    clipped_samples: int
    longest_silence: float
    silent: bool
    duration: float
    sample_rate: int
    channels: int
    rms_profile: list[float] = field(default_factory=list)

    def describe(self) -> str:
        dc = ", ".join(f"{d:+.2e}" for d in self.dc_offset)
        return (
            f"duration    {self.duration:.3f}s @ {self.sample_rate}Hz x{self.channels}\n"
            f"peak        {self.peak:.6f}  ({self.peak_db:.2f} dBFS)\n"
            f"rms         {self.rms:.6f}  ({self.rms_db:.2f} dBFS)\n"
            f"dc offset   {dc}\n"
            f"clipped     {self.clipped_samples} samples\n"
            f"max silence {self.longest_silence:.3f}s\n"
            f"silent      {self.silent}"
        )


def _longest_silence(samples: np.ndarray, sample_rate: int, threshold: float) -> float:
    """Longest run, in seconds, where every channel sits below `threshold`."""
    quiet = np.all(np.abs(samples) < threshold, axis=1)
    if not quiet.any():
        return 0.0
    # Run-length encode the boolean mask by finding transition points.
    padded = np.concatenate(([False], quiet, [False]))
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    runs = edges[1::2] - edges[::2]
    return float(runs.max() / sample_rate) if runs.size else 0.0


def measure(
    audio: Audio,
    *,
    silence_threshold: float = 1e-4,
    profile_seconds: float = 1.0,
) -> Metrics:
    samples = audio.samples
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    rms = float(np.sqrt(np.mean(samples**2))) if samples.size else 0.0
    dc = tuple(float(x) for x in samples.mean(axis=0)) if samples.size else ()
    clipped = int(np.count_nonzero(np.abs(samples) >= FULL_SCALE_EPSILON))

    window = max(1, int(profile_seconds * audio.sample_rate))
    whole = (audio.frames // window) * window
    if whole:
        blocks = samples[:whole].reshape(-1, window, audio.channels)
        profile = [float(x) for x in np.sqrt((blocks**2).mean(axis=(1, 2)))]
    else:
        profile = []

    return Metrics(
        peak=peak,
        peak_db=to_db(peak),
        rms=rms,
        rms_db=to_db(rms),
        dc_offset=dc,
        clipped_samples=clipped,
        longest_silence=_longest_silence(samples, audio.sample_rate, silence_threshold),
        silent=peak < silence_threshold,
        duration=audio.duration,
        sample_rate=audio.sample_rate,
        channels=audio.channels,
        rms_profile=profile,
    )


@dataclass(frozen=True)
class SeamMetrics:
    """How badly a render jumps when its end is spliced back to its start.

    A loop that clicks does so because sample N-1 and sample 0 sit far apart, or
    because the slopes on either side disagree. Both are measurable without
    hearing the click.
    """

    step: float
    step_db: float
    slope_delta: float
    head_rms: float
    tail_rms: float
    level_ratio_db: float

    def describe(self) -> str:
        return (
            f"seam step      {self.step:.6f}  ({self.step_db:.2f} dBFS)\n"
            f"slope delta    {self.slope_delta:.6f}\n"
            f"head/tail rms  {self.head_rms:.6f} / {self.tail_rms:.6f}"
            f"  ({self.level_ratio_db:+.2f} dB)"
        )


def measure_seam(audio: Audio, *, window_seconds: float = 0.5) -> SeamMetrics:
    samples = audio.samples
    if audio.frames < 4:
        raise ValueError("too few frames to measure a seam")

    first, last = samples[0], samples[-1]
    step = float(np.abs(first - last).max())

    # Slope continuity: the delta arriving at the end should resemble the delta
    # leaving the start, otherwise the waveform corners even if it doesn't jump.
    incoming = samples[-1] - samples[-2]
    outgoing = samples[1] - samples[0]
    slope_delta = float(np.abs(outgoing - incoming).max())

    window = min(max(1, int(window_seconds * audio.sample_rate)), audio.frames // 2)
    head_rms = float(np.sqrt(np.mean(samples[:window] ** 2)))
    tail_rms = float(np.sqrt(np.mean(samples[-window:] ** 2)))
    ratio = to_db(head_rms) - to_db(tail_rms) if tail_rms > 0 else -math.inf

    return SeamMetrics(
        step=step,
        step_db=to_db(step),
        slope_delta=slope_delta,
        head_rms=head_rms,
        tail_rms=tail_rms,
        level_ratio_db=ratio,
    )
