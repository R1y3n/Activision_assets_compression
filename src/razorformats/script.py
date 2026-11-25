"""Reader for the Lua-like ``.script`` shader definition language."""

from __future__ import annotations

from typing import Any

from .dataformats import parse_script as _parse_script


def parse_script(data: bytes, source: str = "") -> dict[str, Any]:
    """Parse a shader script into function/block/call trees."""
    return _parse_script(data, source, kind="script")


__all__ = ["parse_script"]
