"""Smoke test for the BSP chunk-chain reader."""

import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from razorformats.bsp import inspect_bsp  # noqa: E402


class BSPTests(unittest.TestCase):
    def test_chunk_chain(self):
        header = bytearray(92)
        struct.pack_into("<III", header, 0, 15, 92, 5)
        header[0x28:0x2D] = b"TEST\x00"
        data = bytes(header) + struct.pack("<II", 20, 4) + b"\xff\xff\x00\x00"
        result = inspect_bsp(data, "test.bsp")
        self.assertEqual(result["header"]["author_tag"], "TEST")
        self.assertEqual(len(result["chunks"]), 1)
        self.assertEqual(result["chunks"][0]["type"], 20)


if __name__ == "__main__":
    unittest.main()
