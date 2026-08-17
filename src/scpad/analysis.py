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


@dataclass(frozen=True)
class SweepMetrics:
    """Whether a one-shot actually travels, and where its two peaks land.

    `measure()` says a whoosh is not silent and not clipped. It cannot say
    whether the filter opened, or whether the thing is a low thump with some
    noise on top. These are the numbers that answer that, so the shape of a
    gesture can be asserted rather than auditioned.

    Times are in seconds from the start of the file. `low_fraction` is the share
    of total energy below `low_hz`, which is the thump-versus-air ratio.
    """

    peak_level_at: float
    peak_centroid_at: float
    centroid_start: float
    centroid_peak: float
    centroid_end: float
    low_fraction: float
    frames: list[tuple[float, float, float]] = field(default_factory=list)

    def describe(self) -> str:
        return (
            f"loudest at     {self.peak_level_at:.3f}s\n"
            f"brightest at   {self.peak_centroid_at:.3f}s\n"
            f"centroid       {self.centroid_start:.0f} -> {self.centroid_peak:.0f} -> "
            f"{self.centroid_end:.0f} Hz\n"
            f"low fraction   {self.low_fraction:.1%}"
        )


def measure_sweep(
    audio: Audio,
    *,
    window_seconds: float = 0.012,
    low_hz: float = 400.0,
    floor_db: float = -45.0,
) -> SweepMetrics:
    mono = audio.samples.mean(axis=1)
    size = 1 << max(6, int(window_seconds * audio.sample_rate)).bit_length()
    if mono.size < size * 2:
        raise ValueError("too few frames to measure a sweep")

    window = np.hanning(size)
    freqs = np.fft.rfftfreq(size, 1.0 / audio.sample_rate)
    # Frames quieter than this are all filter ringing and dither, and their
    # centroids are noise. Including them makes the tail look bright.
    floor = float(np.abs(mono).max()) * (10.0 ** (floor_db / 20.0))

    frames: list[tuple[float, float, float]] = []
    for start in range(0, mono.size - size, size // 2):
        chunk = mono[start : start + size]
        rms = float(np.sqrt(np.mean(chunk**2)))
        if rms < floor:
            continue
        spectrum = np.abs(np.fft.rfft(chunk * window))
        total = float(spectrum.sum())
        if total <= 0.0:
            continue
        frames.append(
            (start / audio.sample_rate, rms, float((freqs * spectrum).sum() / total))
        )
    if not frames:
        raise ValueError("no frames above the silence floor; nothing to measure")

    whole = np.abs(np.fft.rfft(mono * np.hanning(mono.size))) ** 2
    whole_freqs = np.fft.rfftfreq(mono.size, 1.0 / audio.sample_rate)

    return SweepMetrics(
        peak_level_at=max(frames, key=lambda f: f[1])[0],
        peak_centroid_at=max(frames, key=lambda f: f[2])[0],
        centroid_start=frames[0][2],
        centroid_peak=max(f[2] for f in frames),
        centroid_end=frames[-1][2],
        low_fraction=float(whole[whole_freqs < low_hz].sum() / whole.sum()),
        frames=frames,
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
