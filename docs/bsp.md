# Shadow Force: Razor Unit `.bsp` format (reverse-engineered)

## Scope and evidence

This document describes the `.bsp` level container used by *Shadow Force: Razor Unit* (FUN Labs). It was derived by read-only inspection of all 26 `.bsp` files under `/tmp/opencode/razor_extract/bsp/bsp/`.

Primary worked examples used throughout:

| File | Size | Header counters `0x48..0x54` | Author tag |
|---|---:|---|---|
| `warfare.bsp` | `0x63b0e0` (6,533,344) | `(73, 1130, 670, 23)` | `MISHA` |
| `citadel.bsp` | `0x75092f` (7,676,463) | `(106, 604, 1084, 29)` | `HISHU` |
| `dm_boat.bsp` | `0xf7b37f` (16,224,383) | `(106, 84, 4737, 44)` | `HISHU` |

All multi-byte integers and floats are **little-endian**. Floats are IEEE-754 `f32`.

> **Important correction to early hypotheses:** there is **no lump/offset table**. The file is a fixed 92-byte header followed by a forward-chained stream of self-sized chunks. The values previously read as header fields at `0x64`/`0x68` are actually the **type and size of the first real chunk**.

---

## 1. File header (`0x00..0x5b`)

The header is exactly 92 (`0x5c`) bytes. This is confirmed by the value `92` at `0x04` and by the fact that the first chunk header starts at `0x5c` in all 26 files.

| Offset | Size | Type | Meaning / observed value | Confidence |
|---:|---:|---|---|---|
| `0x00` | 4 | `u32` | Format/version constant: `15` in all 26 files | High |
| `0x04` | 4 | `u32` | Header size: `92` in all 26 files | High |
| `0x08` | 4 | `u32` | Sub-version constant: `5` in all 26 files | High |
| `0x0c` | 4 | `u32` | Build/editor number: `37..42` (`42` in the three examples) | Medium |
| `0x10` | 4 | `u32` | Always `1` | High |
| `0x14` | 4 | `u32` | Always `1` | High |
| `0x18` | 4 | `u32` | Always `1` | High |
| `0x1c` | 4 | `u32` | Flag: `0` or `1` (cinematic maps use `0`) | Medium |
| `0x20` | 4 | `u32` | Mode/size flag: `2`, `4`, or `8` (examples use `8`) | Medium |
| `0x24` | 4 | `u32` | Flag: `0` or `1` | Medium |
| `0x28` | 8 | `char[8]` | NUL-padded author/tag string: `MISHA`, `HISHU`, `EMIL`, `PATRICK`, `MPREDA`, `GEORGE` | High |
| `0x30` | 24 | `u32[6]` | Reserved/zero in all files | High |
| `0x48` | 4 | `u32` | Unknown counter A | Observed only |
| `0x4c` | 4 | `u32` | Unknown counter B | Observed only |
| `0x50` | 4 | `u32` | Unknown counter C | Observed only |
| `0x54` | 4 | `u32` | Unknown counter D | Observed only |
| `0x58` | 4 | `u32` | Usually `0`; `stronghold.bsp` has `2` | Observed only |

The four counters at `0x48..0x54` were tested against chunk counts, entity counts, property counts, texture counts, model counts and `model_name` indices. No single consistent mapping holds across all 26 maps, so they are recorded as opaque editor/runtime counters rather than guessed semantics.

---

## 2. Chunk framing

Starting at `0x5c`, every chunk has an 8-byte header:

```text
u32 type          # chunk type / id
u32 payload_size  # number of bytes AFTER this 8-byte header
u8  payload[payload_size]
```

The next chunk begins immediately at `payload_offset + payload_size`. There is no padding, terminator, or central directory. The last payload ends exactly at EOF in all 26 files.

### 2.1 `warfare.bsp` full chunk chain

| # | Chunk hdr | Type | Payload size | Payload range |
|---:|---:|---:|---:|---|
| 0 | `0x00005c` | 0 | `0x000000` | `0x000064..0x000064` (empty) |
| 1 | `0x000064` | 17 | `0x0108f0` | `0x00006c..0x01095c` |
| 2 | `0x01095c` | 10 | `0x00000c` | `0x010964..0x010970` |
| 3 | `0x010970` | 16 | `0x0021ac` | `0x010978..0x012b24` |
| 4 | `0x012b24` | 0 | `0x01925c` | `0x012b2c..0x02bd88` |
| 5 | `0x02bd88` | 1 | `0x00fd90` | `0x02bd90..0x03bb20` |
| 6 | `0x03bb20` | 2 | `0x017278` | `0x03bb28..0x052da0` |
| 7 | `0x052da0` | 3 | `0x1e9792` | `0x052da8..0x23c53a` |
| 8 | `0x23c53a` | 4 | `0x03147c` | `0x23c542..0x26d9be` |
| 9 | `0x26d9be` | 12 | `0x02818c` | `0x26d9c6..0x295b52` |
| 10 | `0x295b52` | 5 | `0x03fc3c` | `0x295b5a..0x2d5796` |
| 11 | `0x2d5796` | 6 | `0x00061c` | `0x2d579e..0x2d5dba` |
| 12 | `0x2d5dba` | 7 | `0x01c442` | `0x2d5dc2..0x2f2204` |
| 13 | `0x2f2204` | 8 | `0x2ae288` | `0x2f220c..0x5a0494` |
| 14 | `0x5a0494` | 11 | `0x00096c` | `0x5a049c..0x5a0e08` |
| 15 | `0x5a0e08` | 14 | `0x0875ec` | `0x5a0e10..0x6283fc` |
| 16 | `0x6283fc` | 18 | `0x0000cb` | `0x628404..0x6284cf` |
| 17 | `0x6284cf` | 20 | `0x001988` | `0x6284d7..0x629e5f` |
| 18 | `0x629e5f` | 9 | `0x011279` | `0x629e67..0x63b0e0` = EOF |

