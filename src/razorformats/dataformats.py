#!/usr/bin/env python3
"""Proof-of-concept parsers for FUN Labs "Shadow Force: Razor Unit" data files.

Standard library only.  Supported:
  .model  - Lua-like model definition DSL        (full parse)
  .script - Lua-like shader definition DSL       (full parse, nested blocks)
  .fx     - Lua-like particle-system DSL         (same generic parser)
  .fnt    - 'SFNT' bitmap-font binary            (full parse)
  .tga    - TGA                                  (header reader)
  .wav    - RIFF/WAVE                            (header/chunk reader)

Usage:
    python3 parsers.py FILE [...] [--format auto|model|script|fx|fnt|tga|wav]
    python3 parsers.py FILE --fields           # selected summary fields only
    python3 parsers.py --selftest              # parse all files in known sample dirs

Output is one JSON object per file on stdout (or a JSON array for multiple files).
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Shared Lua-like lexer / call parser
# --------------------------------------------------------------------------- #

KEYWORDS = {"if", "then", "else", "elseif", "end", "function", "local", "return",
            "while", "for", "do", "and", "or", "not"}


@dataclass
class Token:
    kind: str            # ident | keyword | number | string | punct | newline | eof
    text: str
    value: Any
    line: int

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"T({self.kind},{self.value!r},L{self.line})"


_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "0": "\0", '"': '"', "'": "'", "\\": "\\"}


def lex(text: str) -> list[Token]:
    """Tokenize the small Lua subset used by .model/.script/.fx files."""
    out: list[Token] = []
    i, line, n = 0, 1, len(text)
    while i < n:
        c = text[i]
        if c == "\n":
            out.append(Token("newline", "\n", "\n", line))
            line += 1
            i += 1
        elif c in " \t\r\f\v":
            i += 1
        elif text.startswith("--", i):                     # Lua line comment
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif c in "(){}[],;":
            out.append(Token("punct", c, c, line))
            i += 1
        elif c == '"' or c == "'":
            quote, i = c, i + 1
            buf: list[str] = []
            while i < n and text[i] != quote:
                if text[i] == "\\" and i + 1 < n:
                    i += 1
                    buf.append(_ESCAPES.get(text[i], text[i]))
                    if text[i] == "\n":
                        line += 1
                else:
                    buf.append(text[i])
                    if text[i] == "\n":
                        line += 1
                i += 1
            i += 1  # closing quote
            out.append(Token("string", "".join(buf), "".join(buf), line))
        elif c.isdigit() or (c == "." and i + 1 < n and text[i + 1].isdigit()):
            m = re.match(r"(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?", text[i:])
            assert m
            raw = m.group(0)
            val: Any = float(raw) if any(ch in raw for ch in ".eE") else int(raw)
            out.append(Token("number", raw, val, line))
            i += len(raw)
        elif c.isalpha() or c == "_":
            m = re.match(r"[A-Za-z_]\w*", text[i:])
            assert m
            raw = m.group(0)
            out.append(Token("keyword" if raw in KEYWORDS else "ident", raw, raw, line))
            i += len(raw)
        elif text.startswith("==", i) or text.startswith("~=", i):
            out.append(Token("punct", text[i:i + 2], text[i:i + 2], line))
            i += 2
        else:
            out.append(Token("punct", c, c, line))           # + - * / = < > etc.
            i += 1
    out.append(Token("eof", "", None, line))
    return out


class DslParser:
    """Generic statement/block parser for the game's Lua-like config scripts."""

    def __init__(self, text: str):
        self.toks = lex(text)
        self.pos = 0

    def peek(self, k: int = 0) -> Token:
        return self.toks[min(self.pos + k, len(self.toks) - 1)]

    def take(self) -> Token:
        t = self.peek()
        self.pos += 1
        return t

    @staticmethod
    def _literal(toks: list[Token]) -> Any:
        """Reduce a single-token (or signed number) expression to a Python value."""
        if len(toks) == 1 and toks[0].kind in ("string", "number"):
            return toks[0].value
        if len(toks) == 2 and toks[0].text in ("-", "+") and toks[1].kind == "number":
            return -toks[1].value if toks[0].text == "-" else toks[1].value
        return None

    @staticmethod
    def _expr(toks: list[Token]) -> str:
        return "".join(t.text if t.kind != "string" else json.dumps(t.value)
                       for t in toks)

    def arguments(self) -> list[Any]:
        """Parse '( arg, arg, ... )' starting at current token."""
        if self.peek().text != "(":
            return []
        self.take()
        args: list[Any] = []
        cur: list[Token] = []
        depth = 1
        while depth and self.peek().kind != "eof":
            t = self.take()
            if t.text == "(":
                depth += 1
                cur.append(t)
            elif t.text == ")":
                depth -= 1
                if depth == 0:
                    if cur:
                        args.append(self._literal(cur) if self._literal(cur) is not None
                                    else {"expr": self._expr(cur)})
                    break
                cur.append(t)
            elif t.text == "," and depth == 1:
                if cur:
                    args.append(self._literal(cur) if self._literal(cur) is not None
                                else {"expr": self._expr(cur)})
                cur = []
            else:
                cur.append(t)
        return args

    def call(self, name: str, line: int) -> dict:
        args = self.arguments()
        return {"kind": "call", "name": name, "args": args, "line": line}

    def _skip_separators(self) -> None:
        while self.peek().kind in ("newline",) or self.peek().text == ";":
            self.take()

    def block(self, stop: set[str] | None = None) -> list[dict]:
        """Parse statements until one of `stop` identifiers or EOF.

        End-block keywords are consumed by the caller, except structural
        Begin*/End* pairs which are consumed internally.
        """
        stop = stop or set()
        nodes: list[dict] = []
        while self.peek().kind != "eof":
            self._skip_separators()
            t = self.peek()
            if t.kind == "eof":
                break
            if t.text in stop:
                break
            if t.text == "end":                 # unmatched 'end' - let caller close
                break
            if t.text == "function":
                self.take()
                name = self.take().text
                if self.peek().text == "(":
                    self.arguments()             # parameter list (normally empty)
                body = self.block({"end"})
                if self.peek().text == "end":
                    self.take()
                nodes.append({"kind": "function", "name": name, "body": body,
                              "line": t.line})
                continue
            if t.text == "if" or t.text == "elseif":
                self.take()
                cond: list[Token] = []
                while self.peek().kind != "eof" and self.peek().text != "then":
                    cond.append(self.take())
                if self.peek().text == "then":
                    self.take()
                then_body = self.block({"else", "elseif", "end"})
                node: dict = {"kind": "if", "condition": self._expr(cond),
                              "then": then_body, "line": t.line}
                if self.peek().text == "elseif":
                    # represented as a chain of if nodes in the else branch
                    node["else"] = self._parse_elseif_chain()
                elif self.peek().text == "else":
                    self.take()
                    node["else"] = self.block({"end"})
                if self.peek().text == "end":
                    self.take()
                nodes.append(node)
                continue
            if t.text == "else" or t.text == "then":
                break
            if t.kind in ("ident", "keyword") and t.text.startswith("End") \
                    and self.peek(1).text == "(":
                name = self.take().text
                self.arguments()
                nodes.append({"kind": "block_end_call", "name": name, "line": t.line})
                continue
            if t.kind in ("ident", "keyword") and t.text.startswith("Begin") \
                    and self.peek(1).text == "(":
                begin = self.take().text
                self.arguments()
                end = "End" + begin[5:]
                body = self.block({end})
                if self.peek().text == end:
                    self.take()
                    if self.peek().text == "(":
                        self.arguments()      # EndStage(); etc.
                nodes.append({"kind": "block", "begin": begin, "end": end,
                              "body": body, "line": t.line})
                continue
            if t.kind in ("ident", "keyword") and self.peek(1).text == "(":
                nodes.append(self.call(self.take().text, t.line))
                continue
            # assignment or unrecognised statement: consume to end of line/semicolon
            head = self.take()
            rest: list[Token] = []
            while self.peek().kind not in ("newline", "eof") and self.peek().text != ";":
                rest.append(self.take())
            if head.kind in ("ident", "keyword") and rest and rest[0].text == "=":
                nodes.append({"kind": "assign", "name": head.text,
                              "value": self._expr(rest[1:]), "line": head.line})
            else:
                nodes.append({"kind": "raw", "text": (head.text + self._expr(rest)).strip(),
                              "line": head.line})
        return nodes

    def _parse_elseif_chain(self) -> list[dict]:
        """Consume 'elseif ... then ...' (and optional final else) up to 'end'."""
        result: list[dict] = []
        while self.peek().text == "elseif":
            kw = self.take()
            cond: list[Token] = []
            while self.peek().kind != "eof" and self.peek().text != "then":
                cond.append(self.take())
            if self.peek().text == "then":
                self.take()
            body = self.block({"elseif", "else", "end"})
            result.append({"kind": "if", "condition": self._expr(cond),
                           "then": body, "line": kw.line})
        if self.peek().text == "else":
            self.take()
            result.append({"kind": "else", "body": self.block({"end"})})
        return result

    def parse(self) -> dict:
        return {"kind": "root", "body": self.block()}


