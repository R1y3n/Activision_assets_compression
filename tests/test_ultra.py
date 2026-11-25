"""Tests for the lossy restorable ultra bundle."""

import json
import math
import shutil
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from razorformats.fun import FunArchive, create  # noqa: E402
from razorformats.ultra import pack_ultra, restore_ultra  # noqa: E402
from razorformats.wav import read_wav_header  # noqa: E402


def make_wav(path: Path) -> bytes:
    rate = 8000
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        frames = bytearray()
        for i in range(rate):
            value = int(12000 * math.sin(2 * math.pi * 440 * i / rate))
            frames.extend(struct.pack("<h", value))
        wav.writeframes(frames)
    return path.read_bytes()


def make_tga(path: Path) -> bytes:
    width = height = 8
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0,
                         width, height, 24, 0x20)
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend((x * 32, y * 32, 128))
    data = header + bytes(pixels)
    path.write_bytes(data)
    return data


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
class UltraTests(unittest.TestCase):
    def test_pack_and_restore(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src"
            (src / "Data").mkdir(parents=True)
            wav = make_wav(src / "Data" / "tone.wav")
            tga = make_tga(src / "Data" / "image.tga")
            create(src / "Data" / "assets.fun", {
                "tone.wav": wav, "image.tga": tga,
            })
            (src / "Bin").mkdir()
            (src / "Bin" / "game.exe").write_bytes(b"MZ\x90\x00" + b"x" * 1000)

            bundle = root / "build.tar.xz"
            manifest = pack_ultra(src, bundle)
            self.assertEqual(manifest["files"], 4)
            self.assertEqual(bundle.stat().st_size, manifest["archive_bytes"])

            out = root / "restored"
            result = restore_ultra(bundle, out)
            self.assertEqual(result["restored"], 4)
            self.assertTrue(result["lossy"])

            restored_wav = (out / "Data" / "tone.wav").read_bytes()
            info = read_wav_header(restored_wav)
            self.assertEqual(info["fmt"]["sample_rate"], 8000)
            self.assertEqual(info["fmt"]["channels"], 1)
            self.assertEqual(info["fmt"]["bits_per_sample"], 16)
            self.assertEqual(next(c["size"] for c in info["chunks"]
                                  if c["id"] == "data"),
                             len(wav) - 44)

            restored_tga = (out / "Data" / "image.tga").read_bytes()
            self.assertEqual(struct.unpack_from("<HH", restored_tga, 12), (8, 8))
            self.assertEqual(restored_tga[16], 24)

            with FunArchive(out / "Data" / "assets.fun") as fun:
                entries = fun.entries
                self.assertEqual([e.name for e in entries],
                                 ["tone.wav", "image.tga"])
                embedded_wav = fun.read_file(entries[0])
                embedded_tga = fun.read_file(entries[1])
            self.assertEqual(read_wav_header(embedded_wav)["fmt"]["sample_rate"],
                             8000)
            self.assertEqual(struct.unpack_from("<HH", embedded_tga, 12), (8, 8))

            self.assertEqual((out / "Bin" / "game.exe").read_bytes(),
                             (src / "Bin" / "game.exe").read_bytes())


if __name__ == "__main__":
    unittest.main()
