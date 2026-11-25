#!/usr/bin/env python3
"""Read, extract and create FUN Labs ``.fun`` archives.

A ``.fun`` file is a flat sequence of obfuscated ZIP local-file headers.  The
four-byte ZIP signature is XORed with a fixed rolling key (0x89, +1); the two
bytes following it are a *plaintext* (key, increment) seed for the entry.  The
24-byte header (standard ZIP local header without the signature and the
"version needed" field), the filename and any extra field are each encrypted
with a rolling additive-XOR stream that is restarted from that seed for every
field.  Payload bytes are not obfuscated.

For method 8 entries the two "extra" bytes are normally the plaintext zlib
header ``78 9c`` and ``comp_size`` bytes follow: a raw DEFLATE body plus its
4-byte big-endian Adler-32.  Together, ``extra + payload`` is an ordinary zlib
stream.  The ZIP CRC field is present but always zero in shipped archives.

The module uses only the Python standard library.  Archives are memory-mapped
for inspection; extraction and creation stream data in bounded chunks.

    python3 funarchive.py list game.fun
    python3 funarchive.py extract game.fun -o out --select 'script\\Game\\ai.lua'
    python3 funarchive.py pack new.fun a.txt dir/b.bin --level 6

See ``docs/fun_archive.md`` for the full format specification.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mmap
import os
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "FunArchiveError",
    "FunFormatError",
    "UnsafeNameError",
    "rolling_xor",
    "sanitize_name",
    "FunEntry",
    "FunArchive",
    "WriteEntry",
    "write",
    "create",
    "main",
]

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

ZIP_SIGNATURE = b"PK\x03\x04"
SIG_KEY = 0x89
SIG_INCREMENT = 0x01
HEADER_FMT = "<4H3I2H"
HEADER_SIZE = struct.calcsize(HEADER_FMT)          # 24
LOCAL_PREFIX_SIZE = 4 + 2 + HEADER_SIZE            # 30 bytes before the name
METHOD_STORED = 0
METHOD_DEFLATE = 8
DEFAULT_CHUNK_SIZE = 1 << 20                       # 1 MiB
DEFAULT_ZLIB_LEVEL = 6
MAX_U16 = 0xFFFF
MAX_U32 = 0xFFFFFFFF

# Windows-invalid characters replaced only in sanitizing mode.
_INVALID_CHARS = '<>:"|?*'


class FunArchiveError(Exception):
    """Base class for errors raised by this module."""


class FunFormatError(FunArchiveError):
    """The archive structure or a payload is malformed."""


class UnsafeNameError(FunArchiveError):
    """An entry name would escape the destination directory."""


# --------------------------------------------------------------------------- #
# Crypto primitive and name handling
# --------------------------------------------------------------------------- #

def rolling_xor(data: bytes | bytearray | memoryview, key: int,
                inc: int) -> tuple[bytes, int]:
    """Symmetric rolling additive-XOR transform.

    ``out[i] = data[i] ^ k`` with ``k`` starting at ``key & 0xFF`` and updated
    as ``k = (k + inc) & 0xFF`` *after* each byte.  Returns the transformed
    bytes and the key value that would follow the last byte.
    """
    out = bytearray(len(data))
    k = key & 0xFF
    step = inc & 0xFF
    for i, byte in enumerate(data):
        out[i] = byte ^ k
        k = (k + step) & 0xFF
    return bytes(out), k


def is_zlib_header(data: bytes) -> bool:
    """True if *data* is a two-byte zlib header (CM=8, valid FCHECK)."""
    return (len(data) == 2
            and (data[0] & 0x0F) == 8
            and (data[0] >> 4) <= 7
            and (int.from_bytes(data, "big") % 31) == 0)


def sanitize_name(name: str, *, sanitize: bool = True) -> Path:
    """Turn an archive entry name into a safe relative ``pathlib.Path``.

    Backslashes are treated as separators.  With ``sanitize=True`` (the
    default) absolute paths, drive letters, ``..`` components, empty
    components and Windows-invalid characters are discarded/replaced.  With
    ``sanitize=False`` the name is preserved but traversal or absolute paths
    raise :class:`UnsafeNameError`, so extraction always stays inside its
    destination directory.
    """
    if not isinstance(name, str):
        raise TypeError("entry name must be str")
    if "\x00" in name:
        raise UnsafeNameError("entry name contains a NUL byte")

    normalized = name.replace("\\", "/")
    absolute = normalized.startswith("/") or (
        len(normalized) >= 2 and normalized[1] == ":"
    )
    if absolute and not sanitize:
        raise UnsafeNameError(f"absolute entry name: {name!r}")
    if absolute:
        normalized = normalized.lstrip("/")
        if len(normalized) >= 2 and normalized[1] == ":":
            normalized = normalized[2:].lstrip("/")

    parts: list[str] = []
    for part in normalized.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not sanitize:
                raise UnsafeNameError(f"parent-directory component in {name!r}")
            continue
        if sanitize:
            part = "".join(c if c not in _INVALID_CHARS else "_" for c in part)
            part = part.rstrip(" .")
        if part:
            parts.append(part)

    if not parts:
        if not sanitize:
            raise UnsafeNameError(f"empty entry name: {name!r}")
        parts = ["_unnamed_"]
    return Path(*parts)


# --------------------------------------------------------------------------- #
# Manifest entries
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class FunEntry:
    """One parsed archive entry (metadata only, no payload bytes)."""

    index: int
    name: str
    raw_name: bytes
    header_offset: int
    seed: tuple[int, int]          # (key, increment), both 0..255
    flag: int
    method: int
    mod_time: int
    mod_date: int
    crc32: int
    comp_size: int
    uncomp_size: int
    name_len: int
    extra_len: int
    extra: bytes                   # raw bytes; plaintext in shipped archives
    data_offset: int

    @property
    def is_dir(self) -> bool:
        return self.name.endswith(("/", "\\"))

    @property
    def method_name(self) -> str:
        return {METHOD_STORED: "stored", METHOD_DEFLATE: "deflate"}.get(
            self.method, f"unknown({self.method})"
        )

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable representation for a manifest."""
        return {
            "index": self.index,
            "name": self.name,
            "method": self.method,
            "method_name": self.method_name,
            "flag": self.flag,
            "mod_time": self.mod_time,
            "mod_date": self.mod_date,
            "crc32": self.crc32,
            "comp_size": self.comp_size,
            "uncomp_size": self.uncomp_size,
            "header_offset": self.header_offset,
            "data_offset": self.data_offset,
            "name_len": self.name_len,
            "extra_len": self.extra_len,
            "extra_hex": self.extra.hex(),
            "seed": {"key": self.seed[0], "increment": self.seed[1]},
            "is_dir": self.is_dir,
        }


