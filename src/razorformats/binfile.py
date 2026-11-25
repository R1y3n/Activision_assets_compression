#!/usr/bin/env python3
"""Standalone, standard-library-only parser for Shadow Force: Razor Unit BiN files.

Usage:
    python3 parser.py <file.obj|.anim> [--json out.json] [--obj out.obj]

The extension is only a hint: files are identified by the ``BiN\0`` magic and
by which parser consumes the whole file.  ``--obj`` writes a Wavefront OBJ
file for .obj meshes (positions, normals, UVs, faces); ``--json`` writes the
complete parsed structure, including mesh vertices/faces/weights.

See docs/bin_obj.md, docs/bin_anim.md and FINDINGS.md for the format notes.
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys

MAGIC = b"BiN\x00"

# Node flags carrying an extra 12-byte f32[3] field after the transform.
NODE_EXTRA_FLAGS = (3, 4)

# .anim sub-controller keyframe stride in bytes: u32 time + payload.
KEY_STRIDES = {
    "Pos": 16,        # u32 time + f32 pos[3]
    "Rot": 20,        # u32 time + f32 quat[4]
    "PosRot": 32,     # u32 time + f32 pos[3] + f32 quat[4]
    "PosRotVis": 34,  # PosRot + u16 visibility  (inferred from ASCII 'Vis')
    "Vis": 6,         # u32 time + u16 visibility (inferred, not observed)
}

# Light-animation property key size in bytes (Firelight controller).
LIGHT_PROP_SIZES = {
    "Color": 12,   # f32 rgb
    "PosRot": 28,  # f32 pos[3] + f32 quat[4] (no time field)
}
LIGHT_PROP_DEFAULT = 4  # Intens, Falloff, Hotspot, AttenStart, Radius, etc.


class ParseError(Exception):
    """Raised when the input does not match the expected BiN layout."""


class Reader:
    def __init__(self, data: bytes, offset: int = 0):
        self.data = data
        self.offset = offset

    def need(self, n: int) -> None:
        if n < 0 or self.offset + n > len(self.data):
            raise ParseError(
                "truncated at offset 0x%x (need %d bytes, file size 0x%x)"
                % (self.offset, n, len(self.data))
            )

    def u8(self) -> int:
        self.need(1)
        v = self.data[self.offset]
        self.offset += 1
        return v

    def u16(self) -> int:
        self.need(2)
        v = struct.unpack_from("<H", self.data, self.offset)[0]
        self.offset += 2
        return v

    def u32(self) -> int:
        self.need(4)
        v = struct.unpack_from("<I", self.data, self.offset)[0]
        self.offset += 4
        return v

    def f32(self, count: int = 1) -> tuple:
        self.need(4 * count)
        v = struct.unpack_from("<%df" % count, self.data, self.offset)
        self.offset += 4 * count
        return v

    def string(self) -> str:
        """u8 byte length followed by raw ASCII/Latin-1 bytes (no NUL)."""
        n = self.u8()
        self.need(n)
        raw = self.data[self.offset:self.offset + n]
        self.offset += n
        return raw.decode("latin-1")

    def raw(self, n: int) -> bytes:
        self.need(n)
        v = self.data[self.offset:self.offset + n]
        self.offset += n
        return v


def check_magic(data: bytes) -> None:
    if len(data) < 6 or data[:4] != MAGIC:
        raise ParseError("not a BiN file (expected magic 'BiN\\0')")


def read_node(r: Reader) -> dict:
    """One scene-graph node / skeleton bone record (28 or 40 bytes + strings)."""
    start = r.offset
    parent = r.string()
    name = r.string()
    flags = r.u16()
    translation = r.f32(3)
    rotation = r.f32(4)
    extra = r.f32(3) if flags in NODE_EXTRA_FLAGS else None
    return {
        "offset": start,
        "parent": parent,
        "name": name,
        "flags": flags,
        "translation": list(translation),
        "rotation": list(rotation),
        "extra_f32_3": list(extra) if extra is not None else None,
    }


def parse_obj(data: bytes) -> dict:
    """Parse a BiN .obj (mesh) file."""
    check_magic(data)
    r = Reader(data, 4)
    node_count = r.u16()
    nodes = [read_node(r) for _ in range(node_count)]

    meshes = []
    while r.offset < len(data):
        start = r.offset
        name = r.string()
        mesh_type = r.u16()
        shader = r.string()

        bones = []
        if mesh_type == 2:  # skinned
            bone_count = r.u16()
            bones = [read_node(r) for _ in range(bone_count)]

        vertex_count = r.u16()

        weights = []
        if mesh_type == 2:
            # One 8-byte skin-weight record per vertex, stored before the
            # vertex block: f32 weight + u16 bone0 + u16 bone1.
            for _ in range(vertex_count):
                w = r.f32(1)[0]
                b0 = r.u16()
                b1 = r.u16()
                weights.append({"weight": w, "bone0": b0, "bone1": b1})

        vertices = []
        for _ in range(vertex_count):
            x, y, z, nx, ny, nz, u, v = r.f32(8)
            vertices.append({
                "position": [x, y, z],
                "normal": [nx, ny, nz],
                "uv": [u, v],
            })

        triangle_count = r.u16()
        indices = list(struct.unpack_from("<%dH" % (triangle_count * 3), data, r.offset))
        r.offset += triangle_count * 3 * 2
        faces = [list(indices[i:i + 3]) for i in range(0, len(indices), 3)]

        meshes.append({
            "offset": start,
            "name": name,
            "type": mesh_type,
            "shader": shader,
            "bones": bones,
            "weights": weights,
            "vertices": vertices,
            "faces": faces,
            "vertex_count": vertex_count,
            "triangle_count": triangle_count,
        })

    if r.offset != len(data):
        raise ParseError("trailing bytes after last mesh: 0x%x != 0x%x" % (r.offset, len(data)))
    return {"kind": "obj", "nodes": nodes, "meshes": meshes}


def read_keyframe(r: Reader, sub_type: str) -> dict:
    """One u32-time + payload keyframe of a keyframe controller track."""
    stride = KEY_STRIDES.get(sub_type)
    if stride is None:
        raise ParseError("unknown sub-controller type %r" % sub_type)
    start = r.offset
    time = r.u32()
    if sub_type == "Pos":
        payload = list(r.f32(3))
    elif sub_type == "Rot":
        payload = list(r.f32(4))
    elif sub_type in ("PosRot", "PosRotVis"):
        pos = list(r.f32(3))
        rot = list(r.f32(4))
        payload = pos + rot
        if sub_type == "PosRotVis":
            payload.append(r.u16())
    else:  # Vis
        payload = [r.u16()]
    return {"offset": start, "time": time, "values": payload}


def read_texture_track(r: Reader, controller: str, sub_type: str) -> dict:
    """AnimCtrl_Texture / LOD_1: indexed channel values over keyframes.

    Layout verified against all 98 ``blink.anim`` entries in ``objects.fun``:
    u16 key count, u16 channel count, that many u16 channel indices, then one
    record per key of ``u32 time + f32[2 * channel_count]``.  The values are
    most likely colour/envelope parameters for the listed mesh material
    channels; their exact semantics are not needed to preserve them.
    """
    start = r.offset
    key_count = r.u16()
    channel_count = r.u16()
    channels = [r.u16() for _ in range(channel_count)]
    keyframes = []
    for _ in range(key_count):
        key_offset = r.offset
        time = r.u32()
        values = list(r.f32(2 * channel_count))
        keyframes.append({"offset": key_offset, "time": time,
                          "values": values})
    return {
        "offset": start,
        "controller": controller,
        "type": sub_type,
        "channels": channels,
        "keyframes": keyframes,
    }


def read_light_track(r: Reader, controller: str, sub_type: str) -> dict:
    """AnimCtrl_Light / Firelight: u32 duration, u16 prop count, then properties."""
    duration = r.u32()
    prop_count = r.u16()
    props = []
    for _ in range(prop_count):
        name = r.string()
        key_count = r.u16()
        size = LIGHT_PROP_SIZES.get(name, LIGHT_PROP_DEFAULT)
        if size % 4:
            raise ParseError("odd light property size for %r" % name)
        values = list(r.f32(size // 4)) if key_count == 1 else None
        if values is None:
            values = []
            for _ in range(key_count):
                values.extend(r.f32(size // 4))
        props.append({"name": name, "key_count": key_count, "values": values})
    return {
        "controller": controller,
        "type": sub_type,
        "duration": duration,
        "properties": props,
    }


def parse_anim(data: bytes) -> dict:
    """Parse a BiN .anim (animation) file."""
    check_magic(data)
    r = Reader(data, 4)
    track_count = r.u16()
    tracks = []
    for _ in range(track_count):
        start = r.offset
        controller = r.string()
        sub_type = r.string()
        if controller == "AnimCtrl_Light":
            track = read_light_track(r, controller, sub_type)
            track["offset"] = start
            tracks.append(track)
            continue
        if controller == "AnimCtrl_Texture":
            tracks.append(read_texture_track(r, controller, sub_type))
            continue
        target = r.string()
        key_count = r.u16()
        keys = [read_keyframe(r, sub_type) for _ in range(key_count)]
        tracks.append({
            "offset": start,
            "controller": controller,
            "type": sub_type,
            "target": target,
            "keyframes": keys,
        })

    if r.offset != len(data):
        raise ParseError("trailing bytes after last track: 0x%x != 0x%x" % (r.offset, len(data)))
    return {"kind": "anim", "tracks": tracks}


def write_wavefront(obj: dict, path: str) -> None:
    """Write all meshes in an .obj parse result as a Wavefront .obj file."""
    vertex_base = 1
    with open(path, "w", encoding="ascii") as f:
        f.write("# Generated by parser.py from a Razor Unit BiN .obj\n")
        for mi, mesh in enumerate(obj["meshes"]):
            f.write("g %s\n" % (mesh["name"] or "mesh%d" % mi))
            for v in mesh["vertices"]:
                p, n, uv = v["position"], v["normal"], v["uv"]
                f.write("v %.6f %.6f %.6f\n" % tuple(p))
                f.write("vn %.6f %.6f %.6f\n" % tuple(n))
                f.write("vt %.6f %.6f\n" % tuple(uv))
            for face in mesh["faces"]:
                a, b, c = [i + vertex_base for i in face]
                f.write("f %d/%d/%d %d/%d/%d %d/%d/%d\n" % (a, a, a, b, b, b, c, c, c))
            vertex_base += len(mesh["vertices"])


def summarize(result: dict) -> str:
    lines = []
    if result["kind"] == "obj":
        lines.append("BiN .obj: %d nodes, %d meshes" % (len(result["nodes"]), len(result["meshes"])))
        for m in result["meshes"]:
            lines.append(
                "  mesh %-16r type=%d shader=%-20r verts=%d tris=%d bones=%d weights=%d"
                % (m["name"], m["type"], m["shader"], m["vertex_count"],
                   m["triangle_count"], len(m["bones"]), len(m["weights"]))
            )
    else:
        lines.append("BiN .anim: %d tracks" % len(result["tracks"]))
        for t in result["tracks"]:
            if t["controller"] == "AnimCtrl_Light":
                lines.append("  track %-18r %-10r duration=%d props=%d"
                             % (t["controller"], t["type"], t["duration"], len(t["properties"])))
            elif t["controller"] == "AnimCtrl_Texture":
                lines.append("  track %-18r %-10r channels=%d keys=%d"
                             % (t["controller"], t["type"], len(t["channels"]),
                                len(t["keyframes"])))
            else:
                lines.append("  track %-18r %-10r target=%-20r keys=%d"
                             % (t["controller"], t["type"], t["target"], len(t["keyframes"])))
    return "\n".join(lines)


def parse_file(path: str, kind_hint: str | None = None) -> dict:
    with open(path, "rb") as f:
        data = f.read()
    check_magic(data)

    parsers = [parse_obj, parse_anim]
    if kind_hint == "obj":
        parsers = [parse_obj, parse_anim]
    elif kind_hint == "anim":
        parsers = [parse_anim, parse_obj]

    errors = []
    for parser in parsers:
        try:
            return parser(data)
        except ParseError as e:
            errors.append("%s: %s" % (parser.__name__, e))
    raise ParseError("could not parse %s\n  %s" % (path, "\n  ".join(errors)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help="BiN .obj or .anim file")
    ap.add_argument("--json", metavar="OUT", help="write full parse result as JSON")
    ap.add_argument("--obj", metavar="OUT", help="write .obj meshes as a Wavefront OBJ file")
    ap.add_argument("--force-obj", action="store_true", help="force the .obj parser")
    ap.add_argument("--force-anim", action="store_true", help="force the .anim parser")
    args = ap.parse_args(argv)

    hint = None
    if args.force_obj:
        hint = "obj"
    elif args.force_anim:
        hint = "anim"
    else:
        ext = os.path.splitext(args.file)[1].lower()
        if ext in (".obj", "_obj"):
            hint = "obj"
        elif ext in (".anim", "_anim"):
            hint = "anim"

    try:
        result = parse_file(args.file, hint)
    except (ParseError, OSError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1

    print(summarize(result))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print("wrote JSON: %s" % args.json)

    if args.obj:
        if result["kind"] != "obj":
            print("warning: --obj ignored for an .anim file", file=sys.stderr)
        else:
            write_wavefront(result, args.obj)
            print("wrote OBJ: %s" % args.obj)
    return 0


if __name__ == "__main__":
    sys.exit(main())
