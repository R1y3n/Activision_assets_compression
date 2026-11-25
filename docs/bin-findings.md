# Findings — Razor Unit `BiN` `.obj` / `.anim` reverse engineering

## Scope and method

Analysed **2–4 focused samples deeply**, then validated the resulting rules
against every matching file in the folder (read-only):

- `.obj`: `DummyCam2.obj` (smallest, static), `GI2.obj` (skinned), plus
  `GI1.obj`/`GI4.obj` (extra node-flag data) and `STRING.obj` (large).
- `.anim`: `AnimPale.anim` (smallest), `helicopter_idle.anim`,
  `GI1.anim`/`GI2.anim`, `cover.anim`, `STRING.anim`.

Tools: byte-level hexdumps, quaternion-norm and index-range validation, exact
EOF accounting, ASCII source correlation, and strings from the shipped
converter. No source files were modified.

## Core result

Both formats share a tiny container: `BiN\0` + little-endian **u16 count**,
followed by variable-length records. Strings are `u8 length + raw bytes`.
The count is data-dependent (2, 3, 4, 39, 175, …), never a fixed `af 00`.

### `.obj`

- Header count = number of scene-graph node records; each node is parent
  string, name string, u16 flags, f32 translation[3], f32 quaternion[4].
- **Flags 3 and 4 append a 12-byte f32[3] extension.** Missing this was the
  single biggest source of earlier mis-parses (`GI1.obj`, `GI4.obj`).
- Mesh blocks: name string, u16 type (1 static / 2 skinned), shader string,
  optional skeleton (`u16` bone count + node records), u16 vertex count,
  optional skin weights, vertices, u16 triangle count, u16 face indices.
- Skinned meshes store **one 8-byte weight record per vertex before the
  vertices** (`f32 weight + u16 bone0 + u16 bone1`), reusing the vertex count.
- Vertices are 32 bytes: `f32 pos[3], normal[3], uv[2]`; faces are triples of
  0-based u16 indices.
- All **14 `.obj` files** in the Outro folder parse to their exact EOF.

### `.anim`

- Header count = number of tracks. Keyframe tracks are controller string,
  sub-controller string, target node string, u16 key count, then keys.
- Key strides: `Pos` 16, `Rot` 20, `PosRot` 32, `PosRotVis` 34 (PosRot + u16
  visibility). Each key starts with a u32 monotonic tick/time.
- Light tracks (`AnimCtrl_Light` / `Firelight`) use u32 duration, u16 property
  count, then named properties with typed float keys (Color 12 B, PosRot 28 B,
  scalars 4 B).
- All **13 `.anim` files** in the Outro folder parse to their exact EOF;
  quaternion norms are 1.0 and face/bone indices are in range.

## Corroborating evidence

- `ASC2BIN.EXE` (221,252 bytes, in `objects/Weapons/M47AR/`) is FUN Labs'
  ASCII→BiN converter. Its string table contains `BiN`, `.obj`, `.anim`,
  `.cam`, `wb`, controller names (`AnimCtrl_KeyFrame`, `AnimCtrl_Light`,
  `AnimCtrl_Texture`, `AnimCtrl_LipSync`), sub-controller names (`Pos`, `Rot`,
  `PosRot`, `Vis`), and the exact light-property names (`Color`, `Intens`,
  `Falloff`, `Hotspot`, `AttenStart`, `Radius`, `NearAttenStart`,
  `NearAttenEnd`). This independently confirms nearly every token we decoded.
- ASCII sources such as `objects/Weapons/Ammo/__ammo._obj` show the same
  fields in text form (`parent name flags tx ty tz qx qy qz qw`, mesh
  type/shader/vertex count, 8-float vertices, triangle indices), confirming
  the `.obj` field order and vertex layout.
- `.model` text files provide the name mapping between meshes and animations
  (`HELI.model` → `HELI.obj` + `HELI.anim`; `HELIPTFINAL.model` →
  `Aterizare.obj` + `AnimPale.anim`, `fly.anim`; `STRING.model` →
  `STRING.obj` + `STRING.anim`).

### Malformed-extension samples

- `Models/Animations/Move/run_back.anim_` **is** a BiN animation (magic, 11
  tracks, mixed `PosRot`/`Rot`) and parses cleanly with this format.
- `Weapons/Ammo/__ammo._obj` is **ASCII**, not BiN — it is converter input,
  and is the source-format Rosetta stone mentioned above.
- `Models/ArabianFrankie/lod1.tani` is ASCII `AnimCtrl_Texture` data
  (another converter input, not this binary format).
- `Weapons/LaserDesignator/_laser_display_64._tga_` is an uncompressed TGA
  (64×64, 24 bpp), unrelated to BiN.

## Remaining unknowns

- Semantics of node flag bits (0/1/2/6) and the flag-3/4 `f32[3]` extension.
- Quaternion component order and absolute time unit / frame rate.
- Polarity of the `PosRotVis` visibility flag (`0x0000` vs `0xffff`).
- Meaning of the light `duration` u32 (equals last tick in GI1, off by 4 in GI4).
- Controllers seen in ASC2BIN but not in the samples: `AnimCtrl_Texture`,
  `AnimCtrl_LipSync`.
- Some exported meshes have non-unit normals in the binary files (ASCII
  source shows unit normals); this looks like exporter behaviour, not a
  different vertex layout — normalise on consumption if needed.

## Deliverables

- `docs/bin_obj.md` — byte-level `.obj` specification and worked examples.
- `docs/bin_anim.md` — byte-level `.anim` specification and worked examples.
- `parser.py` — stdlib-only parser CLI with JSON dump and Wavefront OBJ export.
- `FINDINGS.md` — this summary.

Usage:

```
python3 parser.py <file.obj|.anim> [--json out.json] [--obj out.obj]
```
