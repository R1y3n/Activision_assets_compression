"""Reader for ``SFNT`` bitmap-font containers."""

from __future__ import annotations

from typing import Any

from .dataformats import parse_fnt as _parse_fnt


def read_fnt(data: bytes, source: str = "") -> dict[str, Any]:
    """Parse the 28-byte header and 94 glyph records of an ``.fnt`` file."""
    return _parse_fnt(data, source)


__all__ = ["read_fnt"]
