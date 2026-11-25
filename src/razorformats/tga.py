"""Minimal Truevision TGA reader and pure-standard-library PNG writer.

The game archives contain thousands of 24-bit uncompressed TGAs (256x256 with
an 18-byte header).  This module also handles 32-bit and RLE-compressed true
colour images so that the converter is useful on the whole archive.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TGAImage:
    width: int
    height: int
    pixels: bytes  # top-to-bottom RGBA, 4 bytes per pixel
    image_type: int
    pixel_depth: int
    id_field: bytes = b""

    @property
    def rgba(self) -> bytes:
        return self.pixels


def _read_rle(data: bytes, offset: int, count: int, bpp: int) -> tuple[bytes, int]:
    """Decode an RLE packet stream into `count` raw pixels."""
    out = bytearray()
    stride = bpp // 8
    while len(out) < count * stride:
        if offset >= len(data):
            raise ValueError("truncated TGA RLE data")
        packet = data[offset]
        offset += 1
        run = (packet & 0x7F) + 1
        if packet & 0x80:
            pixel = data[offset:offset + stride]
            if len(pixel) != stride:
                raise ValueError("truncated TGA RLE pixel")
            offset += stride
            out.extend(pixel * run)
        else:
            raw = data[offset:offset + run * stride]
            if len(raw) != run * stride:
                raise ValueError("truncated TGA RLE pixel run")
            offset += len(raw)
            out.extend(raw)
    return bytes(out[:count * stride]), offset


def read_tga(data: bytes) -> TGAImage:
    """Parse a TGA image into top-to-bottom RGBA pixels."""
    if len(data) < 18:
        raise ValueError("TGA header is shorter than 18 bytes")
    (id_len, cmap_type, image_type, cmap_first, cmap_len, cmap_depth,
     x_origin, y_origin, width, height, depth, descriptor) = struct.unpack_from(
        "<BBBHHBHHHHBB", data, 0)

    supported = {2: (24, 32), 3: (8,), 10: (24, 32), 11: (8,)}
    if image_type not in supported or depth not in supported[image_type]:
        raise ValueError(
            f"unsupported TGA image_type={image_type} depth={depth} "
            f"(supported: {supported})")
    if cmap_type != 0:
        raise ValueError("colour-mapped TGA is not supported")

    offset = 18 + id_len
    id_field = data[18:offset]
    pixel_count = width * height
    stride = depth // 8

    if image_type in (2, 3):
        raw = data[offset:offset + pixel_count * stride]
        if len(raw) != pixel_count * stride:
            raise ValueError("truncated TGA pixel data")
    else:
        raw, offset = _read_rle(data, offset, pixel_count, depth)

    # TGA stores pixels bottom-to-top unless bit 5 of the descriptor is set.
    rows = [raw[i * stride:(i + 1) * stride] for i in range(pixel_count)]
    rows = [rows[i * width:(i + 1) * width] for i in range(height)]
    if not (descriptor & 0x20):
        rows.reverse()

    rgba = bytearray(width * height * 4)
    for y, row in enumerate(rows):
        for x, px in enumerate(row):
            dst = (y * width + x) * 4
            if depth == 8:
                gray = px[0]
                rgba[dst:dst + 4] = bytes((gray, gray, gray, 255))
            elif depth == 24:
                b, g, r = px
                rgba[dst:dst + 4] = bytes((r, g, b, 255))
            else:
                b, g, r, a = px
                rgba[dst:dst + 4] = bytes((r, g, b, a))
    return TGAImage(width, height, bytes(rgba), image_type, depth, id_field)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def write_png(image: TGAImage, level: int = 6) -> bytes:
    """Encode RGBA pixels as a PNG using only zlib and struct."""
    stride = image.width * 4
    raw = bytearray()
    for y in range(image.height):
        row = image.pixels[y * stride:(y + 1) * stride]
        raw.append(0)  # filter type 0 (None)
        raw.extend(row)
    ihdr = struct.pack(">IIBBBBB", image.width, image.height, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"IDAT", zlib.compress(bytes(raw), level))
            + _png_chunk(b"IEND", b""))


def inspect_tga(data: bytes) -> dict[str, int]:
    """Compact TGA header summary used by ``razor-inspect``."""
    image = read_tga(data)
    return {"width": image.width, "height": image.height,
            "pixel_depth": image.pixel_depth,
            "image_type": image.image_type,
            "id_length": len(image.id_field)}


def tga_to_png(data: bytes) -> bytes:
    """Convenience: decode TGA bytes and return PNG bytes."""
    return write_png(read_tga(data))


def convert_file(src: str | Path, dst: str | Path | None = None) -> Path:
    """Convert a .tga file to .png, returning the destination path."""
    src = Path(src)
    dst = Path(dst) if dst is not None else src.with_suffix(".png")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(tga_to_png(src.read_bytes()))
    return dst


if __name__ == "__main__":  # pragma: no cover
    import argparse
    ap = argparse.ArgumentParser(description="Convert TGA images to PNG")
    ap.add_argument("source")
    ap.add_argument("-o", "--output")
    args = ap.parse_args()
    print(convert_file(args.source, args.output))
