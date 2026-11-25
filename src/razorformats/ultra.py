"""Ultra-compressed, restorable game builds.

This is deliberately *not* lossless.  It rewrites assets into compact forms
that ``ffmpeg`` can decode, then rebuilds playable originals before launch:

* WAV payloads are concatenated per (sample rate, channel count) group and
  encoded as one Opus stream per group; restore slices the decoded PCM back
  into the original WAV headers and data lengths,
* TGA payloads are encoded as WebP and concatenated per (width, height, depth)
  group; restore decodes one WebP stream per group and rebuilds the original
  TGA headers and rows,
* all other bytes are stored verbatim, and ``.fun`` entry metadata is kept so
  rebuilt archives are structurally identical to what the game expects.

Grouping matters: the whole build needs only a few dozen ``ffmpeg`` processes
instead of one per asset, so restore is fast even on a full 500 MB build.

The bundle is a normal ``.tar.xz``.  Extracting it alone is not enough: run
``razor-ultra-restore`` (or ``python -m razorformats.cli ultra-restore``).
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .fun import (
    FunArchive, HEADER_FMT, HEADER_SIZE, METHOD_DEFLATE, SIG_INCREMENT,
    SIG_KEY, ZIP_SIGNATURE, rolling_xor,
)
from .tga import read_tga
from .wav import read_wav_header

MANIFEST_NAME = "ultra-manifest.json"
VERSION = 2
# Rebuilt .fun archives only need to be readable, not small.  A fast zlib
# level keeps restore time low; the shipped bundle already holds the compact
# representation.
RESTORE_FUN_LEVEL = 6


def ffmpeg_path() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise RuntimeError("ffmpeg is required for ultra bundles")
    return path


def _ffmpeg(args: list[str], data: bytes) -> bytes:
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y", *args],
        input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(f"ffmpeg failed: {detail}")
    return proc.stdout


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text)


def _default_jobs() -> int:
    return max(1, os.cpu_count() or 4)


def _is_wav(name: str) -> bool:
    return name.lower().endswith(".wav")


def _is_tga(name: str) -> bool:
    return name.lower().endswith((".tga", "._tga_", ".tga_"))


# --------------------------------------------------------------------------- #
# Per-asset metadata
# --------------------------------------------------------------------------- #

def _wav_parts(wav: bytes) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Return (metadata, raw PCM, item metadata) for a PCM WAV."""
    info = read_wav_header(wav)
    fmt = info["fmt"]
    if fmt["audio_format"] != 1:
        raise ValueError(f"unsupported WAV format {fmt['audio_format']}")
    chunk = next((c for c in info["chunks"] if c["id"] == "data"), None)
    if chunk is None:
        raise ValueError("WAV has no data chunk")
    offset = chunk["offset"]
    size = chunk["size"] or (len(wav) - offset)
    meta = {
        "header_b64": _b64(wav[:offset]),
        "trailer_b64": _b64(wav[offset + size:]),
        "data_size": size,
        "rate": fmt["sample_rate"],
        "channels": fmt["channels"],
    }
    item = {
        "size": size,
        "header_b64": meta["header_b64"],
        "trailer_b64": meta["trailer_b64"],
        "rate": fmt["sample_rate"],
        "channels": fmt["channels"],
    }
    return meta, wav[offset:offset + size], item


