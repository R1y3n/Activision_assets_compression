"""Reader for the Lua-like ``.model`` definition scripts."""

from __future__ import annotations

from typing import Any

from .dataformats import parse_model as _parse_model


def parse_model(data: bytes, source: str = "") -> dict[str, Any]:
    """Parse a model definition into call/conditional trees and a summary."""
    return _parse_model(data, source)


__all__ = ["parse_model"]
