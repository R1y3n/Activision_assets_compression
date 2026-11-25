"""One-command archive -> open formats pipeline test."""

import io
import json
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from razorformats.cli import main_batch  # noqa: E402
from razorformats.convert import convert_archive  # noqa: E402
from razorformats.fun import create  # noqa: E402


def s(value: str) -> bytes:
    raw = value.encode("ascii")
    return bytes([len(raw)]) + raw


def obj_bytes() -> bytes:
    node = (s("") + s("Mesh") + struct.pack("<H", 1)
            + struct.pack("<3f", 0, 0, 0) + struct.pack("<4f", 0, 0, 0, 1))
    verts = b"".join(struct.pack("<8f", *v) for v in [
        (0, 0, 0, 0, 0, 1, 0, 0), (1, 0, 0, 0, 0, 1, 1, 0),
        (0, 1, 0, 0, 0, 1, 0, 1)])
    mesh = (s("Mesh") + struct.pack("<H", 1) + s("TEST")
            + struct.pack("<H", 3) + verts
            + struct.pack("<H", 1) + struct.pack("<3H", 0, 1, 2))
    return b"BiN\x00" + struct.pack("<H", 1) + node + mesh


def anim_bytes() -> bytes:
    keys = b"".join(struct.pack("<I3f", t, 0, 0, t / 1000.0)
                    for t in (0, 33))
    return (b"BiN\x00" + struct.pack("<H", 1)
            + s("AnimCtrl_KeyFrame") + s("Pos") + s("Mesh")
            + struct.pack("<H", 2) + keys)


def tga_bytes() -> bytes:
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                         1, 1, 24, 0)
    return header + bytes([10, 20, 30])


class ArchivePipelineTests(unittest.TestCase):
    def test_archive_is_unpacked_and_converted(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            archive = root / "mini.fun"
            files = {
                "meshes/model.obj": obj_bytes(),
                "textures/test.tga": tga_bytes(),
                "shaders/test.script": (
                    b'function TEST()\r\n\tBeginStage();\r\n'
                    b'\t\tTexture("textures/test.tga");\r\n'
                    b'\tEndStage();\r\nend\r\n'),
                "anims/IDLE.anim": anim_bytes(),
                "test.model": (
                    b'model("test")\r\n'
                    b'\tmesh("meshes/model.obj")\r\n'
                    b'\tanimation("idle","anims/IDLE.anim")\r\n'),
            }
            create(archive, files)
            output = root / "open"
            convert_archive(archive, output)

            manifest = json.loads((output / "conversion-manifest.json").read_text())
            self.assertEqual(manifest["entries"], 5)
            self.assertEqual(manifest["errors"], [])

            gltf_path = output / "models/meshes/model/model.gltf"
            gltf = json.loads(gltf_path.read_text())
            self.assertEqual(len(gltf["meshes"]), 1)
            self.assertEqual(len(gltf["animations"]), 1)
            self.assertEqual(gltf["images"][0]["uri"], "textures/test.png")
            self.assertTrue((gltf_path.parent / "textures/test.png").is_file())
            self.assertTrue((output / "textures/textures/test.png").is_file())
            self.assertTrue((output / "animations/anims/IDLE.json").is_file())


class BatchTests(unittest.TestCase):
    def test_convert_all_archives(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "archives"
            src.mkdir()
            for name in ("one.fun", "two.fun"):
                create(src / name, {"meshes/model.obj": obj_bytes(),
                                    "anims/IDLE.anim": anim_bytes()})
            out = root / "open"
            with redirect_stdout(io.StringIO()):
                status = main_batch([str(src), "-o", str(out)])
            self.assertEqual(status, 0)
            for name in ("one", "two"):
                manifest = json.loads(
                    (out / name / "conversion-manifest.json").read_text())
                self.assertEqual(manifest["entries"], 2)
                self.assertEqual(manifest["errors"], [])


if __name__ == "__main__":
    unittest.main()
