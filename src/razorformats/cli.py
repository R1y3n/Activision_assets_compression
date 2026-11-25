"""Command-line interface: archive listing/extraction, inspection and conversion."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

from .fun import FunArchive, FunArchiveError


def _print_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def main_extract(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="razor-extract",
                                  description="List or extract .fun archives")
    ap.add_argument("archive", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="destination directory (default: <archive>_extracted)")
    ap.add_argument("--list", action="store_true", help="only print the manifest")
    ap.add_argument("--filter", action="append", default=[],
                    help="extract entries whose name contains this text (repeatable)")
    ap.add_argument("--json", action="store_true", help="emit JSON manifest")
    ap.add_argument("--no-inflate", action="store_true")
    ap.add_argument("--no-verify", action="store_true")
    args = ap.parse_args(argv)

    try:
        with FunArchive(args.archive) as archive:
            entries = archive.read()
            if args.list:
                manifest = archive.manifest()
                if args.json:
                    _print_json({"archive": str(args.archive),
                                 "size": archive.size,
                                 "trailing_bytes": archive.trailing_bytes,
                                 "entries": manifest})
                else:
                    for entry in entries:
                        print(f"[{entry.index:5d}] {entry.name}  "
                              f"{entry.method_name} comp={entry.comp_size} "
                              f"uncomp={entry.uncomp_size}")
                return 0

            selected: Callable | None = None
            if args.filter:
                needles = [n.lower() for n in args.filter]
                selected = lambda e: any(n in e.name.lower() for n in needles)
            dest = args.out or Path(f"{args.archive.stem}_extracted")
            written = archive.extract(
                dest, selected=selected, inflate=not args.no_inflate,
                verify=not args.no_verify)
            print(f"extracted {len(written)} entries -> {dest}")
        return 0
    except (FunArchiveError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _load(module: str, name: str):
    try:
        mod = __import__(f"razorformats.{module}", fromlist=[name])
    except ImportError as exc:
        print(f"error: {module} reader is not available yet: {exc}",
              file=sys.stderr)
        raise SystemExit(2) from exc
    return getattr(mod, name)


def main_inspect(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="razor-inspect",
                                  description="Inspect a proprietary game file")
    ap.add_argument("file", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    from .convert import sniff_format
    ext = sniff_format(args.file)
    if ext == "fun":
        with FunArchive(args.file) as archive:
            value = {"archive": str(args.file), "size": archive.size,
                     "trailing_bytes": archive.trailing_bytes,
                     "entries": archive.manifest()}
        _print_json(value)
        return 0

    module, func = {
        "obj": ("obj", "inspect_obj"),
        "anim": ("anim", "inspect_anim"),
        "model": ("model", "parse_model"),
        "script": ("script", "parse_script"),
        "fx": ("fx", "parse_fx"),
        "fnt": ("fnt", "read_fnt"),
        "bsp": ("bsp", "inspect_bsp"),
        "tga": ("tga", "inspect_tga"),
        "wav": ("wav", "read_wav_header"),
    }.get(ext, (None, None))
    if module is None:
        try:
            text = args.file.read_bytes().decode("ascii")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"error: no inspector for {ext!r}: {exc}", file=sys.stderr)
            return 2
        _print_json({"format": "text", "source": str(args.file),
                     "lines": text.splitlines()})
        return 0
    reader = _load(module, func)
    try:
        value = reader(args.file.read_bytes())
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    if args.json:
        _print_json(value)
    else:
        print(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    return 0


def main_convert(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="razor-convert",
                                  description="Convert game files to open formats")
    ap.add_argument("source", type=Path, help="file or directory to convert")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="output directory")
    ap.add_argument("--format", choices=("auto", "gltf", "obj", "json", "png"),
                    default="auto")
    ap.add_argument("--texture", type=Path, default=None,
                    help="TGA/PNG texture to attach to a mesh conversion")
    ap.add_argument("--anim", action="append", default=[],
                    help="animation .anim file to attach (repeatable)")
    ap.add_argument("--extract-only", action="store_true",
                    help="for .fun input, only extract; do not convert entries")
    args = ap.parse_args(argv)

    from .convert import convert_archive, convert_path
    try:
        if (args.source.is_file()
                and args.source.suffix.lower() == ".fun"
                and not args.extract_only):
            outputs = convert_archive(args.source, args.out)
        else:
            outputs = convert_path(args.source, args.out, fmt=args.format,
                                   texture=args.texture, animations=args.anim)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path in outputs:
        print(path)
    return 0


def main_batch(argv: Sequence[str] | None = None) -> int:
    """Convert every ``.fun`` archive in a directory with the full pipeline."""
    ap = argparse.ArgumentParser(
        prog="razor-convert-all",
        description="Convert every .fun archive in a directory to open formats")
    ap.add_argument("source", type=Path,
                    help="directory containing .fun files (or one .fun file)")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="output directory; each archive gets its own subdirectory")
    ap.add_argument("--pattern", default="*.fun",
                    help="archive glob pattern (default: *.fun)")
    ap.add_argument("--extract-only", action="store_true",
                    help="only unpack archives; do not convert entries")
    args = ap.parse_args(argv)

    from .convert import convert_archive, convert_path

    if args.source.is_file() and args.source.suffix.lower() == ".fun":
        archives = [args.source]
    elif args.source.is_dir():
        archives = sorted(p for p in args.source.rglob(args.pattern)
                          if p.is_file())
    else:
        print(f"error: not a .fun file or directory: {args.source}",
              file=sys.stderr)
        return 2
    if not archives:
        print(f"error: no archives matching {args.pattern!r} in {args.source}",
              file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    failed = 0
    for archive in archives:
        dest = args.out / archive.stem
        try:
            if args.extract_only:
                outputs = convert_path(archive, dest)
                summary: dict[str, Any] = {"outputs": len(outputs)}
            else:
                outputs = convert_archive(archive, dest)
                manifest_path = dest / "conversion-manifest.json"
                report = json.loads(manifest_path.read_text(encoding="utf-8"))
                summary = {
                    "entries": report["entries"],
                    "outputs": len(outputs),
                    "errors": len(report["errors"]),
                    "unconverted": len(report["unconverted"]),
                }
        except (OSError, ValueError, KeyError) as exc:
            failed += 1
            summary = {"error": str(exc)}
            print(f"error: {archive.name}: {exc}", file=sys.stderr)
        results.append({"archive": str(archive), "output": str(dest),
                        **summary})
        print(f"{archive.name}: {summary}")

    _print_json({
        "archives": len(archives), "failed": failed, "results": results,
    })
    return 1 if failed else 0


def _repack_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": manifest["kind"],
        "files": manifest["files"],
        "bytes_before": manifest["bytes_before"],
        "bytes_after": manifest.get("bytes_after",
                                    manifest.get("stored_bytes_after_repack")),
        "saved": manifest.get("saved"),
        "archive_bytes": manifest.get("archive_bytes"),
        "ratio": manifest.get("ratio"),
    }


def main_repack(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="razor-repack",
        description="Losslessly repack a game build (.fun entries recompressed)")
    ap.add_argument("source", type=Path, help="original build directory")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="output directory (must be outside source)")
    ap.add_argument("--level", type=int, default=9,
                    help="zlib level for .fun entries (default: 9)")
    args = ap.parse_args(argv)
    from .repack import repack_build
    try:
        manifest = repack_build(args.source, args.out, level=args.level)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(_repack_summary(manifest))
    print(f"manifest: {args.out / 'repack-manifest.json'}")
    return 0


def main_bundle(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="razor-bundle",
        description="Create the smallest lossless .tar.xz of a game build")
    ap.add_argument("source", type=Path, help="original build directory")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="output .tar.xz file")
    ap.add_argument("--level", type=int, default=9,
                    help="zlib level for .fun entries (default: 9)")
    ap.add_argument("--xz-preset", type=int, default=None,
                    help="XZ preset when using the default filters (default: 9e)")
    ap.add_argument("--no-delta", action="store_true",
                    help="disable the PCM-tuned delta filter")
    args = ap.parse_args(argv)
    from .repack import DEFAULT_XZ_FILTERS, bundle_build
    filters = None if args.no_delta else DEFAULT_XZ_FILTERS
    try:
        manifest = bundle_build(args.source, args.out, level=args.level,
                                preset=args.xz_preset, filters=filters)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(_repack_summary(manifest))
    print(f"manifest: {args.out.with_suffix('.json')}")
    return 0


def main_verify(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="razor-verify",
        description="Verify a repacked build against its manifest")
    ap.add_argument("root", type=Path, help="extracted or repacked build directory")
    ap.add_argument("manifest", type=Path, help="repack-manifest.json")
    ap.add_argument("--original", action="store_true",
                    help="verify against the original build hashes instead")
    args = ap.parse_args(argv)
    from .repack import verify_build
    try:
        result = verify_build(args.root, args.manifest, original=args.original)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0 if result["ok"] else 1


def main_ultra_pack(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="razor-ultra-pack",
        description="Pack a build into a small restorable bundle (lossy)")
    ap.add_argument("source", type=Path, help="original build directory")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="output .tar.xz bundle")
    ap.add_argument("--opus-bitrate", type=int, default=64,
                    help="Opus bitrate in kbps (default: 64)")
    ap.add_argument("--webp-quality", type=int, default=80,
                    help="WebP quality 0..100 (default: 80)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                    help="parallel workers (default: CPU count)")
    args = ap.parse_args(argv)
    from .ultra import pack_ultra
    def progress(done: int, total: int, name: str) -> None:
        if done % 25 == 0 or done == total:
            print(f"[{done}/{total}] {name}", file=sys.stderr, flush=True)
    try:
        manifest = pack_ultra(args.source, args.out,
                              opus_bitrate=args.opus_bitrate,
                              webp_quality=args.webp_quality,
                              jobs=args.jobs, progress=progress)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json({
        "kind": manifest["kind"], "files": manifest["files"],
        "bytes_before": manifest["bytes_before"],
        "stored_bytes_after_transform":
            manifest["stored_bytes_after_transform"],
        "archive_bytes": manifest["archive_bytes"],
        "ratio": manifest["ratio"],
    })
    print(f"manifest: {args.out.with_suffix('.json')}")
    return 0


def main_ultra_restore(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="razor-ultra-restore",
        description="Regenerate a playable build from an ultra bundle")
    ap.add_argument("bundle", type=Path, help=".tar.xz bundle")
    ap.add_argument("-o", "--out", type=Path, required=True,
                    help="destination build directory")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                    help="parallel workers (default: CPU count)")
    args = ap.parse_args(argv)
    from .ultra import restore_ultra
    def progress(done: int, total: int, name: str) -> None:
        if done % 25 == 0 or done == total:
            print(f"[{done}/{total}] {name}", file=sys.stderr, flush=True)
    try:
        result = restore_ultra(args.bundle, args.out, jobs=args.jobs,
                               progress=progress)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0


def main_ultra_verify(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="razor-ultra-verify",
        description="Structurally verify a restored ultra build")
    ap.add_argument("original", type=Path, help="original build directory")
    ap.add_argument("restored", type=Path, help="restored build directory")
    ap.add_argument("manifest", type=Path, help="bundle .json manifest")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4,
                    help="parallel workers (default: CPU count)")
    args = ap.parse_args(argv)
    from .ultra import verify_ultra
    def progress(done: int, total: int, name: str) -> None:
        if done % 25 == 0 or done == total:
            print(f"[{done}/{total}] {name}", file=sys.stderr, flush=True)
    try:
        result = verify_ultra(args.original, args.restored, args.manifest,
                              jobs=args.jobs, progress=progress)
    except (OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _print_json(result)
    return 0 if result["ok"] else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="razorformats",
                                  description="FUN Labs format tools")
    commands = ap.add_subparsers(dest="command", required=True)
    commands.add_parser("extract", add_help=False)
    commands.add_parser("inspect", add_help=False)
    commands.add_parser("convert", add_help=False)
    commands.add_parser("convert-all", add_help=False)
    commands.add_parser("repack", add_help=False)
    commands.add_parser("bundle", add_help=False)
    commands.add_parser("verify", add_help=False)
    commands.add_parser("ultra-pack", add_help=False)
    commands.add_parser("ultra-restore", add_help=False)
    commands.add_parser("ultra-verify", add_help=False)
    args, rest = ap.parse_known_args(argv)
    if args.command == "extract":
        return main_extract(rest)
    if args.command == "inspect":
        return main_inspect(rest)
    if args.command == "convert":
        return main_convert(rest)
    if args.command == "convert-all":
        return main_batch(rest)
    if args.command == "repack":
        return main_repack(rest)
    if args.command == "bundle":
        return main_bundle(rest)
    if args.command == "verify":
        return main_verify(rest)
    if args.command == "ultra-pack":
        return main_ultra_pack(rest)
    if args.command == "ultra-restore":
        return main_ultra_restore(rest)
    if args.command == "ultra-verify":
        return main_ultra_verify(rest)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
