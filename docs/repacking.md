# Lossless repacking of a game build

The game reads files by path and expects the original bytes. This document
describes the two safe ways to reduce size without changing executable code or
asset contents.

## What is changed

`repack` and `bundle` copy the whole build tree and rebuild each `.fun` archive
with zlib level 9. For every archive entry the following are preserved and
checked:

* entry name, order and compression method,
* obfuscation seed, flag, DOS date/time, CRC and zlib `extra` header,
* uncompressed size,
* **SHA-256 of the decompressed bytes**.

All non-`.fun` files (`Razor.exe`, `Launcher.exe`, loose `.wav`/`.avi`, config,
help files, saves, …) are copied byte-for-byte and hashed before and after the
copy. No executable instruction is modified.

The resulting runnable folder is therefore binary-equivalent from the game's
point of view: it sees the same files with the same decompressed contents. The
`.fun` container bytes on disk differ only because the DEFLATE streams were
re-encoded more tightly.

## What is deliberately not changed

The big loose WAV files could be made much smaller by converting them to
MP3/OGG/ADPCM, but the game does not request those formats and the decoded
samples would no longer be bit-identical. Deleting help files, logos, saves or
debug logs is also not done: it may not error immediately, but it is not a
byte-preserving transformation. Those kinds of changes are out of scope for a
"runs correctly, no error" build.

## Runnable repacked folder

```bash
python -m razorformats.cli repack "/path/to/SFRU12" -o SFRU12-repacked/
python -m razorformats.cli verify SFRU12-repacked/ \
    SFRU12-repacked/repack-manifest.json
```

Launch the game from `SFRU12-repacked/`. The output contains an extra
`repack-manifest.json`; that file is not read by the game. The destination must
be outside the source tree.

For reference, the supplied 485 MB build contains 12 `.fun` archives whose
entries are already near zlib's best ratio, so this mode saves only about
0.5 MB. Its purpose is to be safe, not spectacular.

If the destination is on NTFS, Windows transparent compression can reduce the
size on disk further without changing any bytes the game reads:

```bat
compact /C /S:"C:\path\to\SFRU12-repacked"
```

## Smallest lossless bundle

For storage or transfer, use the bundle mode. It applies the same lossless
`.fun` repack, then compresses the whole tree with a standard XZ stream using a
4-byte delta filter followed by LZMA2 (`preset 9e`, `lc=3 lp=1 pb=2`). The
delta distance matches one 16-bit stereo PCM frame, which is where most of the
build's bytes live.

```bash
python -m razorformats.cli bundle "/path/to/SFRU12" -o SFRU12.tar.xz
```

This writes:

* `SFRU12.tar.xz` — a normal tar archive that any standard tar/xz can extract,
* `SFRU12.json` — manifest with archive size, ratio and per-file hashes.

The XZ stream also embeds `repack-manifest.json`. Extract it before playing:

```bash
tar -xf SFRU12.tar.xz -C SFRU12/
python -m razorformats.cli verify SFRU12/ SFRU12.json
```

`--no-delta` disables the PCM filter (slower compression ratio, slightly more
compatible with very old xz decoders). `--xz-preset N` overrides the LZMA
preset when the delta filter is disabled.

## Editable-install equivalents

After `python -m pip install -e .`:

```bash
razor-repack /path/to/SFRU12 -o SFRU12-repacked/
razor-bundle /path/to/SFRU12 -o SFRU12.tar.xz
razor-verify SFRU12-repacked/ SFRU12-repacked/repack-manifest.json
```

## Ultra bundle: under 100 MB with a restore step

The lossless modes cannot get below ~216 MB: the PCM WAV data alone is larger
than the target. The `ultra-*` commands use lossy intermediate codecs, but
regenerate a runnable tree before launch. No executable or script is altered.

* Every WAV (loose or inside a `.fun`) is stored as Opus. All WAVs with the same
  sample rate/channel count are encoded as **one** Opus stream; the manifest
  records each original WAV header, trailer and PCM byte length. Restore slices
  the decoded PCM back into the original header/data/trailer layout.
* Every true-colour TGA (loose or inside a `.fun`) is stored as WebP. Images with
  the same dimensions/depth are concatenated into **one** WebP stream, so
  restore decodes it in one `ffmpeg` call and rebuilds the original TGA header,
  channel order, row order and trailing bytes.
* Everything else, including all `.fun` entry metadata (names, order, seeds,
  flags, dates, CRC, method, `extra`), stays verbatim. Rebuilt archives are
  structurally identical; only the decompressed audio/texture bytes differ.

The result is a normal `.tar.xz` containing grouped media streams, small payload
files and `ultra-manifest.json`. Build it once, then restore before playing:

```bash
python -m razorformats.cli ultra-pack "/path/to/SFRU12" \
    -o SFRU12-ultra.tar.xz --jobs 4
python -m razorformats.cli ultra-restore SFRU12-ultra.tar.xz \
    -o SFRU12 --jobs 4
python -m razorformats.cli ultra-verify "/path/to/SFRU12" SFRU12/ \
    SFRU12-ultra.tar.json --jobs 4
```

`ffmpeg` must be installed for both steps (`libopus` and `libwebp` encoders).
`--jobs` defaults to the CPU count. On the supplied 507,517,775-byte build the
bundle is **88,976,204 bytes (17.5%)**, and restore takes about a minute on four
cores. `razor-ultra-pack`, `razor-ultra-restore` and `razor-ultra-verify` are the
short command equivalents after an editable install.

`ultra-verify` checks raw files byte-for-byte and checks audio/textures by
header/structure (dimensions, depth, sample rate, channel count, data length),
since the payload bytes are intentionally lossy.
