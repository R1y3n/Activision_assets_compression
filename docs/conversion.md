# Conversion to open formats

The `razor-convert` CLI converts one file (or a directory tree) at a time.
Conversions are intentionally lossless where possible; structured binary data
is additionally dumped as JSON so that no information is hidden in a
proprietary container.

| Input | Open output | Notes |
|---|---|---|
| `.fun` | full directory of open formats + `conversion-manifest.json` | unpacks to a temporary directory, converts every entry, links meshes/animations/textures from `.model` and shader scripts |
| `.obj` (BiN) | glTF 2.0 (`.gltf`/`.bin`), Wavefront `.obj`, JSON | positions, indices, UVs, normals, node hierarchy, materials, skins |
| `.anim` (BiN) | glTF animation channels + `animation.json` | keyframe times/values mapped to glTF samplers (ms → s) |
| `.model`, `.script`, `.fx` | `.json` | parsed call/parameter trees |
| `.fnt` | `.json` metrics (+ PNG atlas when a matching TGA exists) | bitmap fonts |
| `.bsp` | `.json` (sections) | level geometry/section tables |
| `.tga` | `.png` | pure-Python TGA decoder/PNG encoder |
| `.wav` | `.wav` + `.json` header | WAV is already an open format; header is documented |
| `.lua`, `.txt`, `.ini`, `.cfg`, `.log` | unchanged text | copied verbatim |

## Whole-archive mode

Passing a `.fun` file directly to `razor-convert` runs the full pipeline:

```bash
PYTHONPATH=src python -m razorformats.cli convert objects.fun -o open/objects/
```

The archive is unpacked into a temporary directory, every entry is sniffed by
content (including `.anim_`, `._tga_` and other mangled extensions), and the
results are written to:

```text
open/objects/
  conversion-manifest.json
  models/.../Model/{Model.gltf,model.bin,Model.obj,Model.mtl,Model.json}
  textures/.../<name>.png
  animations/.../<name>.json
  scripts/.../<name>.json
  fonts/.../<name>.json
  levels/.../<name>.json
  audio/.../<name>.wav(+.json)
  other/...                 # text and unclassified files
```

`.model` scripts supply the mesh→animation relationships and shader `.script`
files supply the mesh→texture relationships, so no manual flags are needed.
To process the whole game archive folder in one step:

```bash
PYTHONPATH=src python -m razorformats.cli convert-all "/path/to/fun-folder" -o open/game/
```

This runs the same pipeline for every `.fun` file and writes each archive into
its own subdirectory. It prints a JSON summary with per-archive
entry/output/error counts and exits non-zero if any archive fails.
`--extract-only` performs plain extraction instead. `conversion-manifest.json`
lists every output plus `errors` and `unconverted` (unrecognised binaries, e.g.
the bundled `ASC2BIN.EXE`, are reported rather than copied). Source paths in the
generated metadata are rewritten to archive-relative paths so they remain
meaningful after the temporary extraction directory is removed.

Only three example models are converted and committed under
`examples/models/` to keep the repository small; the CLI works on any of the
1,599 entries in `objects.fun` when given a legally owned copy of the game.

### Mesh conversion notes

* Source node quaternions are treated as `(x, y, z, w)` and source coordinates
  are preserved unchanged. This keeps the conversion lossless; the engine's
  exact quaternion convention is still documented as an open question.
* Skinned meshes gain `JOINTS_0`/`WEIGHTS_0`. The binary format stores one
  weight plus two bone indices per vertex; the weight applies to `bone0` and
  `1 - weight` to `bone1`. Bind-pose inverse matrices are derived from the
  embedded bone hierarchy.
* Animation tracks whose target node is not present in the mesh file become
  synthetic glTF nodes marked with `extras.synthetic = true`. This lets the
  clip play in a glTF viewer without pretending the mesh owns that skeleton.
* `PosRotVis` visibility samples cannot be represented in glTF 2.0; they are
  preserved in the accompanying JSON only.
* `AnimCtrl_Texture` (`LOD_1`) tracks animate material channels, not nodes. They
  become a concise note in the mesh JSON; all keyframes remain in the
  standalone `.anim` JSON.
* `.bsp` conversion currently emits the decoded chunk/entity/texture JSON;
  the large opaque geometry chunks are left byte-for-byte in their original
  files rather than guessed at.