# --------------------------------------------------------------------------- #
# Format-specific wrappers
# --------------------------------------------------------------------------- #

def _walk(node: dict | list):
    """Yield every dict node in a parsed tree (depth-first)."""
    if isinstance(node, list):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)


def parse_model(data: bytes, source: str = "") -> dict:
    text = data.decode("latin-1")
    tree = DslParser(text).parse()
    calls = [n for n in _walk(tree["body"]) if n.get("kind") == "call"]
    summary: dict[str, Any] = {"model": [], "mesh": [], "reference": [],
                               "animation": [], "LoadScript": [], "parent": [],
                               "set": [], "other_calls": {}}
    for c in calls:
        name = c["name"]
        if name in summary and isinstance(summary[name], list):
            summary[name].append(c["args"])
        elif name == "other_calls":
            summary[name][c["name"]] = summary[name].get(c["name"], 0) + 1
        else:
            summary.setdefault(name, {"count": 0})["count"] += 1
    return {"format": "model", "source": source, "bytes": len(data),
            "line_endings": "CRLF" if b"\r\n" in data else "LF",
            "tree": tree, "summary": summary}


def parse_script(data: bytes, source: str = "", kind: str = "script") -> dict:
    text = data.decode("latin-1")
    tree = DslParser(text).parse()
    funcs = [n for n in _walk(tree["body"]) if n.get("kind") == "function"]
    calls = [n for n in _walk(tree["body"]) if n.get("kind") == "call"]
    names: dict[str, int] = {}
    for c in calls:
        names[c["name"]] = names.get(c["name"], 0) + 1
    return {"format": kind, "source": source, "bytes": len(data),
            "line_endings": "CRLF" if b"\r\n" in data else "LF",
            "tree": tree, "functions": [{"name": f["name"], "line": f["line"]}
                                        for f in funcs],
            "call_counts": names}