### 2.2 Cross-file confirmation

Selected rows from two other maps show the same framing and ordering:

| File | First chunks after header | Last three chunks | EOF check |
|---|---|---|---|
| `citadel.bsp` | `0:0x0`, `17:0x108f0`, `10:0xc`, `16:0x2928`, `0:0x16764` | `18`, `20`, `9:0x17687` @ `0x7392a0` | `0x7392a8 + 0x17687 = 0x75092f` = file size |
| `dm_boat.bsp` | `0:0x0`, `17:0x108f0`, `10:0xc`, `16:0x248c`, `0:0x14a04` | `18`, `20`, `9:0x8449` @ `0xf72f2e` | `0xf72f36 + 0x8449 = 0xf7b37f` = file size |

Chunk type 9 (entity text) is the final chunk in every sampled file. Chunk type 17 is always second and always has payload size `0x108f0`. Chunk type 19 is optional (present only in `stronghold.bsp` in this corpus).

---

## 3. Chunk type catalog

Confidence refers to the *layout*, not necessarily the semantic name.

| Type | Name used here | Layout / notes | Confidence |
|---:|---|---|---|
| 0 | Planes / empty | First occurrence is empty (`size=0`). Later occurrence: `u32 count`, then `count × 20-byte` records: `f32 nx,ny,nz,dist; u32 flags`. Exact size relation verified in all maps. | High |
| 1 | Bounds/brushes | `u32 count`, then `count × 36-byte` records: `u32 a,b,c; f32[6] bounds`. `(size-4) / 36 == count` in all maps. | High |
| 2 | Faces (partial) | `u32 count` followed by variable-length face-like records. Records contain `0xffff` sentinels, index pointers and `f32[6]` bounding boxes. Full schema not recovered. | Medium |
| 3 | Geometry table A | `u32 count` followed by a large variable-length binary table. Opaque. | Low |
| 4 | Geometry table B | `u32 count` followed by variable-length records with many `f32` values. Opaque. | Low |
| 5 | Geometry table C | `u32 count` followed by variable-length records. Opaque. | Low |
| 6 | Texture dictionary | `u32 count`, then `count × 40-byte` records: `char[32] name`, `u32 zero`, `u32 0xffffffff`. | High |
| 7 | Mask / raster A | No count; often starts with a long `0xff` run. Opaque. | Low |
| 8 | Raster/geometry B | Starts `u32 count`, `u32 9`, `u32 0x00010000`, then opaque variable records. | Medium |
| 9 | Entity text | `u32 text_length`, then exactly that many bytes of CRLF ASCII with a trailing `0x00`. Last chunk. | High |
| 10 | Three-float global | Exactly 12 bytes: three `f32`. Semantics unknown (possibly fog/global color). | High (layout) |
| 11 | Effects/misc | 20-byte header: `u32 magic=0xd431`, `u32 count`, `u32 12`, `u32 flag`, `u32 start`, then opaque payload; some maps append ASCII entity-text fragments. | Medium |
| 12 | Model table | `u32 count`, then `count × 164-byte` records beginning with `char[4]` name such as `~22\0`; followed by an opaque tail. | High (header) |
| 14 | Asset data | Begins with ASCII `Version 1.10` (12 bytes), then opaque binary. | High (prefix) |
| 16 | Effects table | Begins with `u32` count-like value (e.g. 15, 19, 20), then variable records. Opaque. | Medium |
| 17 | Sky cube | Payload is always `0x108f0 = 6 × 0x2c28`. Each face starts with a 32-byte NUL-padded name, then opaque `f32` data. | High |
| 18 | Byte table | Opaque `u8` table; sizes are small and sometimes odd. | Low |
| 19 | Optional table | Present only in `stronghold.bsp` in this corpus; opaque. | Low |
| 20 | Index table | `u16[]`; `0xffff` is the dominant sentinel. | Medium |

### 3.1 Type 0 — plane records

`warfare.bsp` type-0 payload at `0x12b2c` begins:

```text
count = 5150
record 0: 00 00 00 00  00 00 80 3f  00 00 00 00  ff 7f 76 c4  01 00 00 00
          nx=0.0        ny=1.0        nz=0.0        dist=-986.0      flags=1
```

