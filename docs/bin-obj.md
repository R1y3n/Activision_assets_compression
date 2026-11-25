# BiN `.obj` format — Shadow Force: Razor Unit

Status: **decoded**. All 14 `.obj` files in `cutscenes/cutscenes/Outro/`
parse to their exact EOF with the layout below. Confidence is marked per field.

All integers are **little-endian**; floats are **IEEE-754 binary32**.

## Container

| Offset | Size | Field | Confidence |
|---|---|---|---|
| `0x00` | 4 | magic `42 69 4e 00` = `"BiN\0"` | high |
| `0x04` | u16 | number of top-level scene nodes | high |

The `af 00` (175) commonly seen at `0x04` is a **count**, not part of a fixed
header: `DummyCam2.obj` has `02 00`, `GI2.obj` has `27 00` (39).

The companion converter `ASC2BIN.EXE` contains the strings `.obj`, `.anim`,
`.cam`, `BiN`, `wb`, `Invalid mesh type!` and `File %s is already in binary
format!`, confirming this is FUN Labs' ASCII→BiN output.

### String primitive

`u8 byteLength` followed by exactly that many raw ASCII/Latin-1 bytes, no NUL
terminator. Used for node names, parent names, shader names and mesh names.

## Scene-graph node record (`node`)

Repeated `nodeCount` times at the start of the file, and again as skeleton
bones inside skinned meshes.

| Rel. offset | Size | Field | Confidence |
|---|---|---|---|
| `+0` | string | parent node name (often `"World"`) | high |
| `+1+n` | string | node name | high |
| `+2+n+m` | u16 | flags / node-kind bits (see below) | high (field), low (meaning) |
| `+4+n+m` | f32[3] | translation `(x,y,z)` | high |
| `+16+n+m` | f32[4] | rotation quaternion | high (field), medium (component order) |
| `+32+n+m` | f32[3] | **optional** extra, only when flags ∈ {3,4} | medium-high |

Base size is 28 bytes plus the two strings; 40 bytes with the extra.
Quaternion norms of parsed nodes are ≈1.0, validating the field boundaries.

Observed flag values across the sample set: `0` (399×), `1` (27×), `2` (13×),
`3` (37×), `4` (4×), `6` (2×).

- flags `3`: extra is usually `(-1,-1,-1)` (e.g. `Dummy09`, `Dummy10`, `Dummy13`,
  `ChamberExhaust`); high confidence that the 12 bytes exist, unknown semantics.
- flags `4`: extra is a 3-vector, e.g. `(-0.998,-0.920,-0.584)` for light nodes
  `Flama1`/`Flama2` in `GI1.obj`; likely light direction/colour, unconfirmed.
- flags `0`, `1`, `2`, `6`: no extra bytes. Failing to skip the extra for `3`/`4`
  shifts every subsequent parse (this is why `GI1.obj`/`GI4.obj` initially
  looked malformed).

The ASCII source `objects/Weapons/Ammo/__ammo._obj` shows the same record as
text: `World Box01 1 0.000000 0.000000 0.000000 0.000000 1.000000 -0.000000
-0.000000`, i.e. parent, name, flag, translation, quaternion — confirming the
field order.

## Mesh block

While bytes remain, read meshes sequentially:

| Rel. offset | Size | Field | Confidence |
|---|---|---|---|
| `+0` | string | mesh name (matches a scene node name) | high |
| `+1+n` | u16 | mesh type: `1` = static, `2` = skinned | high |
| `+3+n` | string | shader / material name, e.g. `M4`, `GIHelmet02Shader` | high |
| — | u16 | **if type 2:** skeleton bone count | high |
| — | `node` × count | **if type 2:** skeleton bone records | high |
| — | u16 | vertex count `V` | high |
| — | weight × `V` | **if type 2:** 8-byte skin weights (see below) | high |
| — | vertex × `V` | vertex records (32 bytes each) | high |
| — | u16 | triangle count `T` | high |
| — | u16 × `3T` | three 0-based vertex indices per triangle | high |

### Skin-weight record (type 2 meshes only)

`V` records, each **8 bytes**, immediately before the vertex block:

| Size | Field | Confidence |
|---|---|---|
| f32 | weight, observed in `[0,1]` | high |
| u16 | bone index 0 (0-based into the mesh skeleton array) | high |
| u16 | bone index 1 | high |

Verified on `GI2.obj` (886 weights, indices 0–24 for 25 bones), `cover.obj`,
`STRING.obj`, etc. The 8-byte size is forced by exact EOF agreement; no
separate weight count exists — the vertex count is reused.

### Vertex record

**32 bytes per vertex**, all f32:

| Offset | Field | Confidence |
|---|---|---|
| `+0` | position `(x,y,z)` | high |
| `+12` | normal `(x,y,z)` | high (layout), low (normalisation) |
| `+24` | texture coordinates `(u,v)` | high |

The ASCII source `__ammo._obj` writes each vertex as eight decimal floats in
exactly this order. UV values are usually in `[0,1]`. **Note:** many cutscene
meshes store non-normalised normals (e.g. `GI2.obj` normals have length ≈0.22,
`M4_MP` ≈0.32), so consumers should normalise if a unit normal is required;
this appears to be exported data, not a different field layout.

Face indices are 0-based and remain below the mesh's vertex count.

## Worked examples

### `DummyCam2.obj` — smallest static mesh (263 bytes = `0x107`)

```
0x000  BiN\0
0x004  02 00                 node count = 2
0x006  node 0: World / Dummy01, flags 0, identity-ish transform   -> 0x032
0x032  node 1: Dummy01 / Box01, flags 1                            -> 0x05e
0x05e  05 "Box01" 01 00 10 "GIHelmet02Shader"                     mesh header
0x077  04 00                 vertex count = 4
0x079  ...                   4 x 32-byte vertices                  -> 0x0f9
0x0f9  02 00                 triangle count = 2
0x0fb  02 00 01 00 00 00 00 00 03 00 02 00                        2 faces
0x107  EOF
```

### `GI2.obj` — skinned mesh (66,237 bytes = `0x102bd`)

```
0x004  27 00                       39 top-level nodes              -> 0x8ce
0x8ce  04 "G.I." 02 00 10 "GIHelmet02Shader"
0x8e6  19 00                       25 skeleton bones               -> 0xea0
0xea0  76 03                       886 vertices
0xea2  ...                         886 x 8-byte weights             -> 0x2a52
0x2a52 ...                         886 x 32-byte vertices           -> 0x9912
0x9912 35 04                       1077 triangles
0x9914 ...                         1077 x 3 x u16 indices           -> 0xb252
0xb252 05 "M4_MP" 01 00 02 "M4"     second (static) mesh
0xb25d 3f 02                       575 vertices                    -> 0xfa3f
0xfa3f 6a 01                       362 triangles                   -> 0x102bd
```

## Unknowns / low-confidence items

- Meaning of node flags bits (0/1/2/6) and the extra `f32[3]` for flags 3/4.
- Quaternion component order (`xyzw` vs `wxyz`) — norms validate the extent,
  not the interpretation.
- Whether normals are deliberately unnormalised in some exports.
- Mesh types other than 1 and 2 (`Invalid mesh type!` in ASC2BIN suggests
  others may exist, but none were observed here).
