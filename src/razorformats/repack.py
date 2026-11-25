"""Lossless repacking and bundling of a complete game build.

Two operations are provided:

* :func:`repack_build` rebuilds every ``.fun`` archive at maximum zlib
  compression while leaving all other files byte-for-byte untouched.  Each
  archive entry is compared by SHA-256 before and after, so the game sees
  exactly the same bytes it did originally.
* :func:`bundle_build` writes a single ``.tar.xz`` containing the repacked
  build, with a SHA-256 manifest for post-extraction verification.  The XZ
  stream uses a delta filter tuned for the build's 16-bit stereo PCM audio;
  it is still a normal ``.tar.xz`` that any standard tar/xz can extract.

Nothing here changes executable instructions, asset semantics or file paths;
it only changes how identical bytes are stored on disk.
"""

from __future__ import annotations

import hashlib
import io
import json
import lzma
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Any

from .fun import FunArchive, WriteEntry, write

MANIFEST_NAME = "repack-manifest.json"

# 4-byte delta matches one 16-bit stereo PCM frame and dramatically improves
# compression of the large music WAVs.  Standard xz decoders apply this filter
# automatically, so the resulting .tar.xz remains portable.
DEFAULT_XZ_FILTERS: list[dict[str, int]] = [
    {"id": lzma.FILTER_DELTA, "dist": 4},
    {"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME,
     "lc": 3, "lp": 1, "pb": 2},
]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _entry_record(entry: Any, digest: str) -> dict[str, Any]:
    """Metadata that must survive a lossless ``.fun`` repack."""
    return {
        "index": entry.index,
        "name": entry.name,
        "method": entry.method,
        "flag": entry.flag,
        "seed": list(entry.seed),
        "mod_time": entry.mod_time,
        "mod_date": entry.mod_date,
        "crc32": entry.crc32,
        "extra": entry.extra.hex(),
        "uncomp_size": entry.uncomp_size,
        "sha256": digest,
    }


def _archive_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with FunArchive(path) as archive:
        for entry in archive.entries:
            records.append(_entry_record(entry, sha256(archive.read_file(entry))))
    return records


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def repack_fun(src: str | Path, dst: str | Path, *, level: int = 9,
               relative: str | None = None) -> dict[str, Any]:
    """Rebuild one ``.fun`` archive, verifying every decompressed entry."""
    if not 0 <= level <= 9:
        raise ValueError("level must be 0..9")
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    specs: list[WriteEntry] = []
    original: list[dict[str, Any]] = []
    with FunArchive(src) as archive:
        for entry in archive.entries:
            data = archive.read_file(entry)
            original.append(_entry_record(entry, sha256(data)))
            specs.append(WriteEntry(
                name=entry.name, source=data, method=entry.method,
                seed=entry.seed, flag=entry.flag,
                mod_time=entry.mod_time, mod_date=entry.mod_date,
                crc32=entry.crc32, extra=entry.extra,
                uncomp_size=entry.uncomp_size))

    write(dst, specs, level=level, overwrite=True)
    rebuilt = _archive_records(dst)
    if rebuilt != original:
        raise ValueError(f"entry metadata or content changed in {src.name}")

    before = src.stat().st_size
    after = dst.stat().st_size
    return {
        "kind": "fun",
        "relative": relative if relative is not None else src.name,
        "file": str(src),
        "entries": len(specs),
        "entry_records": original,
        "sha256_original": _digest_file(src),
        "sha256_stored": _digest_file(dst),
        "bytes_before": before,
        "bytes_after": after,
        "saved": before - after,
        "verified": True,
    }


def _iter_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file())


def _file_record(path: Path, root: Path) -> dict[str, Any]:
    relative = _relative(path, root)
    before = path.stat().st_size
    digest = _digest_file(path)
    return {
        "kind": "file",
        "relative": relative,
        "file": str(path),
        "entries": None,
        "sha256_original": digest,
        "sha256_stored": digest,
        "bytes_before": before,
        "bytes_after": before,
        "saved": 0,
        "verified": True,
    }


def _repack_record(path: Path, target: Path, root: Path,
                   level: int) -> dict[str, Any]:
    if path.suffix.lower() == ".fun":
        return repack_fun(path, target, level=level,
                          relative=_relative(path, root))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    record = _file_record(path, root)
    if _digest_file(target) != record["sha256_stored"]:
        raise ValueError(f"copy mismatch for {record['relative']}")
    return record


def repack_build(src: str | Path, dst: str | Path, *,
                 level: int = 9) -> dict[str, Any]:
    """Copy a build tree, repacking ``.fun`` archives losslessly."""
    src = Path(src)
    dst = Path(dst)
    if not src.is_dir():
        raise FileNotFoundError(src)
    if src.resolve() == dst.resolve() or src.resolve() in dst.resolve().parents:
        raise ValueError("destination must be outside the source tree")

    files = _iter_files(src)
    dst.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    total_before = total_after = 0
    for path in files:
        record = _repack_record(path, dst / path.relative_to(src), src, level)
        records.append(record)
        total_before += record["bytes_before"]
        total_after += record["bytes_after"]

    manifest = {
        "kind": "lossless-repack",
        "source": str(src), "destination": str(dst),
        "zlib_level": level, "files": len(records),
        "bytes_before": total_before, "bytes_after": total_after,
        "saved": total_before - total_after,
        "note": "Every .fun entry was SHA-256 and metadata checked after "
                "recompression; all other files are byte-identical copies.",
        "records": records,
    }
    (dst / MANIFEST_NAME).write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return manifest


