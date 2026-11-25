# BSP reverse-engineering findings

## Executive summary

The `.bsp` files are **not Quake-style lumps with a directory**. They are a simple, forward-chained chunk stream:

```text
[92-byte header]
repeat: [u32 chunk_type][u32 payload_size][payload bytes ...]
```

All 26 sampled files satisfy this framing exactly: walking chunks from `0x5c` lands precisely on EOF, with no padding or trailing data. The last chunk is always type 9 (ASCII entity script). The second chunk is always type 17 (a fixed-size six-face sky cube).

The parser delivered with this report was run against all 26 files without a framing or decoding failure. It reliably recovers:

- header/tag/build metadata,
- exact chunk boundaries and sizes,
- texture/material names (chunk 6),
- model aliases such as `~22` (chunk 12),
- sky face names (chunk 17),
- the full entity/property graph (chunk 9).

The remaining large binary chunks (types 2–5, 7–8, 14, 16, 18, 19) are **identified and bounded but not fully decoded**. A converter can preserve them byte-for-byte, but full mesh/lightmap reconstruction requires additional work.

---

## Confidence summary

| Finding | Confidence | Basis |
|---|---|---|
| 92-byte header and version constants | High | `0x04 == 92`; version `15`, subversion `5` in all 26 files |
| Chunk chain `[type][size][payload]`, no lump table | High | Chain ends exactly at EOF in all 26 files |
| Chunk 0 = empty first record, then 20-byte plane table | High | Exact size relation `4 + count*20` in all 26 files |
| Chunk 1 = 36-byte records with 6-float bounds | High | Exact size relation `4 + count*36` in all 26 files |
| Chunk 6 = 40-byte texture records | High | Exact size relation `4 + count*40`; readable names in all files |
| Chunk 9 = length-prefixed CRLF entity text, last chunk | High | Length equals `payload_size-4`; line grammar uniform across 26 files |
| Chunk 10 = exactly three `f32` values | High (layout) | 12-byte payload in every file |
| Chunk 12 = model table with `~N` names | High (header) | Count + 164-byte records in every file |
| Chunk 17 = 6-face sky cube | High | Fixed `0x108f0` payload = `6 × 0x2c28`; six names per file |
| Header counters at `0x48..0x54` | Low | No consistent correlation with any counted structure found |
| Semantics of geometry chunks 2–5, 8, 16 | Low | Variable-length records; only counts/sentinels identified |
| Semantics of three floats in chunk 10 | Low | Values are large and not obviously RGB/direction |

---

## Key byte-level evidence

### Header and first chunks

| File | Header `0x00..0x0c` | Tag | Counters `0x48..0x54` | Chunk @ `0x5c` |
|---|---|---|---|---|
| `warfare.bsp` | `15, 92, 5, 42` | `MISHA` | `73, 1130, 670, 23` | `type=0, size=0` |
| `citadel.bsp` | `15, 92, 5, 42` | `HISHU` | `106, 604, 1084, 29` | `type=0, size=0` |
| `dm_boat.bsp` | `15, 92, 5, 42` | `HISHU` | `106, 84, 4737, 44` | `type=0, size=0` |

Correct interpretation: the first chunk header is at `0x5c`; the values at `0x64` and `0x68` (`17`, `0x108f0`) are the **type and size of chunk 17**, not extended header fields.

### Chain termination

| File | Last chunk header | Type | Payload size | Payload end vs file size |
|---|---:|---:|---:|---|
| `warfare.bsp` | `0x629e5f` | 9 | `0x11279` | `0x63b0e0` = EOF |
| `citadel.bsp` | `0x7392a0` | 9 | `0x17687` | `0x75092f` = EOF |
| `dm_boat.bsp` | `0xf72f2e` | 9 | `0x8449` | `0xf7b37f` = EOF |

### Entity text

| File | Chunk 9 payload | Declared text length | Entities | Properties |
|---|---:|---:|---:|---:|
| `warfare.bsp` | `0x629e67` | 70,261 | 121 | 2,030 |
| `citadel.bsp` | `0x7392a8` | 95,875 | 150 | 2,821 |
| `dm_boat.bsp` | `0xf72f36` | 33,861 | 89 | 912 |

The text starts with lines such as:

```text
entity("info_pivot_point")
	property("position", "877.000000 -592.000000 -544.000000")
	property("angle", "0 0 0")
	property("name", "AI001_paste17_paste23_paste13")
	property("target_name", "oldpiv07")
```

No braces are used. Lines are CRLF-terminated; properties are tab-indented; the final byte of the text block is `0x00`.

### Sky cube names

