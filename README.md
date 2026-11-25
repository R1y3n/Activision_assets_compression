# razor-openformats

Open-source documentation, readers and converters for the proprietary file
formats used by **Shadow Force: Razor Unit** (FUN Labs / Activision Value) and
related titles: the `.fun` archive container, `BiN` `.obj` meshes, `BiN`
`.anim` animations, `.bsp` levels, `.model`/`.script` text scripts, `.fnt`
fonts, `.fx` effects and the standard TGA/WAV assets embedded in the archives.

Everything is implemented with the Python 3 standard library (no third-party
dependencies). Source code is MIT licensed; see [NOTICE](NOTICE) for the status
of game-derived example assets.

## Why

The game stores its data in a family of undocumented formats. This project
turns that knowledge into:

* format specifications written down in [`docs/`](docs/),
* reusable pure-Python readers in [`src/razorformats/`](src/razorformats/),
* a CLI that can list/extract archives, inspect files and convert models to
  open formats (glTF 2.0, PNG, JSON, Wavefront OBJ).

## Quick start

The package uses a `src/` layout. From the repository root, either export the
source path once:

```bash
export PYTHONPATH=src
```

or install the package (`python -m pip install -e .`) and use the short commands
(`razor-extract`, `razor-inspect`, `razor-convert`, `razor-convert-all`,
`razor-repack`, `razor-bundle`, `razor-verify`, `razor-ultra-pack`,
`razor-ultra-restore`, `razor-ultra-verify`) instead of
`python -m razorformats.cli`.

### One command: archive in, open formats out

```bash
python -m razorformats.cli convert "/path/to/objects.fun" -o open/objects/
```

That single command unpacks the archive to a temporary directory and converts
every entry: meshes become glTF 2.0 + OBJ + JSON, animations become glTF
channels + JSON, textures become PNG, scripts/levels/fonts/audio become JSON or
open files, and `conversion-manifest.json` records every output and any errors.
Mesh↔animation links are recovered from the archive's `.model` files and
mesh↔texture links from its shader `.script` files. The proprietary originals
are not copied into the output tree. Use `--extract-only` if you want just the
unpacked files.

Verified end-to-end on all 12 shipped `.fun` archives (3,402 entries total):
0 conversion errors. Three opaque binary leftovers (`.dockstate`,
`ASC2BIN.EXE`, `texture.dat`) are reported under `unconverted` in the manifest
rather than copied into the open-format tree.

### Every `.fun` archive at once

```bash
python -m razorformats.cli convert-all "/path/to/fun-folder" -o open/game/
```

Each archive is unpacked and converted with the full pipeline into its own
subdirectory (`open/game/objects/`, `open/game/textures/`, …). The command
prints a per-archive summary and returns non-zero if any archive fails. With
an editable install the short form is `razor-convert-all <folder> -o <out>`.

### Shrinking a runnable build / making a compressed download

```bash
# A folder the game can run from directly (lossless, byte-identical assets)
python -m razorformats.cli repack "/path/to/SFRU12" -o SFRU12-repacked/

# A single lossless .tar.xz for storage or transfer (extract before playing)
python -m razorformats.cli bundle "/path/to/SFRU12" -o SFRU12.tar.xz

# Verify an extracted bundle or repacked folder
python -m razorformats.cli verify SFRU12-repacked/ SFRU12-repacked/repack-manifest.json
```

See [`docs/repacking.md`](docs/repacking.md) for exactly what is and is not
changed. Short commands after `pip install -e .`: `razor-repack`,
`razor-bundle` and `razor-verify`.

### Getting under 100 MB (lossy bundle + restore step)

```bash
# Build the compact bundle (needs ffmpeg on PATH; one-time, slow step)
python -m razorformats.cli ultra-pack "/path/to/SFRU12" \
    -o SFRU12-ultra.tar.xz --jobs 4

# Regenerate a playable SFRU12 folder before launching the game
python -m razorformats.cli ultra-restore SFRU12-ultra.tar.xz \
    -o SFRU12 --jobs 4

# Structural check of the restored tree (needs the original for comparison)
python -m razorformats.cli ultra-verify "/path/to/SFRU12" SFRU12/ \
    SFRU12-ultra.tar.json --jobs 4
```

Audio is stored as Opus and embedded textures as WebP, grouped so restore only
starts a few dozen `ffmpeg` processes for the entire build. The full supplied
build packs to **~89 MB** (from 507 MB) and restores in about a minute on four
cores. Restored audio/textures are not byte-identical; every other byte and
all `.fun` entry metadata are preserved. Short commands: `razor-ultra-pack`,
`razor-ultra-restore`, `razor-ultra-verify`.

### Inspecting and converting individual files

```bash
# list archive entries
python -m razorformats.cli extract --list "/path/to/objects.fun"

# extract selected entries
python -m razorformats.cli extract "/path/to/objects.fun" -o extracted \
    --filter 'Weapons/Knife/'

# inspect a proprietary file
python -m razorformats.cli inspect extracted/Weapons/Knife/ST/Model.obj

# convert a mesh (+ texture and animations) to glTF 2.0, OBJ and JSON
python -m razorformats.cli convert extracted/Weapons/Knife/RD/Model.obj \
    -o examples/models/knife/ \
    --texture extracted/Weapons/Knife/knife_256.tga \
    --anim extracted/Weapons/Knife/RD/FIRE1.anim
```

See [`docs/00-index.md`](docs/00-index.md) for the complete format list,
[`docs/conversion.md`](docs/conversion.md) for conversion details and
[`examples/models/`](examples/models/) for three converted example models
(knife, grenade launcher and frag grenade).

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

(The `PYTHONPATH=src` export above also covers the tests.)

## Status

The implementation is research-grade: formats are reverse-engineered from the
files in the supplied archives, so unknown fields are documented rather than
guessed. See each format document for confidence levels and open questions.
