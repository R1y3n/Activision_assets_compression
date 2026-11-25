"""Reader for the game's RIFF/WAVE PCM files."""

from __future__ import annotations

from typing import Any

from .dataformats import parse_wav as _parse_wav


def read_wav_header(data: bytes, source: str = "") -> dict[str, Any]:
    """Walk RIFF chunks and decode the nonstandard 20-byte ``fmt `` header."""
    return _parse_wav(data, source)


__all__ = ["read_wav_header"]
