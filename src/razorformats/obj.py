"""Public reader for FUN Labs' binary BiN ``.obj`` mesh files."""

from __future__ import annotations

from typing import Any

from .binfile import ParseError, parse_obj


def read_obj(data: bytes) -> dict[str, Any]:
    """Parse raw BiN .obj bytes into scene nodes and meshes."""
    return parse_obj(data)


def inspect_obj(data: bytes) -> dict[str, Any]:
    """Compact, JSON-friendly summary used by ``razor-inspect``."""
    result = read_obj(data)
    return {
        "kind": "obj",
        "nodes": len(result["nodes"]),
        "meshes": [
            {
                "name": mesh["name"],
                "type": mesh["type"],
                "shader": mesh["shader"],
                "vertices": mesh["vertex_count"],
                "triangles": mesh["triangle_count"],
                "bones": len(mesh["bones"]),
            }
            for mesh in result["meshes"]
        ],
    }


__all__ = ["ParseError", "inspect_obj", "read_obj"]
