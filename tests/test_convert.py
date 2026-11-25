"""End-to-end tests for the BiN -> glTF/OBJ/JSON converter."""

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from razorformats.anim import read_anim  # noqa: E402
from razorformats.convert import convert_obj, sniff_format  # noqa: E402
from razorformats.gltf import COMPONENT_TYPES, TYPE_SIZES  # noqa: E402


def string(value: str) -> bytes:
    raw = value.encode("ascii")
    return bytes([len(raw)]) + raw


def minimal_obj() -> bytes:
    node = (string("") + string("Mesh") + struct.pack("<H", 1)
            + struct.pack("<3f", 0, 0, 0) + struct.pack("<4f", 0, 0, 0, 1))
    vertices = b"".join(struct.pack("<8f", *v) for v in [
        (0, 0, 0, 0, 0, 1, 0, 0),
        (1, 0, 0, 0, 0, 1, 1, 0),
        (0, 1, 0, 0, 0, 1, 0, 1),
    ])
    mesh = (string("Mesh") + struct.pack("<H", 1) + string("TEST")
            + struct.pack("<H", 3) + vertices
            + struct.pack("<H", 1) + struct.pack("<3H", 0, 1, 2))
    return b"BiN\x00" + struct.pack("<H", 1) + node + mesh


def minimal_anim() -> bytes:
    keys = b"".join(struct.pack("<I3f", t, 0, 0, t / 1000.0)
                    for t in (0, 33, 66))
    track = (string("AnimCtrl_KeyFrame") + string("Pos") + string("Mesh")
             + struct.pack("<H", 3) + keys)
    return b"BiN\x00" + struct.pack("<H", 1) + track


def texture_anim_bytes() -> bytes:
    keys = b"".join(struct.pack("<I4f", t, 0.0, 0.5, 1.0, float(t))
                    for t in (0, 33))
    return (b"BiN\x00" + struct.pack("<H", 1)
            + string("AnimCtrl_Texture") + string("LOD_1")
            + struct.pack("<HH", 2, 2) + struct.pack("<HH", 4, 5) + keys)


class ConvertTests(unittest.TestCase):
    def test_sniff_mangled_extensions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            anim = root / "run_back.anim_"
            anim.write_bytes(minimal_anim())
            self.assertEqual(sniff_format(anim), "anim")
            text = root / "__ammo._obj"
            text.write_text(" 1\n World Box01 1 0 0 0\n")
            self.assertEqual(sniff_format(text), "_obj")

    def test_texture_animation_is_parsed_and_noted(self):
        anim = read_anim(texture_anim_bytes())["tracks"][0]
        self.assertEqual(anim["controller"], "AnimCtrl_Texture")
        self.assertEqual(anim["type"], "LOD_1")
        self.assertEqual(anim["channels"], [4, 5])
        self.assertEqual([k["time"] for k in anim["keyframes"]], [0, 33])
        self.assertEqual(len(anim["keyframes"][0]["values"]), 4)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            obj_path = root / "Model.obj"
            anim_path = root / "BLINK.anim"
            obj_path.write_bytes(minimal_obj())
            anim_path.write_bytes(texture_anim_bytes())
            convert_obj(obj_path, root / "out", animation_paths=[anim_path])
            doc = json.loads((root / "out" / "Model.json").read_text())
            note = doc["conversion"]["animations"][0]["notes"][0]
            self.assertEqual(note["controller"], "AnimCtrl_Texture")
            self.assertEqual(note["keys"], 2)

    def test_convert_obj_with_animation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            obj_path = root / "Model.obj"
            anim_path = root / "IDLE.anim"
            obj_path.write_bytes(minimal_obj())
            anim_path.write_bytes(minimal_anim())
            outputs = convert_obj(obj_path, root / "out",
                                  animation_paths=[anim_path])
            for path in outputs:
                self.assertTrue(path.is_file(), path)

            doc = json.loads((root / "out" / "Model.gltf").read_text())
            self.assertEqual(doc["asset"]["version"], "2.0")
            self.assertEqual(len(doc["meshes"]), 1)
            self.assertEqual(len(doc["animations"]), 1)
            attrs = doc["meshes"][0]["primitives"][0]["attributes"]
            self.assertEqual(set(attrs), {"POSITION", "NORMAL", "TEXCOORD_0"})

            buffer = (root / "out" / "model.bin").read_bytes()
            for accessor in doc["accessors"]:
                view = doc["bufferViews"][accessor["bufferView"]]
                component_name = {v: k for k, v in COMPONENT_TYPES.items()}[
                    accessor["componentType"]]
                component_size = {"float": 4, "uchar": 1, "ushort": 2,
                                  "short": 2, "uint": 4}[component_name]
                size = (accessor["count"] * TYPE_SIZES[accessor["type"]]
                        * component_size)
                self.assertLessEqual(view["byteOffset"] + view["byteLength"],
                                     len(buffer))
                self.assertGreaterEqual(view["byteLength"], size)

            wavefront = (root / "out" / "Model.obj").read_text()
            self.assertIn("v 0.000000", wavefront)
            self.assertIn("f 1/1/1", wavefront)
            dump = json.loads((root / "out" / "Model.json").read_text())
            self.assertEqual(dump["format"], "bin-obj")
            self.assertEqual(dump["conversion"]["animations"][0]["channels"], 1)
            sampler = doc["animations"][0]["samplers"][0]
            self.assertAlmostEqual(
                doc["accessors"][sampler["input"]]["max"][0], 0.066, places=5)


if __name__ == "__main__":
    unittest.main()
