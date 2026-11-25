"""Smoke tests for the dependency-free core helpers."""

import struct
import sys
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from razorformats.gltf import AnimTrack, GLTFBuilder  # noqa: E402
from razorformats.tga import read_tga, tga_to_png  # noqa: E402


class CoreTests(unittest.TestCase):
    def test_tga_uncompressed_24bit(self):
        width = height = 2
        header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                             width, height, 24, 0)
        # TGA stores rows bottom-to-top, pixels in BGR order.
        pixels = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 255, 255, 255])
        img = read_tga(header + pixels)
        self.assertEqual((img.width, img.height, img.pixel_depth), (2, 2, 24))
        self.assertEqual(img.rgba[:4], bytes([255, 0, 0, 255]))  # red
        png = tga_to_png(header + pixels)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(zlib.decompress(png[41:-12])[:5],
                         b"\x00\xff\x00\x00\xff")

    def test_gltf_builder(self):
        b = GLTFBuilder("test")
        mesh = b.add_mesh("tri", [[0, 0, 0], [1, 0, 0], [0, 1, 0]], [0, 1, 2],
                          uvs=[[0, 0], [1, 0], [0, 1]])
        node = b.add_node("tri", mesh)
        b.add_default_scene([node])
        b.add_animation("idle", [AnimTrack(node, "translation", [0, 1],
                                           [[0, 0, 0], [0, 0, 1]])])
        doc = b.to_dict()
        self.assertEqual(doc["asset"]["version"], "2.0")
        self.assertEqual(len(doc["meshes"][0]["primitives"][0]["attributes"]), 2)
        self.assertEqual(len(doc["animations"]), 1)
        self.assertGreater(doc["buffers"][0]["byteLength"], 0)


if __name__ == "__main__":
    unittest.main()