| Face | `warfare.bsp` / `citadel.bsp` | `dm_boat.bsp` |
|---:|---|---|
| 0 | `drydesert_dayxpos` | `sea_nightxpos` |
| 1 | `drydesert_dayypos` | `sea_nightypos` |
| 2 | `drydesert_dayzpos` | `sea_nightzpos` |
| 3 | `drydesert_dayxneg` | `sea_nightxneg` |
| 4 | `drydesert_dayyneg` | `sea_nightyneg` |
| 5 | `drydesert_dayzneg` | `sea_nightzneg` |

### Texture and model tables

| File | Chunk 6 count | Example texture | Chunk 12 count | Example models |
|---|---:|---|---:|---|
| `warfare.bsp` | 39 | `border02_128` | 21 | `~22 … ~2` |
| `citadel.bsp` | 42 | `a_wall09_256` | 9 | `~10 … ~2` |
| `dm_boat.bsp` | 42 | `floor08_128` | 10 | `~11 … ~2` |

---

## Chunk semantics recovered

| Type | Recovered layout | Notes |
|---:|---|---|
| 0 | Empty first occurrence; later `u32 count`, then `count × (f32 nx, ny, nz, dist, u32 flags)` | Plane-like records; exact stride 20 |
| 1 | `u32 count`, then `count × (u32,u32,u32, f32[6])` | Bounds-like records; exact stride 36 |
| 6 | `u32 count`, then `count × (char[32] name, u32 0, u32 0xffffffff)` | Texture/material dictionary; exact stride 40 |
| 9 | `u32 text_length`, `text[text_length]` | Entity graph; last chunk |
| 10 | `f32[3]` | Three global floats; semantics unknown |
| 11 | `u32 0xd431`, `u32 count`, `u32 12`, `u32 flag`, `u32 start`, then opaque/text | Some maps store ASCII fragments here |
| 12 | `u32 count`, then `count × 164-byte` headers starting with `~N\0`, then opaque tail | Model references used by entity `model_name` properties |
| 14 | ASCII `Version 1.10` prefix, then opaque binary | Asset/lightmap-style data |
| 17 | Six fixed `0x2c28` face records, each with 32-byte name | Sky cube; total payload always `0x108f0` |
| 20 | `u16[]` with many `0xffff` sentinels | Index/visibility table |

---

## Converter requirements

### What can be converted now

1. **World metadata**: version, build, author tag, header counters, all chunk offsets/sizes.
2. **Entity graph**: every `entity(...)` and its `property(...)` values. This is sufficient to reconstruct object placement, lights, spawn points, objectives, triggers, AI hints and `world_properties`.
3. **Sky**: the six sky-face names give the environment set (e.g. `drydesert_day`, `sea_night`). The remaining face bytes are opaque and should be preserved verbatim unless the renderer format is decoded.
4. **Materials**: texture names from chunk 6 can be resolved against `textures.fun` or extracted assets.
5. **Model references**: `~N` names from chunk 12 and entity `model_name` properties identify referenced models.

### What is required for full geometry conversion

- Decode chunk types 2, 3, 4, 5 and 8. These contain the bulk of the geometry/index data. They use variable-length records with `0xffff` sentinels, pointer-like fields and `f32` bounding boxes.
- Decode chunk 14 (`Version 1.10` binary) and chunk 16 if lightmaps/particle systems must be reproduced visually.
- Decode chunk 12's opaque tail to obtain per-model geometry/materials.
- Resolve external assets: texture images, models, sky images and shaders from the `.fun` archives.
- Confirm coordinate conventions. Entity positions are plain `f32` triplets; units appear to be game units (hundreds–thousands), but handedness/up-axis and transform order were not established from BSP data alone.

### Recommended converter strategy

1. Parse the header and validate the chunk chain; reject files whose chain does not end at EOF.
2. Parse and export chunk 9 as an authoritative entity list.
3. Parse chunks 6, 12 and 17 as lookup tables.
4. For unknown chunks, write raw payloads to sidecar files keyed by chunk index/type. This preserves information without inventing a schema.
5. Treat header counters and type-10 floats as opaque metadata until further evidence is found.

---

## Open questions

1. Semantics of the four header counters at `0x48`, `0x4c`, `0x50`, `0x54`. They do not consistently equal entity, property, texture, model, plane, face or vertex counts.
2. Meaning of the three `f32` values in chunk 10; values are large and not plausible 0–255 colors.
3. Exact schemas for variable-length records in chunks 2–5, 8, 16.
4. Whether the ASCII fragment in some type-11 payloads is a duplicate entity stream, a truncated script, or an effect definition.
5. Why `stronghold.bsp` has an extra type-19 chunk and header `0x58 == 2`.
6. Whether chunk 12's 164-byte records contain further interpretable transform/material fields beyond the `~N` name.

---

## Deliverables

- `/tmp/opencode/research/bsp/docs/bsp.md` — byte-level format specification with evidence from multiple files.
- `/tmp/opencode/research/bsp/parser.py` — standard-library parser with CLI and JSON output.
- `/tmp/opencode/research/bsp/FINDINGS.md` — this report.
