"""Reader for the Lua-like ``.fx`` particle-system scripts."""

from __future__ import annotations

from typing import Any

from .dataformats import parse_script as _parse_script


def parse_fx(data: bytes, source: str = "") -> dict[str, Any]:
    """Parse a particle effect script into function/call trees."""
    return _parse_script(data, source, kind="fx")


__all__ = ["parse_fx"]