def _tga_parts(tga: bytes) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    """Return (metadata, RGBA pixels, item metadata) for a true-colour TGA."""
    image = read_tga(tga)
    header_len = 18 + len(image.id_field)
    if image.image_type != 2:
        raise ValueError(f"unsupported TGA image type {image.image_type}")
    pixel_len = image.width * image.height * (image.pixel_depth // 8)
    meta = {
        "width": image.width,
        "height": image.height,
        "depth": image.pixel_depth,
        "descriptor": tga[17],
        "header_b64": _b64(tga[:header_len]),
        "trailer_b64": _b64(tga[header_len + pixel_len:]),
    }
    item = {
        "size": image.width * image.height * 4,
        "width": image.width,
        "height": image.height,
        "depth": image.pixel_depth,
        "descriptor": tga[17],
        "header_b64": meta["header_b64"],
        "trailer_b64": meta["trailer_b64"],
    }
    return meta, image.rgba, item


def _pcm_to_wav(item: dict[str, Any], pcm: bytes) -> bytes:
    size = item["size"]
    if len(pcm) < size:
        pcm += b"\x00" * (size - len(pcm))
    return (_unb64(item["header_b64"]) + pcm[:size]
            + _unb64(item["trailer_b64"]))


def _rgba_to_tga(item: dict[str, Any], rgba: bytes) -> bytes:
    width = item["width"]
    height = item["height"]
    depth = item["depth"]
    stride = depth // 8
    expected = width * height * 4
    if len(rgba) < expected:
        rgba += b"\x00" * (expected - len(rgba))
    src = memoryview(rgba[:expected])
    pixels = bytearray(width * height * stride)
    pixels[0::stride] = src[2::4]  # B
    pixels[1::stride] = src[1::4]  # G
    pixels[2::stride] = src[0::4]  # R
    if stride == 4:
        pixels[3::stride] = src[3::4]
    if not (item["descriptor"] & 0x20):
        rows = [bytes(pixels[y * width * stride:(y + 1) * width * stride])
                for y in range(height)]
        rows.reverse()
        pixels = bytearray(b"".join(rows))
    return (_unb64(item["header_b64"]) + bytes(pixels)
            + _unb64(item["trailer_b64"]))


# --------------------------------------------------------------------------- #
# Packing
# --------------------------------------------------------------------------- #

@dataclass
class _Group:
    id: str
    kind: str
    key: tuple[Any, ...]
    path: Path
    items: list[dict[str, Any]]

    def descriptor(self, relative: str) -> dict[str, Any]:
        desc: dict[str, Any] = {
            "id": self.id, "kind": self.kind, "path": relative,
            "count": len(self.items), "items": self.items,
        }
        if self.kind == "opus":
            desc["rate"], desc["channels"] = self.key
        else:
            desc["width"], desc["height"], desc["depth"] = self.key
        return desc


def _iter_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _classify(data: bytes, name: str, stage: Path, raw_index: int,
              audio: dict[tuple[Any, ...], _Group],
              images: dict[tuple[Any, ...], _Group],
              rgba_tasks: list[tuple[Path, Path, int, int, str]]
              ) -> tuple[dict[str, Any], int]:
    """Store one asset; return its manifest reference and stored byte count."""
    if _is_wav(name):
        _meta, pcm, item = _wav_parts(data)
        key = (item["rate"], item["channels"])
        group = audio.get(key)
        if group is None:
            group = _Group(f"a{len(audio)}", "opus", key,
                           stage / "audio" / f"{len(audio):03d}.pcm", [])
            audio[key] = group
            group.path.parent.mkdir(parents=True, exist_ok=True)
            group.path.touch()
        index = len(group.items)
        group.items.append(item)
        with group.path.open("ab") as handle:
            handle.write(pcm)
        return {"kind": "opus", "stream": group.id, "index": index}, 0

    if _is_tga(name):
        _meta, rgba, item = _tga_parts(data)
        key = (item["width"], item["height"], item["depth"])
        group = images.get(key)
        if group is None:
            group = _Group(f"w{len(images)}", "webp", key,
                           stage / "images" / f"{len(images):03d}.webp", [])
            images[key] = group
            group.path.parent.mkdir(parents=True, exist_ok=True)
            group.path.touch()
        index = len(group.items)
        group.items.append(item)
        raw = stage / "images" / f"{group.id}-{index:06d}.rgba"
        raw.write_bytes(rgba)
        rgba_tasks.append((raw, stage / "images" / f"{group.id}-{index:06d}.webp",
                           item["width"], item["height"], item["depth"]))
        return {"kind": "webp", "stream": group.id, "index": index}, 0

    payload = stage / "payload" / f"{raw_index:06d}.bin"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(data)
    return ({"kind": "raw", "payload": f"payload/{raw_index:06d}.bin"},
            len(data))


def _encode_rgba(job: tuple[Path, Path, int, int, str],
                 quality: int) -> int:
    src, dest, width, height, depth = job
    rgba = src.read_bytes()
    webp = _ffmpeg([
        "-f", "rawvideo", "-pix_fmt", "rgba",
        "-s", f"{width}x{height}", "-i", "pipe:0",
        "-c:v", "libwebp", "-quality", str(quality),
        "-pix_fmt", "rgb24" if depth == 24 else "rgba",
        "-f", "webp", "pipe:1",
    ], rgba)
    dest.write_bytes(webp)
    src.unlink()
    return len(webp)


def _finalize_audio(group: _Group, dest: Path, bitrate: int) -> int:
    pcm = group.path.read_bytes()
    webm = _ffmpeg([
        "-f", "s16le", "-ar", str(group.key[0]), "-ac", str(group.key[1]),
        "-i", "pipe:0", "-vn", "-c:a", "libopus", "-b:a", f"{bitrate}k",
        "-ac", str(group.key[1]), "-ar", "48000", "-f", "ogg", "pipe:1",
    ], pcm)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(webm)
    group.path.unlink()
    return len(webm)


def pack_ultra(src: str | Path, archive: str | Path, *,
               opus_bitrate: int = 64, webp_quality: int = 80,
               jobs: int | None = None,
               progress: Callable[[int, int, str], None] | None = None
               ) -> dict[str, Any]:
    """Pack a build into a small, restorable, lossy ``.tar.xz`` bundle."""
    src = Path(src)
    archive = Path(archive)
    if not src.is_dir():
        raise FileNotFoundError(src)
    archive.parent.mkdir(parents=True, exist_ok=True)
    jobs = jobs or _default_jobs()
    files = _iter_files(src)

    records: list[dict[str, Any]] = []
    audio: dict[tuple[Any, ...], _Group] = {}
    images: dict[tuple[Any, ...], _Group] = {}
    rgba_tasks: list[tuple[Path, Path, int, int, str]] = []
    raw_index = 0

    with tempfile.TemporaryDirectory(prefix="razor_ultra_",
                                     dir=archive.parent) as temp:
        stage = Path(temp)
        (stage / "payload").mkdir()
        (stage / "audio").mkdir()
        (stage / "images").mkdir()
        (stage / "streams").mkdir()

        for file_index, path in enumerate(files, start=1):
            relative = _relative(path, src)
            info = path.stat()
            base = {"relative": relative, "mode": info.st_mode & 0o777,
                    "bytes_before": info.st_size}
            stored = 0
            if path.suffix.lower() == ".fun":
                entries_json: list[dict[str, Any]] = []
                with FunArchive(path) as fun:
                    for entry in fun.entries:
                        ref, size = _classify(
                            fun.read_file(entry), entry.name, stage,
                            raw_index, audio, images, rgba_tasks)
                        if size:
                            raw_index += 1
                        stored += size
                        entries_json.append({"meta": _entry_meta(entry), **ref})
                fun_id = f"{file_index:06d}"
                entries_path = stage / "fun" / fun_id / "entries.json"
                entries_path.parent.mkdir(parents=True, exist_ok=True)
                entries_path.write_text(
                    json.dumps(entries_json, ensure_ascii=False),
                    encoding="utf-8")
                stored += entries_path.stat().st_size
                record = {**base, "kind": "fun",
                          "entries_json": f"fun/{fun_id}/entries.json",
                          "bytes_after": stored}
            else:
                ref, size = _classify(
                    path.read_bytes(), path.name, stage, raw_index,
                    audio, images, rgba_tasks)
                if size:
                    raw_index += 1
                record = {**base, **ref, "bytes_after": size}
            records.append(record)
            if progress is not None:
                progress(file_index, len(files), relative)

        total_tasks = len(rgba_tasks) + len(audio) + len(images)
        done = 0
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            if rgba_tasks:
                futures = [pool.submit(_encode_rgba, task, webp_quality)
                           for task in rgba_tasks]
                for future in as_completed(futures):
                    future.result()
                    done += 1
                    if progress is not None:
                        progress(done, total_tasks, "encoding WebP")

            futures = [pool.submit(
                _finalize_audio, group,
                stage / "streams" / f"{group.id}.bin", opus_bitrate)
                for group in audio.values()]
            for future in as_completed(futures):
                future.result()
                done += 1
                if progress is not None:
                    progress(done, total_tasks, "encoding Opus")

        # Concatenate WebP images per group in deterministic item order.
        for group in images.values():
            dest = stage / "streams" / f"{group.id}.bin"
            with dest.open("wb") as out:
                for index in range(len(group.items)):
                    part = stage / "images" / f"{group.id}-{index:06d}.webp"
                    out.write(part.read_bytes())
                    part.unlink()
            done += 1
            if progress is not None:
                progress(done, total_tasks, f"joining {group.id}")

        stream_descriptors = [
            group.descriptor(f"streams/{group.id}.bin")
            for group in list(audio.values()) + list(images.values())
        ]
        stream_bytes = sum(
            (stage / d["path"]).stat().st_size for d in stream_descriptors)
        manifest = {
            "kind": "razor-ultra",
            "version": VERSION,
            "source": str(src),
            "opus_bitrate": opus_bitrate,
            "webp_quality": webp_quality,
            "lossy": True,
            "files": len(records),
            "bytes_before": sum(r["bytes_before"] for r in records),
            "stored_bytes_after_transform": (
                sum(r.get("bytes_after", 0) for r in records) + stream_bytes),
            "streams": stream_descriptors,
            "records": records,
        }
        (stage / MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")

        # Preset 6 keeps the bundle well under 100 MB while making both
        # packing and restore noticeably faster than the extreme preset.
        with tarfile.open(archive, "w:xz", preset=6) as tar:
            for item in sorted(stage.rglob("*")):
                if item.is_file():
                    tar.add(item, arcname=_relative(item, stage),
                            recursive=False)

    archive_bytes = archive.stat().st_size
    manifest["archive_bytes"] = archive_bytes
    manifest["ratio"] = (archive_bytes / manifest["bytes_before"]
                         if manifest["bytes_before"] else 0.0)
    archive.with_suffix(".json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return manifest


def _entry_meta(entry: Any) -> dict[str, Any]:
    return {
        "name": entry.name,
        "method": entry.method,
        "seed": list(entry.seed),
        "flag": entry.flag,
        "mod_time": entry.mod_time,
        "mod_date": entry.mod_date,
        "crc32": entry.crc32,
        "extra": entry.extra.hex(),
    }


# --------------------------------------------------------------------------- #
# Restoring
# --------------------------------------------------------------------------- #

def _decode_stream(job: tuple[dict[str, Any], Path]) -> tuple[str, bytes]:
    descriptor, stage = job
    blob = (stage / descriptor["path"]).read_bytes()
    if descriptor["kind"] == "opus":
        decoded = _ffmpeg([
            "-i", "pipe:0", "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ar", str(descriptor["rate"]),
            "-ac", str(descriptor["channels"]), "pipe:1",
        ], blob)
    elif descriptor["kind"] == "webp":
        decoded = _ffmpeg([
            "-f", "webp_pipe", "-i", "pipe:0",
            "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1",
        ], blob)
    else:
        raise ValueError(f"unknown stream kind {descriptor['kind']!r}")
    expected = sum(item["size"] for item in descriptor["items"])
    if len(decoded) < expected:
        decoded += b"\x00" * (expected - len(decoded))
    return descriptor["id"], decoded[:expected]


def _stream_offsets(descriptor: dict[str, Any]) -> list[tuple[int, int]]:
    offsets: list[tuple[int, int]] = []
    pos = 0
    for item in descriptor["items"]:
        size = item["size"]
        offsets.append((pos, size))
        pos += size
    return offsets


def _restore_one_asset(ref: dict[str, Any], stage: Path,
                       streams: dict[str, bytes],
                       offsets: dict[str, list[tuple[int, int]]],
                       descriptors: dict[str, dict[str, Any]]) -> bytes:
    kind = ref["kind"]
    if kind == "raw":
        return (stage / ref["payload"]).read_bytes()
    stream_id = ref["stream"]
    index = ref["index"]
    start, size = offsets[stream_id][index]
    chunk = streams[stream_id][start:start + size]
    item = descriptors[stream_id]["items"][index]
    if kind == "opus":
        return _pcm_to_wav(item, chunk)
    if kind == "webp":
        return _rgba_to_tga(item, chunk)
    raise ValueError(f"unknown asset kind {kind!r}")


def _encode_fun_entry(meta: dict[str, Any], data: bytes) -> bytes:
    """Encode one .fun local-file record (same layout as fun.write)."""
    key, inc = meta["seed"]
    name = meta["name"].encode("latin-1")
    extra = bytes.fromhex(meta["extra"])
    method = meta["method"]
    if method == METHOD_DEFLATE:
        compressor = zlib.compressobj(
            RESTORE_FUN_LEVEL, zlib.DEFLATED, -15)
        payload = compressor.compress(data) + compressor.flush()
        payload += struct.pack(">I", zlib.adler32(data))
    else:
        payload = data
    header = struct.pack(
        HEADER_FMT, meta["flag"], method, meta["mod_time"],
        meta["mod_date"], meta["crc32"], len(payload), len(data),
        len(name), len(extra))
    record = bytearray(
        rolling_xor(ZIP_SIGNATURE, SIG_KEY, SIG_INCREMENT)[0]
        + bytes((key, inc)) + b"\x00" * HEADER_SIZE)
    record += rolling_xor(name, key, inc)[0]
    record += extra
    record += payload
    record[6:6 + HEADER_SIZE] = rolling_xor(header, key, inc)[0]
    return bytes(record)


def _restore_fun(job: tuple[dict[str, Any], Path, Path, Path,
                            dict[str, bytes],
                            dict[str, list[tuple[int, int]]],
                            dict[str, dict[str, Any]]]) -> str:
    record, stage, out, _manifest_path, streams, offsets, descriptors = job
    entries = json.loads(
        (stage / record["entries_json"]).read_text(encoding="utf-8"))
    parts: list[bytes] = []
    for ref in entries:
        data = _restore_one_asset(ref, stage, streams, offsets, descriptors)
        parts.append(_encode_fun_entry(ref["meta"], data))
    target = out / record["relative"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"".join(parts))
    target.chmod(record.get("mode", 0o644))
    return record["relative"]


def _restore_loose(job: tuple[dict[str, Any], Path, Path, Path,
                              dict[str, bytes],
                              dict[str, list[tuple[int, int]]],
                              dict[str, dict[str, Any]]]) -> str:
    record, stage, out, _manifest_path, streams, offsets, descriptors = job
    target = out / record["relative"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_restore_one_asset(
        record, stage, streams, offsets, descriptors))
    target.chmod(record.get("mode", 0o644))
    return record["relative"]


def _extract_tar(archive: Path, stage: Path) -> None:
    with tarfile.open(archive, "r:xz") as tar:
        try:
            tar.extractall(stage, filter="data")
        except TypeError:  # Python < 3.12
            tar.extractall(stage)


def restore_ultra(archive: str | Path, out: str | Path, *,
                  jobs: int | None = None,
                  progress: Callable[[int, int, str], None] | None = None
                  ) -> dict[str, Any]:
    """Regenerate a playable game tree from an ultra bundle."""
    archive = Path(archive)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="razor_ultra_restore_",
                                     dir=out.parent) as temp:
        stage = Path(temp)
        _extract_tar(archive, stage)
        manifest_path = stage / MANIFEST_NAME
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("kind") != "razor-ultra":
            raise ValueError("not a razor ultra bundle")
        jobs = jobs or _default_jobs()

        descriptors = {d["id"]: d for d in manifest.get("streams", [])}
        streams: dict[str, bytes] = {}
        stream_jobs = [(d, stage) for d in descriptors.values()]
        done = 0
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(_decode_stream, job) for job in stream_jobs]
            for future in as_completed(futures):
                stream_id, decoded = future.result()
                streams[stream_id] = decoded
                done += 1
                if progress is not None:
                    progress(done, len(stream_jobs), f"decoding {stream_id}")

        offsets = {key: _stream_offsets(value)
                   for key, value in descriptors.items()}
        tasks: list[tuple[Any, ...]] = []
        for record in manifest["records"]:
            common = (record, stage, out, manifest_path,
                      streams, offsets, descriptors)
            tasks.append((_restore_fun if record["kind"] == "fun"
                          else _restore_loose, common))

        total = len(stream_jobs) + len(tasks)
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(fn, args) for fn, args in tasks]
            for future in as_completed(futures):
                result = future.result()
                done += 1
                if progress is not None:
                    progress(done, total, result)

    return {
        "restored": len(manifest["records"]),
        "out": str(out),
        "lossy": True,
        "note": "Audio and textures were regenerated, not byte-identical.",
    }


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #

