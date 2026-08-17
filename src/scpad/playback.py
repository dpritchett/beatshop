"""Playback via the Windows audio stack, for human listening only.

Deliberately quarantined. Nothing in the render or verification path imports
this module, and no test may call it. WSL2's own audio (PulseAudio under WSLg)
is unreliable enough that anything it told us would be worse than no signal at
all, so we never use it -- we hand the file to Windows and let Windows play it.

The mechanism is `System.Media.SoundPlayer` over `powershell.exe`: no GUI app
opens, no WSL audio device is touched. SoundPlayer only reliably handles 16-bit
PCM, so we stage a converted copy rather than feeding it our 24-bit renders.
"""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

import numpy as np

from .wavio import read_wav

_STAGING_DIRNAME = "scpad-playback"


class PlaybackError(RuntimeError):
    pass


def _powershell(script: str, *, timeout: float | None = 30.0) -> str:
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=timeout,
            # Explicit: the returncode check below raises PlaybackError with the
            # actual stderr in it, which is more use than CalledProcessError.
            check=False,
        )
    except FileNotFoundError as exc:
        raise PlaybackError(
            "powershell.exe not found -- playback needs WSL interop enabled"
        ) from exc
    if proc.returncode != 0:
        raise PlaybackError(
            f"powershell failed: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def _to_wsl_path(windows_path: str) -> Path:
    proc = subprocess.run(
        ["wslpath", "-u", windows_path], capture_output=True, text=True, check=True
    )
    return Path(proc.stdout.strip())


def _to_windows_path(path: Path) -> str:
    proc = subprocess.run(
        ["wslpath", "-w", str(path)], capture_output=True, text=True, check=True
    )
    return proc.stdout.strip()


def write_wav16(path: Path, samples: np.ndarray, sample_rate: int) -> None:
    """Write float samples as 16-bit PCM.

    Playback-only, so plain rounding is fine -- no dither. This never touches a
    rendered artifact; it only produces the throwaway staging copy.
    """
    clipped = np.clip(samples, -1.0, 1.0)
    ints = np.round(clipped * 32767.0).astype("<i2")
    payload = ints.tobytes()
    channels = samples.shape[1]
    byte_rate = sample_rate * channels * 2

    header = b"RIFF" + struct.pack("<I", 36 + len(payload)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, channels * 2, 16
    )
    header += b"data" + struct.pack("<I", len(payload))
    path.write_bytes(header + payload)


def stage(source: Path) -> Path:
    """Copy `source` into Windows-visible temp as 16-bit PCM. Returns WSL path."""
    audio = read_wav(source)
    staging_root = _to_wsl_path(_powershell("$env:TEMP")) / _STAGING_DIRNAME
    staging_root.mkdir(parents=True, exist_ok=True)
    staged = staging_root / f"{source.stem}-16bit.wav"
    write_wav16(staged, audio.samples, audio.sample_rate)
    return staged


def play(source: str | Path, *, wait: bool = True) -> Path:
    """Play `source` through Windows. Returns the staged file it played.

    With `wait=False` the call returns immediately and playback continues in a
    detached powershell process -- useful for long ambient renders where
    blocking for the full duration is not what anyone wants.
    """
    path = Path(source)
    if not path.is_file():
        raise PlaybackError(f"no such file: {path}")

    staged = stage(path)
    windows_path = _to_windows_path(staged).replace("'", "''")
    script = f"(New-Object Media.SoundPlayer '{windows_path}').PlaySync()"

    if wait:
        audio = read_wav(staged)
        _powershell(script, timeout=audio.duration + 30.0)
    else:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return staged