def parse_fnt(data: bytes, source: str = "") -> dict:
    """Parse the FUN Labs 'SFNT' bitmap-font container."""
    if len(data) < 20 or data[:4] != b"SFNT":
        raise ValueError("not an SFNT font")
    end = data.find(b"\x00", 4)
    if end < 0:
        raise ValueError("unterminated font name")
    name = data[4:end].decode("latin-1")
    p = end + 1
    if len(data) - p < 28:
        raise ValueError("truncated SFNT header")
    (size_byte,) = struct.unpack_from("<B", data, p)
    f1, f2, f3, f4 = struct.unpack_from(">IIII", data, p + 1)
    # The last three header words have ambiguous endianness (see docs): report bytes.
    f5 = data[p + 17:p + 21]
    f6 = data[p + 21:p + 25]
    f7 = data[p + 25:p + 28]
    body = data[p + 28:]
    count = len(body) // 7
    glyphs = []
    for i in range(count):
        o = i * 7
        code = body[o]
        u, v = struct.unpack_from("<HH", body, o + 1)
        glyphs.append({"index": i, "char": chr(code) if 32 <= code < 127 else None,
                       "code": code, "raw_offset": (v << 16) | u,
                       "u_px": u, "v_px": v, "byte5": body[o + 5],
                       "byte6": body[o + 6]})
    def _stride(values: list[int]) -> int | None:
        deltas = {b - a for a, b in zip(values, values[1:])}
        return next(iter(deltas)) if len(deltas) == 1 else None

    return {"format": "fnt", "source": source, "bytes": len(data),
            "signature": data[:4].decode("ascii"), "name": name,
            "name_offset": 4, "name_length": end - 4, "header_offset": p,
            "header_size": 28,
            "header": {"font_size_u8": size_byte, "field1_be32": f1,
                       "field2_be32": f2, "field3_be32": f3, "field4_be32": f4,
                       "field5_raw": f5.hex(), "field6_raw": f6.hex(),
                       "field7_raw": f7.hex()},
            "glyph_record_size": 7, "glyph_count": count,
            "cell_width_px": _stride(sorted({g["u_px"] for g in glyphs})),
            "cell_height_px": _stride(sorted({g["v_px"] for g in glyphs})),
            "trailing_bytes": len(body) % 7, "glyphs": glyphs}


