"""Tests for lossless build repacking and bundling."""

import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from razorformats.fun import FunArchive, create  # noqa: E402
from razorformats.repack import (  # noqa: E402
    bundle_build, bundle_manifest, repack_build, verify_build,
)


def make_source(root: Path) -> dict[str, bytes]:
    payloads = {
        "a.txt": b"hello world\n" * 50,
        "sub/b.bin": bytes(range(256)) * 8,
        "empty.dat": b"",
    }
    create(root / "Data" / "test.fun", payloads, seed=(0x89, 0x1B))
    (root / "Data" / "readme.txt").write_bytes(b"plain file\n" * 20)
    (root / "Bin").mkdir(exist_ok=True)
    (root / "Bin" / "game.exe").write_bytes(b"MZ\x90\x00" + b"\x00" * 500)
    return payloads


class RepackTests(unittest.TestCase):
    def test_repack_build_is_lossless_and_verifiable(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            payloads = make_source(src)
            out = root / "out"

            manifest = repack_build(src, out)
            self.assertEqual(manifest["files"], 3)
            self.assertEqual(manifest["bytes_before"],
                             sum(r["bytes_before"] for r in manifest["records"]))

            result = verify_build(out, Path(out / "repack-manifest.json"))
            self.assertTrue(result["ok"], result["mismatches"])
            original = verify_build(src, Path(out / "repack-manifest.json"),
                                    original=True)
            self.assertTrue(original["ok"], original["mismatches"])

            self.assertEqual((out / "Data" / "readme.txt").read_bytes(),
                             (src / "Data" / "readme.txt").read_bytes())
            with FunArchive(src / "Data" / "test.fun") as before, \
                    FunArchive(out / "Data" / "test.fun") as after:
                self.assertEqual([e.seed for e in before.entries],
                                 [e.seed for e in after.entries])
                self.assertEqual([before.read_file(e) for e in before.entries],
                                 [after.read_file(e) for e in after.entries])
                self.assertEqual(
                    sorted(before.read_file(e) for e in before.entries),
                    sorted(payloads.values()))

    def test_bundle_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            src.mkdir()
            make_source(src)
            archive = root / "build.tar.xz"

            manifest = bundle_build(src, archive)
            self.assertEqual(manifest["archive_bytes"], archive.stat().st_size)
            embedded = bundle_manifest(archive)
            self.assertEqual(embedded["files"], manifest["files"])

            extracted = root / "extracted"
            with tarfile.open(archive, "r:xz") as tar:
                try:
                    tar.extractall(extracted, filter="data")
                except TypeError:  # Python < 3.12
                    tar.extractall(extracted)
            result = verify_build(extracted, archive.with_suffix(".json"))
            self.assertTrue(result["ok"], result["mismatches"])


if __name__ == "__main__":
    unittest.main()