def _wav_summary(data: bytes) -> tuple[Any, ...]:
    info = read_wav_header(data)
    chunk = next(c for c in info["chunks"] if c["id"] == "data")
    return (tuple(sorted(info["fmt"].items())), chunk["size"], len(data))


def _tga_summary(data: bytes) -> tuple[bytes, int]:
    return (data[:18 + data[0]], len(data))


def _compare_asset(problems: list[str], label: str,
                   original: bytes, restored: bytes, name: str) -> None:
    if _is_wav(name):
        if _wav_summary(original) != _wav_summary(restored):
            problems.append(f"WAV structure: {label}")
    elif _is_tga(name):
        if _tga_summary(original) != _tga_summary(restored):
            problems.append(f"TGA structure: {label}")
    elif original != restored:
        problems.append(f"bytes differ: {label}")


def _verify_one(job: tuple[dict[str, Any], Path, Path, Path]
                ) -> tuple[str, list[str]]:
    record, original, restored, stage = job
    relative = record["relative"]
    problems: list[str] = []
    try:
        a = original / relative
        b = restored / relative
        if not b.is_file():
            return relative, [f"missing: {relative}"]
        if record["kind"] == "fun":
            with FunArchive(a) as fa, FunArchive(b) as fb:
                ea, eb = fa.entries, fb.entries
                if len(ea) != len(eb):
                    problems.append(f"entry count differs: {relative}")
                else:
                    entries = json.loads(
                        (stage / record["entries_json"]).read_text(
                            encoding="utf-8"))
                    for x, y, ref in zip(ea, eb, entries):
                        left = (x.name, x.method, x.seed, x.flag,
                                x.mod_time, x.mod_date, x.extra)
                        right = (y.name, y.method, y.seed, y.flag,
                                 y.mod_time, y.mod_date, y.extra)
                        if left != right:
                            problems.append(
                                f"entry metadata: {relative}:{x.name}")
                        _compare_asset(problems,
                                       f"{relative}:{x.name}",
                                       fa.read_file(x), fb.read_file(y),
                                       x.name)
        else:
            _compare_asset(problems, relative, a.read_bytes(),
                           b.read_bytes(), a.name)
    except Exception as exc:  # noqa: BLE001 - report, do not abort the pool
        problems.append(f"error {relative}: {exc}")
    return relative, problems