def parse_tga(data: bytes, source: str = "") -> dict:
    """Read a TGA header (no RLE/colormap decoding - header proof reader)."""
    if len(data) < 18:
        raise ValueError("truncated TGA header")
    (idlen, cmap_type, img_type, cmap_first, cmap_len, cmap_depth,
     xorg, yorg, width, height, bpp, desc) = struct.unpack_from("<BBBHHBHHHHBB", data, 0)
    pixel_bytes = width * height * ((bpp + 7) // 8)
    expected = 18 + idlen + (cmap_len * ((cmap_depth + 7) // 8) if cmap_type else 0) \
        + pixel_bytes
    return {"format": "tga", "source": source, "bytes": len(data),
            "id_length": idlen, "color_map_type": cmap_type, "image_type": img_type,
            "color_map": {"first_entry": cmap_first, "length": cmap_len,
                          "entry_bits": cmap_depth},
            "origin": {"x": xorg, "y": yorg},
            "width": width, "height": height, "bits_per_pixel": bpp,
            "descriptor": desc, "alpha_bits": desc & 0x0F,
            "origin_corner": {0: "bottom-left", 2: "top-left"}.get(desc >> 4, "reserved"),
            "expected_file_size": expected,
            "size_matches": len(data) == expected,
            "has_tga_footer": len(data) >= 18 and data[-18:-6] == b"TRUEVISION-XFILE.\x00"}


def parse_wav(data: bytes, source: str = "") -> dict:
    """Read RIFF/WAVE chunks and PCM fmt fields."""
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not a RIFF/WAVE file")
    pos, chunks = 12, []
    fmt = None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = int.from_bytes(data[pos + 4:pos + 8], "little")
        body = data[pos + 8:pos + 8 + size]
        item = {"id": cid.decode("latin-1"), "size": size,
                "offset": pos + 8, "complete": len(body) == size}
        if cid == b"fmt " and len(body) >= 16:
            (audio_fmt, channels, rate, byte_rate, block_align, bits) = \
                struct.unpack_from("<HHIIHH", body, 0)
            fmt = {"audio_format": audio_fmt, "channels": channels,
                   "sample_rate": rate, "byte_rate": byte_rate,
                   "block_align": block_align, "bits_per_sample": bits,
                   "extra_bytes": size - 16,
                   "extra_hex": body[16:].hex() if size > 16 else ""}
            item["fmt"] = fmt
        chunks.append(item)
        pos += 8 + size + (size & 1)      # word alignment
    riff_size = int.from_bytes(data[4:8], "little")
    end_of_chunks = max((c["offset"] + c["size"] for c in chunks), default=12)
    return {"format": "wav", "source": source, "bytes": len(data),
            "riff_size": riff_size, "riff_size_matches_data": riff_size == end_of_chunks - 8,
            "chunks": chunks, "fmt": fmt}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def detect(path: Path, data: bytes) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext in ("model", "script", "fx", "fnt", "tga", "wav"):
        return ext
    if data.startswith(b"SFNT"):
        return "fnt"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if len(data) >= 18 and data[2] in (2, 10) and \
            int.from_bytes(data[12:14], "little") in range(1, 8193):
        return "tga"
    return "script"


def parse_file(path: Path, fmt: str | None = None) -> dict:
    data = path.read_bytes()
    fmt = fmt or detect(path, data)
    if fmt == "model":
        return parse_model(data, str(path))
    if fmt == "script":
        return parse_script(data, str(path))
    if fmt == "fx":
        return parse_script(data, str(path), kind="fx")
    if fmt == "fnt":
        return parse_fnt(data, str(path))
    if fmt == "tga":
        return parse_tga(data, str(path))
    if fmt == "wav":
        return parse_wav(data, str(path))
    raise ValueError(f"unsupported format: {fmt}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--format", choices=["auto", "model", "script", "fx", "fnt",
                                         "tga", "wav"], default="auto")
    ap.add_argument("--fields", action="store_true",
                    help="emit compact summary instead of the full tree")
    ap.add_argument("--selftest", action="store_true",
                    help="parse representative files from known sample directories")
    args = ap.parse_args()

    paths = list(args.files)
    if args.selftest:
        roots = [Path("/tmp/opencode/razor_extract"),
                 Path("/tmp/opencode/research/misc/corpus"),
                 Path("/tmp/opencode/research/misc/samples")]
        for ext in ("model", "script", "fnt", "tga", "wav", "fx"):
            for root in roots:
                found = [p for p in root.rglob("*." + ext)
                         if p.is_file() and "research/misc/corpus/objects/objects/Weapons" not in str(p)]
                paths.extend(found[:2])
    if not paths:
        ap.error("provide at least one file (or --selftest)")

    results = []
    for p in paths:
        try:
            obj = parse_file(p, None if args.format == "auto" else args.format)
            if args.fields:
                obj.pop("tree", None)
                if "glyphs" in obj:
                    obj["glyphs"] = obj["glyphs"][:4]
                if "chunks" in obj:
                    obj["chunks"] = obj["chunks"][:4]
            results.append(obj)
        except Exception as e:                              # keep CLI useful
            results.append({"source": str(p), "error": f"{type(e).__name__}: {e}"})
    print(json.dumps(results[0] if len(results) == 1 else results, indent=2,
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
