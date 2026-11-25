"""Public reader for FUN Labs' binary BiN ``.anim`` files."""

from __future__ import annotations

from typing import Any

from .binfile import ParseError, parse_anim


def read_anim(data: bytes) -> dict[str, Any]:
    """Parse raw BiN .anim bytes into controller/keyframe tracks."""
    return parse_anim(data)


def inspect_anim(data: bytes) -> dict[str, Any]:
    """Compact, JSON-friendly summary used by ``razor-inspect``."""
    result = read_anim(data)
    return {
        "kind": "anim",
        "tracks": len(result["tracks"]),
        "controllers": sorted({t.get("controller", "") for t in result["tracks"]}),
        "targets": [t.get("target") for t in result["tracks"]
                    if t.get("target")],
    }


__all__ = ["ParseError", "inspect_anim", "read_anim"]
