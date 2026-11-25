#!/usr/bin/env python3
"""Standard-library parser for Shadow Force: Razor Unit .bsp files.

The format is a 92-byte header followed by a forward chain of self-sized
chunks: [u32 type][u32 payload_size][payload].  This parser validates that the
chain ends exactly at EOF and decodes the chunk types whose layouts are known.

Usage:
    python3 parser.py <file.bsp>
    python3 parser.py <file.bsp> --json out.json
    python3 parser.py <file.bsp> --json -
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

HEADER_SIZE = 92
CHUNK_HEADER = struct.Struct("<II")

ENTITY_RE = re.compile(r'entity\("([^"]*)"\)')
PROPERTY_RE = re.compile(r'\tproperty\("([^"]*)", "(.*)"\)')


def hx(value: int) -> str:
    return f"0x{value:x}"


def cstring(raw: bytes) -> str:
    """Decode a NUL-padded ASCII/Latin-1 field."""
    end = raw.find(b"\x00")
    if end >= 0:
        raw = raw[:end]
    return raw.decode("latin-1", errors="replace")


def mostly_ascii(raw: bytes) -> bool:
    if not raw:
        return False
    sample = raw[:256]
    printable = sum(1 for b in sample if b in (9, 10, 13) or 32 <= b < 127)
    return printable / len(sample) >= 0.85


def need(data: bytes, offset: int, size: int, what: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ValueError(
            f"truncated {what}: need {size} bytes at {hx(offset)}, "
            f"file is {len(data)} bytes"
        )


def parse_header(data: bytes) -> Dict[str, Any]:
    need(data, 0, HEADER_SIZE, "header")
    u32 = lambda off: struct.unpack_from("<I", data, off)[0]
    reserved = struct.unpack_from("<6I", data, 0x30)
    return {
        "version": u32(0x00),
        "header_size": u32(0x04),
        "subversion": u32(0x08),
        "build": u32(0x0C),
        "flag_0x10": u32(0x10),
        "flag_0x14": u32(0x14),
        "flag_0x18": u32(0x18),
        "flag_0x1c": u32(0x1C),
        "mode_0x20": u32(0x20),
        "flag_0x24": u32(0x24),
        "author_tag": cstring(data[0x28:0x30]),
        "reserved": list(reserved),
        "counters": [u32(0x48), u32(0x4C), u32(0x50), u32(0x54)],
        "unknown_0x58": u32(0x58),
    }


def walk_chunks(data: bytes) -> List[Dict[str, Any]]:
    chunks: List[Dict[str, Any]] = []
    offset = HEADER_SIZE
    index = 0
    while offset < len(data):
        need(data, offset, CHUNK_HEADER.size, "chunk header")
        ctype, size = CHUNK_HEADER.unpack_from(data, offset)
        payload_offset = offset + CHUNK_HEADER.size
        payload_end = payload_offset + size
        if payload_end > len(data):
            raise ValueError(
                f"chunk {index} type {ctype} at {hx(offset)} claims {size} bytes, "
                f"ending at {hx(payload_end)} past EOF {hx(len(data))}"
            )
        info: Dict[str, Any] = {
            "index": index,
            "type": ctype,
            "chunk_offset": offset,
            "chunk_offset_hex": hx(offset),
            "payload_offset": payload_offset,
            "payload_offset_hex": hx(payload_offset),
            "payload_size": size,
            "payload_end": payload_end,
            "payload_end_hex": hx(payload_end),
        }
        if size >= 4:
            info["first_u32"] = struct.unpack_from("<I", data, payload_offset)[0]
        else:
            info["first_u32"] = None
        chunks.append(info)
        offset = payload_end
        index += 1
    if offset != len(data):
        raise ValueError(f"chunk chain ended at {hx(offset)} but file size is {hx(len(data))}")
    return chunks


def decode_planes(payload: bytes) -> Dict[str, Any]:
    if len(payload) == 0:
        return {"kind": "empty"}
    if len(payload) < 4:
        return {"kind": "malformed", "reason": "payload shorter than count"}
    count = struct.unpack_from("<I", payload, 0)[0]
    stride = 20
    expected = 4 + count * stride
    records = []
    usable = min(count, max(0, (len(payload) - 4) // stride))
    for i in range(usable):
        nx, ny, nz, dist, flags = struct.unpack_from("<ffffI", payload, 4 + i * stride)
        records.append(
            {
                "nx": nx,
                "ny": ny,
                "nz": nz,
                "distance": dist,
                "flags": flags,
            }
        )
    return {
        "kind": "plane_table",
        "count": count,
        "record_size": stride,
        "expected_payload_size": expected,
        "size_matches": expected == len(payload),
        "records": records,
    }


def decode_bounds(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 4:
        return {"kind": "malformed", "reason": "payload shorter than count"}
    count = struct.unpack_from("<I", payload, 0)[0]
    stride = 36
    expected = 4 + count * stride
    records = []
    usable = min(count, max(0, (len(payload) - 4) // stride))
    for i in range(usable):
        a, b, c = struct.unpack_from("<III", payload, 4 + i * stride)
        bounds = struct.unpack_from("<6f", payload, 16 + i * stride)
        records.append({"a": a, "b": b, "c": c, "bounds": list(bounds)})
    return {
        "kind": "bounds_table",
        "count": count,
        "record_size": stride,
        "expected_payload_size": expected,
        "size_matches": expected == len(payload),
        "records": records,
    }


def decode_textures(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 4:
        return {"kind": "malformed", "reason": "payload shorter than count"}
    count = struct.unpack_from("<I", payload, 0)[0]
    stride = 40
    expected = 4 + count * stride
    records = []
    usable = min(count, max(0, (len(payload) - 4) // stride))
    for i in range(usable):
        off = 4 + i * stride
        name = cstring(payload[off : off + 32])
        zero, sentinel = struct.unpack_from("<II", payload, off + 32)
        records.append({"name": name, "zero": zero, "sentinel": sentinel})
    return {
        "kind": "texture_dictionary",
        "count": count,
        "record_size": stride,
        "expected_payload_size": expected,
        "size_matches": expected == len(payload),
        "records": records,
    }


def decode_entities(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 4:
        return {"kind": "malformed", "reason": "payload shorter than text length"}
    text_length = struct.unpack_from("<I", payload, 0)[0]
    raw = payload[4 : 4 + text_length]
    text = raw.decode("latin-1", errors="replace").rstrip("\x00")
    entities: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    property_count = 0
    for line in text.splitlines():
        m = ENTITY_RE.fullmatch(line)
        if m:
            current = {"class": m.group(1), "properties": {}}
            entities.append(current)
            continue
        m = PROPERTY_RE.fullmatch(line)
        if m and current is not None:
            current["properties"][m.group(1)] = m.group(2)
            property_count += 1
    return {
        "kind": "entity_text",
        "text_length": text_length,
        "text_ends_at_payload_end": 4 + text_length == len(payload),
        "has_trailing_nul": raw.endswith(b"\x00"),
        "text_preview": text[:600],
        "entity_count": len(entities),
        "property_count": property_count,
        "entities": entities,
    }


def decode_globals(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 12:
        return {"kind": "malformed", "reason": "type 10 payload is not 12 bytes"}
    values = struct.unpack_from("<3f", payload, 0)
    return {"kind": "three_floats", "values": list(values), "raw_hex": payload[:12].hex()}


def decode_misc11(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 20:
        return {"kind": "malformed", "reason": "type 11 payload shorter than 20 bytes"}
    magic, count, twelve, flag, start = struct.unpack_from("<5I", payload, 0)
    remainder = payload[20:]
    result: Dict[str, Any] = {
        "kind": "misc_effects",
        "magic": magic,
        "magic_ok": magic == 0xD431,
        "count": count,
        "field_12": twelve,
        "flag": flag,
        "start": start,
        "remainder_size": len(remainder),
        "remainder_hex": remainder[:64].hex(),
    }
    if mostly_ascii(remainder):
        result["remainder_text"] = remainder.decode("latin-1", errors="replace")[:2000]
    return result


def decode_models(payload: bytes) -> Dict[str, Any]:
    if len(payload) < 4:
        return {"kind": "malformed", "reason": "type 12 payload shorter than count"}
    count = struct.unpack_from("<I", payload, 0)[0]
    stride = 164
    header_end = 4 + count * stride
    if header_end > len(payload):
        return {
            "kind": "model_table",
            "count": count,
            "error": "declared model headers exceed payload size",
        }
    names = []
    for i in range(count):
        off = 4 + i * stride
        names.append(cstring(payload[off : off + 4]))
    tail = payload[header_end:]
    return {
        "kind": "model_table",
        "count": count,
        "header_record_size": stride,
        "model_names": names,
        "tail_size": len(tail),
        "tail_hex": tail[:64].hex(),
    }


def decode_asset14(payload: bytes) -> Dict[str, Any]:
    version = None
    if payload.startswith(b"Version "):
        version = payload[:12].decode("latin-1", errors="replace")
    return {
        "kind": "asset_data",
        "version": version,
        "prefix_hex": payload[:64].hex(),
    }


def decode_sky(payload: bytes) -> Dict[str, Any]:
    face_size = 0x2C28
    name_size = 32
    faces = []
    for i in range(6):
        off = i * face_size
        if off >= len(payload):
            break
        name = cstring(payload[off : off + name_size])
        faces.append(
            {
                "index": i,
                "name": name,
                "face_offset": off,
                "face_offset_hex": hx(off),
                "data_hex": payload[off + name_size : off + name_size + 32].hex(),
            }
        )
    return {
        "kind": "sky_cube",
        "payload_size": len(payload),
        "expected_payload_size": 6 * face_size,
        "size_matches": len(payload) == 6 * face_size,
        "face_size": face_size,
        "faces": faces,
    }


def decode_u16_table(payload: bytes) -> Dict[str, Any]:
    odd = len(payload) % 2 != 0
    usable = len(payload) - (1 if odd else 0)
    values = list(struct.unpack_from(f"<{usable // 2}H", payload, 0)) if usable else []
    return {
        "kind": "u16_table",
        "count": len(values),
        "odd_trailing_byte": odd,
        "sentinel_ffff_count": values.count(0xFFFF),
        "values": values,
    }


def decode_chunk(data: bytes, chunk: Dict[str, Any]) -> Dict[str, Any]:
    payload = data[chunk["payload_offset"] : chunk["payload_end"]]
    ctype = chunk["type"]
    if ctype == 0:
        return decode_planes(payload)
    if ctype == 1:
        return decode_bounds(payload)
    if ctype == 6:
        return decode_textures(payload)
    if ctype == 9:
        return decode_entities(payload)
    if ctype == 10:
        return decode_globals(payload)
    if ctype == 11:
        return decode_misc11(payload)
    if ctype == 12:
        return decode_models(payload)
    if ctype == 14:
        return decode_asset14(payload)
    if ctype == 17:
        return decode_sky(payload)
    if ctype == 20:
        return decode_u16_table(payload)
    if 2 <= ctype <= 8 or ctype in (16, 18, 19):
        return {
            "kind": "opaque",
            "first_u32": chunk.get("first_u32"),
            "prefix_hex": payload[:64].hex(),
        }
    return {"kind": "unknown", "prefix_hex": payload[:64].hex()}


def build_result(path: Path, data: bytes) -> Dict[str, Any]:
    header = parse_header(data)
    chunks = walk_chunks(data)
    for chunk in chunks:
        chunk["decoded"] = decode_chunk(data, chunk)

    by_type: Dict[int, List[Dict[str, Any]]] = {}
    for chunk in chunks:
        by_type.setdefault(chunk["type"], []).append(chunk)

    entity_docs: List[Dict[str, Any]] = []
    for chunk in by_type.get(9, []):
        decoded = chunk["decoded"]
        for entity in decoded.get("entities", []):
            entity_docs.append(entity)

    textures: List[str] = []
    for chunk in by_type.get(6, []):
        for record in chunk["decoded"].get("records", []):
            textures.append(record["name"])

    models: List[str] = []
    for chunk in by_type.get(12, []):
        models.extend(chunk["decoded"].get("model_names", []))

    sky_names: List[str] = []
    for chunk in by_type.get(17, []):
        sky_names.extend(face["name"] for face in chunk["decoded"].get("faces", []))

    type_counts: Dict[str, int] = {}
    for ctype in sorted(by_type):
        type_counts[str(ctype)] = len(by_type[ctype])

    summary = {
        "chunk_count": len(chunks),
        "chunk_type_counts": type_counts,
        "first_chunk_offset_hex": chunks[0]["chunk_offset_hex"] if chunks else None,
        "last_chunk_end_hex": chunks[-1]["payload_end_hex"] if chunks else None,
        "chain_ends_at_eof": bool(chunks) and chunks[-1]["payload_end"] == len(data),
        "author_tag": header["author_tag"],
        "entity_count": len(entity_docs),
        "property_count": sum(len(e["properties"]) for e in entity_docs),
        "texture_count": len(textures),
        "texture_names": textures,
        "model_count": len(models),
        "model_names": models,
        "sky_face_names": sky_names,
    }

    return {
        "file": str(path),
        "file_size": len(data),
        "file_size_hex": hx(len(data)),
        "header": header,
        "summary": summary,
        "chunks": chunks,
    }


def print_human(result: Dict[str, Any]) -> None:
    header = result["header"]
    summary = result["summary"]
    print(f"File: {result['file']}")
    print(f"Size: {result['file_size']} ({result['file_size_hex']})")
    print(
        "Header: version={version} header_size={header_size} subversion={subversion} "
        "build={build} tag={author_tag!r} counters={counters}".format(**header)
    )
    print(
        f"Chunks: {summary['chunk_count']} "
        f"(types: {summary['chunk_type_counts']}), "
        f"chain ends at EOF: {summary['chain_ends_at_eof']}"
    )
    print(
        f"Entities: {summary['entity_count']}, properties: {summary['property_count']}, "
        f"textures: {summary['texture_count']}, models: {summary['model_count']}"
    )
    print("Sky faces: " + ", ".join(summary["sky_face_names"]))
    print("\nChunk chain:")
    for chunk in result["chunks"]:
        decoded = chunk["decoded"]
        extra = ""
        if decoded.get("kind") == "entity_text":
            extra = f" entities={decoded['entity_count']}"
        elif decoded.get("kind") in ("plane_table", "bounds_table", "texture_dictionary", "model_table"):
            extra = f" count={decoded.get('count')}"
        print(
            f"  #{chunk['index']:2d} type={chunk['type']:2d} "
            f"chunk={chunk['chunk_offset_hex']:>10} "
            f"payload={chunk['payload_offset_hex']:>10} "
            f"size=0x{chunk['payload_size']:x} "
            f"end={chunk['payload_end_hex']:>10} "
            f"kind={decoded.get('kind')}{extra}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Parse Shadow Force: Razor Unit .bsp files")
    parser.add_argument("bsp", type=Path, help="path to a .bsp file")
    parser.add_argument("--json", dest="json_path", metavar="PATH",
                        help="write full JSON to PATH, or '-' for stdout")
    args = parser.parse_args(argv)

    try:
        data = args.bsp.read_bytes()
        result = build_result(args.bsp, data)
    except (OSError, ValueError, struct.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json_path:
        payload = json.dumps(result, indent=2, ensure_ascii=False)
        if args.json_path == "-":
            print(payload)
        else:
            out = Path(args.json_path)
            out.write_text(payload + "\n", encoding="utf-8")
            print(f"wrote {out}")
    else:
        print_human(result)
    return 0


def inspect_bsp(data: bytes, name: str = "<bsp>") -> Dict[str, Any]:
    """Library-friendly entry point: parse raw .bsp bytes into a JSON-ready dict."""
    return build_result(Path(name), bytes(data))


def parse_bsp(data: bytes, name: str = "<bsp>") -> Dict[str, Any]:
    """Alias used by the converter pipeline."""
    return inspect_bsp(data, name)


if __name__ == "__main__":
    raise SystemExit(main())