# --------------------------------------------------------------------------- #
# Reader
# --------------------------------------------------------------------------- #

class FunArchive:
    """Memory-mapped reader/extractor for a FUN Labs ``.fun`` archive.

    ``read()`` returns a cached list of :class:`FunEntry` objects (the
    manifest); ``iter(archive)`` scans lazily.  ``extract()`` writes selected
    entries to a destination directory.
    """

    def __init__(self, path: str | os.PathLike[str], *, strict: bool = False,
                 encoding: str = "latin-1") -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"archive not found: {self.path}")
        self.strict = strict
        self.encoding = encoding
        self._size = self.path.stat().st_size
        self._fh: BinaryIO | None = None
        self._map: mmap.mmap | None = None
        self._manifest: list[FunEntry] | None = None
        self._end_offset = 0

    # -- lifecycle -------------------------------------------------------- #

    def __enter__(self) -> FunArchive:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        """Release the memory map and file handle (idempotent)."""
        if self._map is not None:
            self._map.close()
            self._map = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    @property
    def size(self) -> int:
        return self._size

    @property
    def trailing_bytes(self) -> int:
        """Byte count after the last successfully parsed entry."""
        return max(0, self._size - self._end_offset)

    def _buffer(self) -> mmap.mmap:
        if self._map is None:
            self._fh = self.path.open("rb")
            try:
                self._map = mmap.mmap(self._fh.fileno(), 0,
                                      access=mmap.ACCESS_READ)
            except BaseException:
                self._fh.close()
                self._fh = None
                raise
        return self._map

    # -- parsing ---------------------------------------------------------- #

    def _scan(self) -> Iterator[FunEntry]:
        buf = self._buffer()
        size = self._size
        off = 0
        index = 0
        while off + LOCAL_PREFIX_SIZE <= size:
            sig, _ = rolling_xor(buf[off:off + 4], SIG_KEY, SIG_INCREMENT)
            if sig != ZIP_SIGNATURE:
                if self.strict:
                    raise FunFormatError(
                        f"bad signature at offset 0x{off:X}: {sig.hex()}"
                    )
                break

            key, inc = buf[off + 4], buf[off + 5]
            header, _ = rolling_xor(
                buf[off + 6:off + 6 + HEADER_SIZE], key, inc
            )
            (flag, method, mod_time, mod_date, crc32, comp_size, uncomp_size,
             name_len, extra_len) = struct.unpack(HEADER_FMT, header)

            name_start = off + LOCAL_PREFIX_SIZE
            data_start = name_start + name_len + extra_len
            end = data_start + comp_size
            if end > size:
                if self.strict:
                    raise FunFormatError(
                        f"entry #{index + 1} at 0x{off:X} claims {comp_size} "
                        f"payload bytes; only {size - data_start} remain"
                    )
                break

            # Each obfuscated field starts a *fresh* keystream from the seed.
            name_bytes, _ = rolling_xor(buf[name_start:name_start + name_len],
                                        key, inc)
            name = name_bytes.decode(self.encoding)
            # The extra field is stored PLAINTEXT in shipped archives (normally
            # the two-byte zlib header 78 9c); do not decrypt it here.
            extra = bytes(buf[name_start + name_len:data_start])

            index += 1
            yield FunEntry(
                index=index,
                name=name,
                raw_name=name_bytes,
                header_offset=off,
                seed=(key, inc),
                flag=flag,
                method=method,
                mod_time=mod_time,
                mod_date=mod_date,
                crc32=crc32,
                comp_size=comp_size,
                uncomp_size=uncomp_size,
                name_len=name_len,
                extra_len=extra_len,
                extra=extra,
                data_offset=data_start,
            )
            off = end
        self._end_offset = off

    def __iter__(self) -> Iterator[FunEntry]:
        return self._scan()

    def read(self, *, refresh: bool = False) -> list[FunEntry]:
        """Return the entry manifest, parsing the archive on first use.

        ``refresh=True`` re-stat / re-parses the file (useful if it changed).
        """
        if refresh:
            self._size = self.path.stat().st_size
            self._manifest = None
        if self._manifest is None:
            self._manifest = list(self._scan())
        return list(self._manifest)

    @property
    def entries(self) -> list[FunEntry]:
        return self.read()

    def manifest(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.read()]

    # -- payload access --------------------------------------------------- #

    def _check_entry(self, entry: FunEntry) -> None:
        if not 0 <= entry.data_offset <= self._size:
            raise FunFormatError(f"invalid data offset for {entry.name!r}")
        if entry.data_offset + entry.comp_size > self._size:
            raise FunFormatError(f"payload of {entry.name!r} is truncated")

    def payload_view(self, entry: FunEntry) -> memoryview:
        """Read-only memoryview of the (obfuscation-free) payload bytes."""
        self._check_entry(entry)
        return memoryview(self._buffer())[entry.data_offset:
                                          entry.data_offset + entry.comp_size]

    def iter_payload_chunks(self, entry: FunEntry,
                            chunk_size: int = DEFAULT_CHUNK_SIZE
                            ) -> Iterator[memoryview]:
        """Yield the payload in bounded chunks without copying the whole entry.

        ``mmap`` slices produce immutable ``bytes``, avoiding lingering exported
        buffers that would keep the map alive after :meth:`close`.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._check_entry(entry)
        buf = self._buffer()
        start = entry.data_offset
        for pos in range(0, entry.comp_size, chunk_size):
            yield buf[start + pos:start + min(pos + chunk_size, entry.comp_size)]

    def read_payload(self, entry: FunEntry) -> bytes:
        """Convenience method returning the full payload (use sparingly)."""
        view = self.payload_view(entry)
        try:
            return bytes(view)
        finally:
            view.release()

    def read_file(self, entry: FunEntry, *, verify: bool = True) -> bytes:
        """Decompress one entry fully into memory (small entries only)."""
        pieces: list[bytes] = []
        self._decode_entry(entry, pieces.append, inflate=True, verify=verify)
        return b"".join(pieces)

    # -- extraction ------------------------------------------------------- #

    @staticmethod
    def _select_entries(entries: Iterable[FunEntry],
                        selected: Any) -> Iterator[FunEntry]:
        if selected is None:
            yield from entries
            return
        if callable(selected):
            for entry in entries:
                if selected(entry):
                    yield entry
            return
        if isinstance(selected, FunEntry):
            wanted = {selected.name}
        elif isinstance(selected, str):
            wanted = {selected}
        else:
            wanted = set(selected)
        originals = tuple(wanted)
        wanted |= {s.replace("/", "\\") for s in originals if isinstance(s, str)}
        wanted |= {s.replace("\\", "/") for s in originals if isinstance(s, str)}
        for entry in entries:
            if entry.name in wanted or entry.index in wanted:
                yield entry

    def _decode_entry(self, entry: FunEntry, write: Callable[[bytes], Any],
                      *, inflate: bool, verify: bool) -> None:
        """Stream one decoded payload into ``write``."""
        method = entry.method
        if method == METHOD_DEFLATE and inflate:
            # zlib-wrapped mode validates the Adler-32; with verify=False we
            # use raw mode so a damaged trailer cannot abort the extraction.
            zlib_mode = verify and is_zlib_header(entry.extra)
            decompressor = zlib.decompressobj(15 if zlib_mode else -15)
            adler = zlib.adler32(b"")
            produced = 0
            try:
                if zlib_mode:
                    out = decompressor.decompress(entry.extra)
                    if out:
                        write(out)
                        produced += len(out)
                for chunk in self.iter_payload_chunks(entry):
                    out = decompressor.decompress(chunk)
                    if out:
                        write(out)
                        produced += len(out)
                        adler = zlib.adler32(out, adler)
                tail = decompressor.flush()
                if tail:
                    write(tail)
                    produced += len(tail)
                    adler = zlib.adler32(tail, adler)
            except zlib.error as exc:
                raise FunFormatError(
                    f"DEFLATE failure for {entry.name!r}: {exc}"
                ) from exc
            if verify:
                if not decompressor.eof:
                    raise FunFormatError(
                        f"incomplete DEFLATE stream for {entry.name!r}"
                    )
                leftover = decompressor.unused_data
                if zlib_mode:
                    if leftover:
                        raise FunFormatError(
                            f"unexpected trailing bytes after zlib stream for "
                            f"{entry.name!r}"
                        )
                elif len(leftover) < 4:
                    raise FunFormatError(
                        f"missing Adler-32 trailer for {entry.name!r}"
                    )
                else:
                    stored_adler = struct.unpack(">I", leftover[:4])[0]
                    if stored_adler != adler:
                        raise FunFormatError(
                            f"Adler-32 mismatch for {entry.name!r}: "
                            f"header {stored_adler:#010x}, got {adler:#010x}"
                        )
                if entry.uncomp_size and produced != entry.uncomp_size:
                    raise FunFormatError(
                        f"size mismatch for {entry.name!r}: inflated "
                        f"{produced}, header says {entry.uncomp_size}"
                    )
            return

        if method in (METHOD_STORED, METHOD_DEFLATE):
            produced = 0
            for chunk in self.iter_payload_chunks(entry):
                write(chunk)
                produced += len(chunk)
            if verify and method == METHOD_STORED and entry.uncomp_size:
                if produced != entry.uncomp_size:
                    raise FunFormatError(
                        f"size mismatch for {entry.name!r}: stored "
                        f"{produced}, header says {entry.uncomp_size}"
                    )
            return

        if not inflate:
            for chunk in self.iter_payload_chunks(entry):
                write(chunk)
            return
        raise FunFormatError(
            f"unsupported compression method {method} for {entry.name!r}"
        )

    def extract(self, dest: str | os.PathLike[str], *,
                selected: Any = None,
                inflate: bool = True,
                sanitize: bool = True,
                overwrite: bool = True,
                verify: bool = True,
                chunk_size: int = DEFAULT_CHUNK_SIZE) -> list[Path]:
        """Extract entries to *dest* and return the paths written.

        ``selected`` may be ``None`` (all entries), a name, an iterable of
        names/indices, a :class:`FunEntry`, or a predicate callable.  With
        ``inflate=False`` raw payload bytes are written (the two-byte zlib
        header in the "extra" field is omitted, as in the reference tool).
        ``sanitize=False`` preserves names but still forbids traversal.
        """
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        root = Path(dest).resolve()
        root.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        entries = self.read()

        for entry in self._select_entries(entries, selected):
            rel = sanitize_name(entry.name, sanitize=sanitize)
            out_path = root / rel
            resolved = out_path.resolve()
            if not resolved.is_relative_to(root):
                raise UnsafeNameError(
                    f"entry {entry.name!r} escapes destination directory"
                )
            if entry.is_dir:
                resolved.mkdir(parents=True, exist_ok=True)
                written.append(resolved)
                continue
            if resolved.exists() and not overwrite:
                raise FileExistsError(f"refusing to overwrite {resolved}")
            resolved.parent.mkdir(parents=True, exist_ok=True)

            temp_path = resolved.with_name(
                f".{resolved.name}.funpart-{os.urandom(6).hex()}"
            )
            try:
                with temp_path.open("wb") as fh:
                    def write(chunk: bytes | memoryview) -> None:
                        fh.write(chunk)
                    self._decode_entry(entry, write, inflate=inflate,
                                       verify=verify)
                os.replace(temp_path, resolved)
            except BaseException:
                temp_path.unlink(missing_ok=True)
                raise
            written.append(resolved)
        return written


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #

@dataclass
class WriteEntry:
    """Specification for one entry to write.

    Supply either ``source`` (bytes, path or binary file object; compressed
    while writing) or ``payload`` (already-encoded bytes written verbatim).
    """

    name: str
    source: bytes | bytearray | memoryview | str | os.PathLike[str] | BinaryIO | None = None
    payload: bytes | None = None
    method: int = METHOD_DEFLATE
    seed: tuple[int, int] | None = None
    flag: int = 0
    mod_time: int = 0
    mod_date: int = 0
    crc32: int = 0
    extra: bytes | None = None
    uncomp_size: int | None = None


def _zlib_header(level: int) -> bytes:
    """Two-byte zlib header produced by zlib for the given level."""
    compressor = zlib.compressobj(level, zlib.DEFLATED, 15)
    return (compressor.compress(b"") + compressor.flush())[:2]


def _iter_source_chunks(source: Any, chunk_size: int) -> Iterator[bytes]:
    if isinstance(source, (bytes, bytearray, memoryview)):
        blob = bytes(source)
        if blob:
            yield blob
        return
    if isinstance(source, (str, os.PathLike)):
        with Path(source).open("rb") as fh:
            while chunk := fh.read(chunk_size):
                yield chunk
        return
    if hasattr(source, "read"):
        seekable = bool(getattr(source, "seekable", lambda: False)())
        restore = source.tell() if seekable else None
        while chunk := source.read(chunk_size):
            yield chunk
        if restore is not None:
            source.seek(restore)
        return
    raise TypeError(f"unsupported source type: {type(source).__name__}")


def _count_inflated(payload: bytes, extra: bytes) -> int:
    """Count decompressed bytes without retaining them."""
    decompressor = zlib.decompressobj(15 if is_zlib_header(extra) else -15)
    total = 0
    if is_zlib_header(extra):
        total += len(decompressor.decompress(extra))
    for pos in range(0, len(payload), DEFAULT_CHUNK_SIZE):
        total += len(decompressor.decompress(
            payload[pos:pos + DEFAULT_CHUNK_SIZE]
        ))
    total += len(decompressor.flush())
    return total


def _coerce_entry(item: Any) -> WriteEntry:
    if isinstance(item, WriteEntry):
        return item
    if isinstance(item, Mapping):
        return WriteEntry(**item)
    if isinstance(item, (tuple, list)) and len(item) == 2:
        return WriteEntry(name=item[0], source=item[1])
    raise TypeError(f"cannot interpret {item!r} as an archive entry")


def _resolve_seed(seed: Any, index: int, name: str) -> tuple[int, int]:
    if seed is None:
        digest = hashlib.blake2s(f"{index}:{name}".encode("latin-1"),
                                 digest_size=2).digest()
        key, inc = digest[0], digest[1] or 1
    elif callable(seed):
        key, inc = seed(index, name)
    elif isinstance(seed, (tuple, list)) and len(seed) == 2:
        key, inc = seed
    else:
        raise TypeError("seed must be None, a (key, increment) pair, or a callable")
    if not (0 <= key <= 255 and 0 <= inc <= 255):
        raise ValueError("seed key and increment must be in 0..255")
    return key, inc


def _validate_name(name: str) -> bytes:
    sanitize_name(name, sanitize=False)  # rejects traversal/absolute names
    try:
        encoded = name.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise ValueError(f"entry name is not latin-1 encodable: {name!r}") from exc
    if not encoded or len(encoded) > MAX_U16:
        raise ValueError(f"invalid entry name length for {name!r}")
    return encoded


def write(archive: str | os.PathLike[str], entries: Iterable[Any], *,
          level: int = DEFAULT_ZLIB_LEVEL,
          seed: tuple[int, int] | Callable[[int, str], tuple[int, int]] | None = None,
          overwrite: bool = False,
          chunk_size: int = DEFAULT_CHUNK_SIZE) -> Path:
    """Build a ``.fun`` archive from an iterable of :class:`WriteEntry` specs.

    The file is written to a temporary sibling and atomically replaced on
    success.  Header sizes are patched after streaming each payload, so only
    one chunk of a plain file is held in memory at a time.
    """
    if not 0 <= level <= 9:
        raise ValueError("level must be 0..9")
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    dest = Path(archive)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"refusing to overwrite {dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(entries, Mapping):
        entries = [_coerce_entry((name, source))
                   for name, source in entries.items()]
    else:
        entries = [_coerce_entry(item) for item in entries]

    temp_path = dest.with_name(f".{dest.name}.funpart-{os.urandom(6).hex()}")
    count = 0
    try:
        with temp_path.open("w+b") as out:
            for index, spec in enumerate(entries, start=1):
                name_bytes = _validate_name(spec.name)
                method = spec.method
                if method not in (METHOD_STORED, METHOD_DEFLATE):
                    raise ValueError(f"unsupported method {method}")
                if (spec.source is None) == (spec.payload is None):
                    raise ValueError(
                        f"entry {spec.name!r} needs exactly one of source/payload"
                    )
                key, inc = _resolve_seed(
                    spec.seed if spec.seed is not None else seed,
                    index, spec.name)
                if spec.extra is not None:
                    extra = bytes(spec.extra)
                else:
                    extra = _zlib_header(level) if method == METHOD_DEFLATE else b""
                if len(extra) > MAX_U16:
                    raise ValueError(f"extra field too long for {spec.name!r}")

                out.write(rolling_xor(ZIP_SIGNATURE, SIG_KEY, SIG_INCREMENT)[0])
                out.write(bytes((key, inc)))
                header_pos = out.tell()
                out.write(b"\x00" * HEADER_SIZE)          # patched below
                out.write(rolling_xor(name_bytes, key, inc)[0])
                out.write(extra)                          # plaintext, as in .fun
                data_pos = out.tell()

                if spec.payload is not None:
                    payload = bytes(spec.payload)
                    out.write(payload)
                    if spec.uncomp_size is None:
                        if method == METHOD_DEFLATE:
                            plain_size = _count_inflated(payload, extra)
                        else:
                            plain_size = len(payload)
                    else:
                        plain_size = spec.uncomp_size
                else:
                    plain_size = 0
                    if method == METHOD_DEFLATE:
                        compressor = zlib.compressobj(level, zlib.DEFLATED, -15)
                        adler = zlib.adler32(b"")
                        for chunk in _iter_source_chunks(spec.source, chunk_size):
                            plain_size += len(chunk)
                            adler = zlib.adler32(chunk, adler)
                            piece = compressor.compress(chunk)
                            if piece:
                                out.write(piece)
                        piece = compressor.flush()
                        if piece:
                            out.write(piece)
                        out.write(struct.pack(">I", adler))
                    else:
                        for chunk in _iter_source_chunks(spec.source, chunk_size):
                            plain_size += len(chunk)
                            out.write(chunk)

                comp_size = out.tell() - data_pos
                if comp_size > MAX_U32 or plain_size > MAX_U32:
                    raise ValueError(f"entry {spec.name!r} exceeds 32-bit size limit")
                header = struct.pack(
                    HEADER_FMT, spec.flag, method, spec.mod_time, spec.mod_date,
                    spec.crc32, comp_size, plain_size, len(name_bytes), len(extra),
                )
                out.seek(header_pos)
                out.write(rolling_xor(header, key, inc)[0])
                out.seek(0, os.SEEK_END)
                count += 1
        if not count:
            temp_path.unlink(missing_ok=True)
            raise ValueError("no entries to write")
        os.replace(temp_path, dest)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    return dest


def create(archive: str | os.PathLike[str],
           files: Mapping[str, Any] | Iterable[Any] | None = None, *,
           src_dir: str | os.PathLike[str] | None = None,
           prefix: str = "",
           level: int = DEFAULT_ZLIB_LEVEL,
           seed: tuple[int, int] | Callable[[int, str], tuple[int, int]] | None = None,
           method: int = METHOD_DEFLATE,
           overwrite: bool = False,
           chunk_size: int = DEFAULT_CHUNK_SIZE) -> Path:
    """Create a ``.fun`` archive from plain files.

    ``files`` is a mapping of ``name -> bytes/path/file object`` or an iterable
    of ``(name, source)`` pairs / :class:`WriteEntry` objects.  Alternatively
    pass ``src_dir`` to include every regular file below that directory, with
    backslash-separated relative names and an optional ``prefix``.
    """
    if files is not None and src_dir is not None:
        raise ValueError("give either files or src_dir, not both")
    if files is None and src_dir is None:
        raise ValueError("no input files given")

    if src_dir is not None:
        root = Path(src_dir)
        if not root.is_dir():
            raise NotADirectoryError(f"not a directory: {root}")
        clean_prefix = prefix.replace("/", "\\").strip("\\")
        specs: list[WriteEntry] = []
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            relative = path.relative_to(root).as_posix().replace("/", "\\")
            name = "\\".join(part for part in (clean_prefix, relative) if part)
            specs.append(WriteEntry(name=name, source=path, method=method))
        files = specs
    else:
        # Apply the requested default method to plain (name, source) inputs.
        def as_spec(name: str, value: Any) -> WriteEntry:
            if isinstance(value, WriteEntry):
                return value
            if isinstance(value, Mapping):
                return WriteEntry(**dict(value, name=name))
            return WriteEntry(name=name, source=value, method=method)

        if isinstance(files, Mapping):
            files = [as_spec(name, value) for name, value in files.items()]
        else:
            normalized: list[WriteEntry] = []
            for item in files:
                if isinstance(item, WriteEntry) or isinstance(item, Mapping):
                    normalized.append(_coerce_entry(item))
                else:
                    name, source = item
                    normalized.append(as_spec(name, source))
            files = normalized

    return write(archive, files, level=level, seed=seed, overwrite=overwrite,
                 chunk_size=chunk_size)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _parse_seed(value: str) -> tuple[int, int]:
    parts = value.replace(",", ":").split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("seed must be KEY,INC (e.g. 89,1b)")
    try:
        key, inc = (int(part, 0) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("seed bytes must be integers") from exc
    if not (0 <= key <= 255 and 0 <= inc <= 255):
        raise argparse.ArgumentTypeError("seed bytes must be in 0..255")
    return key, inc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="funarchive.py",
        description="Read, extract and create FUN Labs .fun archives.")
    commands = parser.add_subparsers(dest="command", required=True)

    list_cmd = commands.add_parser("list", help="print the entry manifest")
    list_cmd.add_argument("archive", type=Path)
    list_cmd.add_argument("--json", action="store_true",
                          help="emit a JSON manifest")
    list_cmd.add_argument("--strict", action="store_true",
                          help="fail on structural anomalies")

    extract_cmd = commands.add_parser("extract", help="extract entries")
    extract_cmd.add_argument("archive", type=Path)
    extract_cmd.add_argument("-o", "--dest", type=Path, default=None,
                             help="destination directory (default: <stem>_out)")
    extract_cmd.add_argument("--select", action="append", default=[],
                             help="extract only this name/index (repeatable)")
    extract_cmd.add_argument("--no-inflate", action="store_true",
                             help="write raw payload bytes")
    extract_cmd.add_argument("--no-sanitize", action="store_true",
                             help="keep name characters (still blocks traversal)")
    extract_cmd.add_argument("--no-verify", action="store_true",
                             help="skip size/Adler-32 validation")
    extract_cmd.add_argument("--strict", action="store_true",
                             help="fail on structural anomalies")

    pack_cmd = commands.add_parser("pack", help="build an archive from files")
    pack_cmd.add_argument("archive", type=Path)
    pack_cmd.add_argument("files", nargs="*", type=Path,
                          help="plain files to include")
    pack_cmd.add_argument("--from", dest="src_dir", type=Path, default=None,
                          help="include all files under this directory")
    pack_cmd.add_argument("--prefix", default="",
                          help="name prefix when using --from")
    pack_cmd.add_argument("--seed", type=_parse_seed, default=None,
                          help="shared (key, increment) seed, e.g. 0x89,0x1b")
    pack_cmd.add_argument("--level", type=int, default=DEFAULT_ZLIB_LEVEL,
                          help="zlib compression level 0..9 (default: 6)")
    pack_cmd.add_argument("--method", type=int, default=METHOD_DEFLATE,
                          choices=(METHOD_STORED, METHOD_DEFLATE),
                          help="0 = stored, 8 = DEFLATE (default: 8)")
    pack_cmd.add_argument("--overwrite", action="store_true",
                          help="replace the output archive if it exists")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "list":
            with FunArchive(args.archive, strict=args.strict) as archive:
                entries = archive.read()
                if args.json:
                    print(json.dumps({
                        "archive": str(args.archive),
                        "size": archive.size,
                        "trailing_bytes": archive.trailing_bytes,
                        "entries": archive.manifest(),
                    }, indent=2))
                else:
                    print(f"{args.archive}  ({archive.size} bytes, "
                          f"{len(entries)} entries, "
                          f"{archive.trailing_bytes} trailing bytes)")
                    for entry in entries:
                        print(f"[{entry.index:5d}] {entry.name}  "
                              f"method={entry.method_name} "
                              f"comp={entry.comp_size} uncomp={entry.uncomp_size} "
                              f"seed={entry.seed[0]:02x},{entry.seed[1]:02x} "
                              f"crc={entry.crc32:#010x} "
                              f"@0x{entry.header_offset:X}")
            return 0

        if args.command == "extract":
            dest = args.dest or Path(f"{args.archive.stem}_out")
            selected: list[Any] = []
            for value in args.select:
                try:
                    selected.append(int(value, 0))
                except ValueError:
                    selected.append(value)
            with FunArchive(args.archive, strict=args.strict) as archive:
                if not args.select:
                    selected = None
                paths = archive.extract(
                    dest, selected=selected,
                    inflate=not args.no_inflate,
                    sanitize=not args.no_sanitize,
                    verify=not args.no_verify,
                )
            print(f"wrote {len(paths)} entries to {dest.resolve()}")
            return 0

        if args.command == "pack":
            if bool(args.files) == bool(args.src_dir):
                print("error: provide files or --from DIR (not both/none)",
                      file=sys.stderr)
                return 2
            specs = [WriteEntry(name=path.name, source=path, method=args.method)
                     for path in args.files]
            result = create(args.archive, specs if args.files else None,
                            src_dir=args.src_dir, prefix=args.prefix,
                            seed=args.seed, level=args.level,
                            method=args.method, overwrite=args.overwrite)
            with FunArchive(result) as archive:
                count = len(archive.read())
            print(f"wrote {result} ({count} entries)")
            return 0
    except (FunArchiveError, OSError, ValueError, zlib.error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
