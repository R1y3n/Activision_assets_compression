# BiN `.anim` format — Shadow Force: Razor Unit

Status: **decoded for all observed controllers/sub-controllers**. All 13
`.anim` files in `cutscenes/cutscenes/Outro/`, the oddly named
`run_back.anim_`, and all 98 `AnimCtrl_Texture` tracks in `objects.fun`
parse to their exact EOF with the layout below.

All integers are **little-endian**; floats are **IEEE-754 binary32**.

## Container and track list

| Offset | Size | Field | Confidence |
|---|---|---|---|
| `0x00` | 4 | magic `"BiN\0"` | high |
| `0x04` | u16 | track/controller count | high |

Strings use the same `u8 length + raw bytes` encoding as `.obj` files.

`AnimPale.anim` has `03 00` (3 tracks), `helicopter_idle.anim` has `04 00`,
`STRING.anim` has `af 00` (175 tracks) — again proving the u16 is a count.

Track names correspond to node names in paired `.obj` files (e.g. `Box01`,
`Dummy01`, `Elice_mare`), and `.model` text files map animations to meshes
(e.g. `HELI.model` → `HELI.obj` + `HELI.anim`; `HELIPTFINAL.model` →
`Aterizare.obj` + `AnimPale.anim` / `fly.anim`).

## Keyframe controller track (`AnimCtrl_KeyFrame`)

| Rel. offset | Size | Field | Confidence |
|---|---|---|---|
| `+0` | string | controller name, always `AnimCtrl_KeyFrame` in samples | high |
| `+1+n` | string | sub-controller type (see table below) | high |
| `+2+n+m` | string | target node name | high |
| `+3+n+m+k` | u16 | number of keyframes `K` | high |
| — | key × `K` | keyframe records | high |

### Keyframe record

Every keyframe starts with a **u32 time** (monotonically increasing, first
value 0), followed by the sub-controller payload:

| Sub-controller | Stride | Payload after `u32 time` | Confidence |
|---|---|---|---|
| `Pos` | 16 | `f32[3]` position | high |
| `Rot` | 20 | `f32[4]` quaternion | high |
| `PosRot` | 32 | `f32[3]` position + `f32[4]` quaternion | high |
| `PosRotVis` | 34 | `PosRot` payload + `u16` visibility | high (stride), medium (last field) |
| `Vis` | 6 | `u16` visibility | inferred only; not observed in samples |

The converter binary contains exactly the sub-controller tokens `Rot`, `Pos`,
`PosRot`, `Vis`, and controller tokens `AnimCtrl_KeyFrame`, `AnimCtrl_Light`,
`AnimCtrl_Texture`, `AnimCtrl_LipSync`. Only the first two controllers appear
in the cutscene samples.

All parsed quaternions are unit length (norm 1.0), which validates the 32/20/
34-byte strides. The `PosRotVis` trailing u16 is `0x0000` or `0xffff`; treated
as a visibility flag, but its polarity is unknown.

`Rot` tracks are common in character animations (e.g. `run_back.anim_` uses
`PosRot` for the root/pelvis and `Rot` for limb bones); `Pos` is used by
footstep controllers (`GI2.anim`).

### Time values

Times are unsigned 32-bit integers, strictly increasing, typically starting at
0 and stepping by 21/33/34/35/etc. depending on the clip (e.g. `AnimPale.anim`
`Box02`: 0, 33, 66, 100, 133, … 500). They are most likely **frame/tick
indices**, not milliseconds — the .model text files carry separate timing
parameters (`0.3`, `1.00`, `NO_LOOP`) but no explicit frame rate, so units are
unconfirmed. The light controller's `duration` field equals or closely matches
the maximum keyframe time in the same file (GI1: 5133 = max; GI4: 5337 vs max
5333), supporting the "tick count" interpretation.

### Worked example — `AnimPale.anim` (1161 bytes = `0x489`)

```
0x000  BiN\0
0x004  03 00                              3 tracks
0x006  11 "AnimCtrl_KeyFrame" 06 "PosRot" 05 "Box01"
0x025  01 00                              1 keyframe
0x027  time=0, pos(0,0,0), quat(0,-1,0,0)  -> 0x047
0x047  track "Box02", 16 keys              -> 0x268
0x268  track "Box03", 16 keys              -> 0x489 EOF
```

## `AnimCtrl_Light` track (`Firelight`)

Found in `GI1.anim` and `GI4.anim`. Different layout — no target string:

| Rel. offset | Size | Field | Confidence |
|---|---|---|---|
| `+0` | string | `"AnimCtrl_Light"` | high |
| `+1+n` | string | `"Firelight"` | high |
| `+2+n+m` | u32 | duration / final tick (0x140d = 5133 in GI1) | medium (meaning) |
| `+6+n+m` | u16 | property count (9) | high |
| — | property × count | property records | high |

Each property record: `string name`, `u16 key count`, then key data:

| Property | Key size | Data | Confidence |
|---|---|---|---|
| `Color` | 12 | `f32[3]` RGB | high |
| `PosRot` | 28 | `f32[3]` pos + `f32[4]` quat, **no time** | high |
| `Intens`, `Falloff`, `Hotspot`, `AttenStart`, `Radius`, `NearAttenStart`, `NearAttenEnd` | 4 | one `f32` per key | high |

The property tokens exactly match the converter's `Invalid LightAnim chunck`
string table. In `GI1.anim` the light track spans `0xee11`–`0xf13c` (811
bytes) with 9 properties; `Intens` has 154 float keys, ending with `PosRot`
whose 28 bytes exactly reproduce the `Firelight` node transform in `GI1.obj`.

## `AnimCtrl_Texture` track (`LOD_1`)

Found in the 98 `blink.anim` files under `objects/Models/*`. No target node
string; instead the track addresses material channels by index:

| Rel. offset | Size | Field | Confidence |
|---|---|---|---|
| `+0` | string | `"AnimCtrl_Texture"` | high |
| `+1+n` | string | `"LOD_1"` | high |
| `+2+n+m` | u16 | keyframe count `K` | high |
| `+4+n+m` | u16 | channel count `C` (8, 10, 12 or 14 observed) | high |
| — | `u16[C]` | channel/material indices | high |
| — | key × `K` | `u32 time` + `f32[2*C]` values (stride `4 + 8*C`) | high |

The stride was confirmed by requiring every one of the 98 tracks to end
exactly at EOF (key counts 10–20, channel counts 8/10/12/14). The values are
likely RGB/alpha-style envelope parameters for the listed channels; their
precise semantics remain an open question. glTF has no equivalent animation
channel, so the converter records a concise note in the mesh JSON and keeps
the full keyframes in the standalone `.anim` JSON.

## Unknowns / low-confidence items

- Absolute time unit / frames-per-second; no playback-rate metadata in these files.
- Quaternion component order (`xyzw` vs `wxyz`).
- Polarity of the `PosRotVis` visibility u16 (`0` vs `0xffff`).
- Meaning of the light `duration` u32 (matches last tick, but not exactly in GI4).
- Semantics of the `f32[2*C]` payloads in `AnimCtrl_Texture` tracks.
- `AnimCtrl_LipSync` is present in ASC2BIN but absent from all sampled files;
  the parser reports an error rather than guessing its layout.