def verify_ultra(original: str | Path, restored: str | Path,
                 manifest_path: str | Path, *,
                 jobs: int | None = None,
                 progress: Callable[[int, int, str], None] | None = None
                 ) -> dict[str, Any]:
    """Check that a restored tree is structurally playable.

    Raw files must match byte-for-byte.  Audio and textures are compared by
    header/structure only, because the ultra format is intentionally lossy.
    """
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest["records"]
    jobs = jobs or _default_jobs()

    # entries.json files live inside the bundle, not beside the manifest.
    with tempfile.TemporaryDirectory(prefix="razor_ultra_verify_",
                                     dir=manifest_path.parent) as temp:
        stage = Path(temp)
        if manifest_path.name != MANIFEST_NAME:
            bundle = Path(str(manifest_path)[:-5] + ".xz") \
                if str(manifest_path).endswith(".json") else \
                manifest_path.with_suffix(".xz")
            _extract_tar(bundle, stage)
        counts: dict[str, int] = {}
        mismatches: list[str] = []
        done = 0
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            futures = [pool.submit(
                _verify_one,
                (r, Path(original), Path(restored), stage))
                for r in records]
            for future in as_completed(futures):
                relative, problems = future.result()
                done += 1
                mismatches.extend(problems)
                if progress is not None:
                    progress(done, len(records), relative)

        for record in records:
            kind = "fun" if record["kind"] == "fun" else record["kind"]
            counts[kind] = counts.get(kind, 0) + 1
        return {"checked": done, "counts": counts,
                "mismatches": mismatches, "ok": not mismatches}