The payload size `0x1925c` satisfies `4 + 5150*20`. The same exact relation holds for the type-0 chunks in `citadel.bsp` and `dm_boat.bsp`.

### 3.2 Type 6 — texture dictionary

Example first entries:

| File | Payload offset | Count | First names |
|---|---:|---:|---|
| `warfare.bsp` | `0x2d579e` | 39 | `border02_128`, `roof02_128`, `bwall13b_128`, … |
| `citadel.bsp` | `0x321f0c` | 42 | `a_wall09_256`, `A_Zoner`, `a_wall04_256`, … |
| `dm_boat.bsp` | `0x3bd35a` | 42 | `floor08_128`, `mwall04_256`, `a_iron01_64`, … |

Trailer fields in every sampled record are `u32 0` followed by `u32 0xffffffff`.

### 3.3 Type 9 — entity script

The payload is:

```text
u32 length
char text[length]   # CRLF line endings; final byte is 0x00
```

`warfare.bsp` chunk header at `0x629e5f`:

```text
09 00 00 00        # type 9
79 12 01 00        # payload size 0x11279 = 70265
-- payload @ 0x629e67 --
75 12 01 00        # text length 0x11275 = 70261
65 6e 74 69 ...    # "entity(\"info_pivot_point\")..."
```

`0x629e6b + 0x11275 = 0x63b0e0`, exactly EOF.

Grammar (no braces):

```text
entity("<class>")
\tproperty("<key>", "<value>")
\tproperty("<key>", "<value>")
entity("<next class>")
...
```

Counts:

| File | Text length | `entity(` lines | `property(` lines |
|---|---:|---:|---:|
| `warfare.bsp` | 70,261 | 121 | 2,030 |
| `citadel.bsp` | 95,875 | 150 | 2,821 |
| `dm_boat.bsp` | 33,861 | 89 | 912 |

### 3.4 Type 10 — three floats

| File | Payload offset | Bytes | Values |
|---|---:|---|---|
| `warfare.bsp` | `0x10964` | `ff c3 a8 c5 ff 9f 4d 45 ff f7 4f 45` | `(-5400.5, 3290.0, 3327.5)` |
| `citadel.bsp` | `0x10964` | `00 c0 c3 43 00 20 99 44 00 20 2b c5` | `(391.5, 1225.0, -2738.0)` |
| `dm_boat.bsp` | `0x10964` | `00 78 01 45 00 62 26 46 00 00 dd c3` | `(2071.5, 10648.5, -442.0)` |

### 3.5 Type 12 — model table

The first `u32` is a count, followed by that many 164-byte headers. Each header begins with a 4-byte name (`~N\0`).

| File | Payload offset | Count | Names observed |
|---|---:|---:|---|
| `warfare.bsp` | `0x26d9c6` | 21 | `~22`, `~21`, … `~2` |
| `citadel.bsp` | `0x2e9758` | 9 | `~10`, `~9`, … `~2` |
| `dm_boat.bsp` | `0x393ece` | 10 | `~11`, `~10`, … `~2` |

These names correspond to the `model_name` property values in the entity script (e.g. `property("model_name", "~22")`). The remainder of type 12 is an opaque table of 96-byte-aligned records and is not yet decoded.

### 3.6 Type 17 — sky cube

Payload size is always `0x108f0`, exactly six `0x2c28`-byte faces. Each face begins with a 32-byte NUL-padded name.

| Face | `warfare.bsp` / `citadel.bsp` | `dm_boat.bsp` |
|---:|---|---|
| 0 | `drydesert_dayxpos` | `sea_nightxpos` |
| 1 | `drydesert_dayypos` | `sea_nightypos` |
| 2 | `drydesert_dayzpos` | `sea_nightzpos` |
| 3 | `drydesert_dayxneg` | `sea_nightxneg` |
| 4 | `drydesert_dayyneg` | `sea_nightyneg` |
| 5 | `drydesert_dayzneg` | `sea_nightzneg` |

These names are also repeated as `world_properties` entries in the entity text.

---

## 4. Parser

`parser.py` (standard library only) implements:

- Header decoding and validation.
- Chunk-chain walking with exact EOF validation.
- Decoders for chunk types 0, 1, 6, 9, 10, 11, 12, 14, 17, 20.
- Best-effort summaries for the opaque chunk types.
- Entity/script parsing into structured `{class, properties}` records.

Usage:

```bash
python3 parser.py <file.bsp>                 # human-readable summary to stdout
python3 parser.py <file.bsp> --json out.json # full structured JSON to file
python3 parser.py <file.bsp> --json -        # full JSON to stdout
```

---

## 5. Known unknowns

- Semantics of the four header counters at `0x48..0x54`.
- Full record schemas for chunk types 2, 3, 4, 5, 8, 16, 18, 19.
- Meaning of the trailing fields in type-1 and type-12 records.
- Exact interpretation of the three `f32` values in type 10.
- Whether the ASCII fragment in some type-11 payloads is a duplicate/truncated entity stream or a separate effect script.

A converter can already recover level metadata, sky names, texture names, model references and the complete entity graph. Full geometry conversion additionally requires decoding the opaque chunks listed above.