def _xz_kwargs(preset: int | None,
               filters: list[dict[str, int]] | None) -> dict[str, Any]:
    if filters is not None:
        return {"filters": filters}
    if preset is None:
        preset = 9 | lzma.PRESET_EXTREME
    return {"preset": preset}


def _describe_filters(filters: list[dict[str, int]] | None) -> Any:
    if filters is None:
        return "default-preset-9e"
    names = {lzma.FILTER_DELTA: "delta", lzma.FILTER_LZMA2: "lzma2",
             lzma.FILTER_LZMA1: "lzma1"}
    return [{**f, "id": names.get(f["id"], f["id"])} for f in filters]


def bundle_build(src: str | Path, archive: str | Path, *,
                 level: int = 9, preset: int | None = None,
                 filters: list[dict[str, int]] | None = None) -> dict[str, Any]:
    """Write a ``.tar.xz`` of a build, repacking ``.fun`` files on the fly."""
    src = Path(src)
    archive = Path(archive)
    if not src.is_dir():
        raise FileNotFoundError(src)
    archive.parent.mkdir(parents=True, exist_ok=True)
    xz_kwargs = _xz_kwargs(preset, filters)
    used_filters = filters if filters is not None else _describe_filters(None)

    records: list[dict[str, Any]] = []
    total_before = total_after = 0
    # Keep the uncompressed staging tar on the destination filesystem: /tmp
    # is often a small tmpfs and a full game build can be close to 500 MB.
    with tempfile.TemporaryDirectory(prefix="razor_repack_",
                                     dir=archive.parent) as temp:
        scratch = Path(temp)
        tar_path = scratch / "build.tar"
        with tarfile.open(tar_path, "w", format=tarfile.PAX_FORMAT) as tar:
            for path in _iter_files(src):
                relative = _relative(path, src)
                record = _repack_record(path, scratch / path.relative_to(src),
                                        src, level)
                records.append(record)
                tar.add(scratch / path.relative_to(src), arcname=relative,
                        recursive=False)
                (scratch / path.relative_to(src)).unlink()
                total_before += record["bytes_before"]
                total_after += record["bytes_after"]

            manifest: dict[str, Any] = {
                "kind": "lossless-bundle",
                "source": str(src), "archive": archive.name,
                "xz_filters": _describe_filters(filters),
                "zlib_level": level, "files": len(records),
                "bytes_before": total_before,
                "stored_bytes_after_repack": total_after,
                "records": records,
            }
            payload = (json.dumps(manifest, indent=2,
                                  ensure_ascii=False) + "\n").encode("utf-8")
            info = tarfile.TarInfo(MANIFEST_NAME)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))

        with tar_path.open("rb") as fh, lzma.open(archive, "wb", **xz_kwargs) as out:
            shutil.copyfileobj(fh, out, 1 << 20)

    bytes_after = archive.stat().st_size
    manifest["archive_bytes"] = bytes_after
    manifest["xz_filters_used"] = used_filters
    manifest["ratio"] = (bytes_after / total_before if total_before else 0.0)
    archive.with_suffix(".json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return manifest


def bundle_manifest(archive: str | Path) -> dict[str, Any]:
    """Read the manifest embedded in a bundle produced by :func:`bundle_build`."""
    with lzma.open(archive, "rb") as fh, tarfile.open(fileobj=fh, mode="r|") as tar:
        for member in tar:
            if member.name == MANIFEST_NAME:
                extracted = tar.extractfile(member)
                if extracted is None:
                    break
                return json.loads(extracted.read().decode("utf-8"))
    raise ValueError(f"no {MANIFEST_NAME} in {archive}")


def verify_build(root: str | Path, manifest_path: str | Path, *,
                 original: bool = False) -> dict[str, Any]:
    """Verify an extracted (or original) tree against a repack manifest."""
    root = Path(root)
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    checked = 0
    mismatches: list[str] = []

    for record in manifest["records"]:
        relative = record.get("relative", Path(record["file"]).name)
        target = root / relative
        if not target.is_file():
            mismatches.append(f"missing: {relative}")
            continue
        checked += 1
        expected_size = record["bytes_before" if original else "bytes_after"]
        if target.stat().st_size != expected_size:
            mismatches.append(f"size differs: {relative}")
            continue
        expected_digest = record[f"sha256_{'original' if original else 'stored'}"]
        if _digest_file(target) != expected_digest:
            mismatches.append(f"content differs: {relative}")
            continue
        if record["kind"] == "fun":
            actual = _archive_records(target)
            expected = record["entry_records"]
            if actual != expected:
                mismatches.append(f"archive entries differ: {relative}")

    return {"checked": checked, "mismatches": mismatches,
            "ok": not mismatches and checked == len(manifest["records"])}
