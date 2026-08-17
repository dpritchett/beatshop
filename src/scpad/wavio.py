"""Minimal RIFF/WAVE reader.

We render 24-bit PCM, which Python's stdlib `wave` module cannot unpack into
samples, and pulling in libsndfile bindings just to read our own output is more
dependency than the job needs. Parsing RIFF chunks directly also means the
analysis path can assert on header layout, which is how we caught that scsynth
writes no timestamp chunk (and is therefore byte-reproducible).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# WAVE_FORMAT_* tags we accept from scsynth.
_FORMAT_PCM = 0x0001
_FORMAT_FLOAT = 0x0003
_FORMAT_EXTENSIBLE = 0xFFFE


@dataclass(frozen=True)
class Audio:
    """Decoded audio, normalized to float64 in nominal [-1.0, 1.0]."""

    samples: np.ndarray  # shape (frames, channels)
    sample_rate: int
    bit_depth: int
    data_offset: int  # byte offset of the data chunk payload
    chunk_ids: tuple[str, ...]

    @property
    def frames(self) -> int:
        return int(self.samples.shape[0])

    @property
    def channels(self) -> int:
        return int(self.samples.shape[1])

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate


def _decode_pcm(payload: bytes, channels: int, bit_depth: int, fmt: int) -> np.ndarray:
    if fmt == _FORMAT_FLOAT:
        dtype = {32: "<f4", 64: "<f8"}[bit_depth]
        flat = np.frombuffer(payload, dtype=dtype).astype(np.float64)
    elif bit_depth == 24:
        # 24-bit has no numpy dtype. Widen each 3-byte little-endian sample into
        # the high 3 bytes of an int32 so the sign bit lands correctly, then
        # shift back down.
        raw = np.frombuffer(payload, dtype=np.uint8)
        usable = (raw.size // 3) * 3
        triples = raw[:usable].reshape(-1, 3).astype(np.uint32)
        packed = (triples[:, 0] << 8) | (triples[:, 1] << 16) | (triples[:, 2] << 24)
        flat = (packed.astype(np.int32) >> 8).astype(np.float64) / (2**23)
    else:
        dtype = {8: "u1", 16: "<i2", 32: "<i4"}[bit_depth]
        ints = np.frombuffer(payload, dtype=dtype)
        if bit_depth == 8:
            flat = (ints.astype(np.float64) - 128.0) / 128.0
        else:
            flat = ints.astype(np.float64) / float(2 ** (bit_depth - 1))

    usable_frames = flat.size // channels
    return flat[: usable_frames * channels].reshape(usable_frames, channels)


def read_wav(path: str | Path) -> Audio:
    data = Path(path).read_bytes()
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError(f"{path}: not a RIFF/WAVE file")

    pos = 12
    chunk_ids: list[str] = []
    fmt = channels = sample_rate = bit_depth = 0
    payload = b""
    data_offset = 0

    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (size,) = struct.unpack("<I", data[pos + 4 : pos + 8])
        body = data[pos + 8 : pos + 8 + size]
        chunk_ids.append(chunk_id.decode("ascii", "replace"))

        if chunk_id == b"fmt ":
            fmt, channels, sample_rate = struct.unpack("<HHI", body[:8])
            (bit_depth,) = struct.unpack("<H", body[14:16])
            if fmt == _FORMAT_EXTENSIBLE and len(body) >= 26:
                # The real format tag lives at the front of the GUID.
                (fmt,) = struct.unpack("<H", body[24:26])
        elif chunk_id == b"data":
            payload, data_offset = body, pos + 8

        # Chunks are word-aligned; odd sizes carry a pad byte.
        pos += 8 + size + (size & 1)

    if not channels or not payload:
        raise ValueError(f"{path}: missing fmt or data chunk")
    if fmt not in (_FORMAT_PCM, _FORMAT_FLOAT):
        raise ValueError(f"{path}: unsupported format tag 0x{fmt:04X}")

    return Audio(
        samples=_decode_pcm(payload, channels, bit_depth, fmt),
        sample_rate=sample_rate,
        bit_depth=bit_depth,
        data_offset=data_offset,
        chunk_ids=tuple(chunk_ids),
    )
