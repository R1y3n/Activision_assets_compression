"""Round-trip tests for the FUN archive reader/writer."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from razorformats.fun import FunArchive, create  # noqa: E402


class FunArchiveTests(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plain = {
                "hello.txt": b"hello world\n",
                "sub/nested.bin": bytes(range(256)) * 4,
                "empty.dat": b"",
            }
            archive = create(root / "test.fun", plain, seed=(0x89, 0x1B))
            with FunArchive(archive) as fun:
                entries = fun.read()
                self.assertEqual(len(entries), 3)
                self.assertEqual(fun.trailing_bytes, 0)
                for entry in entries:
                    self.assertEqual(entry.method, 8)
                    self.assertEqual(entry.extra, b"\x78\x9c")
                    self.assertEqual(fun.read_file(entry),
                                     plain[entry.name.replace("\\", "/")])
                out = root / "out"
                fun.extract(out)
            for name, data in plain.items():
                self.assertEqual((out / name).read_bytes(), data)

    def test_traversal_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with self.assertRaises(Exception):
                create(root / "t.fun", {"../../escape.txt": b"x"})
            archive = create(root / "t.fun", {"safe\\escape.txt": b"x"})
            with FunArchive(archive) as fun:
                written = fun.extract(root / "out")
            self.assertEqual(written[0], (root / "out" / "safe" / "escape.txt").resolve())


if __name__ == "__main__":
    unittest.main()
