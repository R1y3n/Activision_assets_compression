# `.fnt` bitmap fonts and `.fx` particle scripts

## 1. `.fnt` — `SFNT` bitmap font container

All 14 files in `fonts.fun` share one layout; sizes differ **only** with the
length of the embedded font name (701 bytes for `Font1Large` etc., 707 for
`verdanafont21_32`). Every file contains exactly **94 glyph records**, one per
printable ASCII code `0x21` (`!`) … `0x7E` (`~`), in ascending order.

### Layout

| Offset | Size | Type | Example (`Font1Large.fnt`) | Confidence / meaning |
|---|---:|---|---|---|
| `0x00` | 4 | ASCII | `"SFNT"` | High — signature |
| `0x04` | variable | ASCII+NUL | `"Font1Large\0"` (11 bytes) | High — font/atlas name; stripped name maps to `Textures\\Font\\<name>.tga` in `textures.fun` |
| `p = end(name)+1` | 1 | u8 | `0x15` (21) / `0x0A` (10) | High — font point/pixel size; matches `…21_32` vs `…10_16` filenames |
| `p+1` | 4 | **BE** u32 | `0x00000020` (32) / `0x0000000F` (15) | High value, medium meaning — glyph row/cell height (see atlas notes) |
| `p+5` | 4 | **BE** u32 | `0x00000010` (16) / `0x00000008` (8) | Medium — about half the row height; maybe glyph nominal height |
| `p+9` | 4 | **BE** u32 | `0` | High value; meaning unknown (flags?) |
| `p+13` | 4 | **BE** u32 | `32` / `15` | Same as `p+1`; medium meaning |
| `p+17` | 4 | raw | `00 00 00 00` (large) / `00 00 00 80` (small) | Low — BE reads 0/128; correlates with atlas 256/128 px (256 may be stored as 0 mod 256) |
| `p+21` | 4 | raw | `01 00 00 00` (large) / `00 00 00 80` (small) | Low — BE reads 0x01000000/128, LE reads 1/0x80000000; endianness ambiguous |
| `p+25` | 3 | raw | `01 00 00` (large) / `00 00 00` (small) | Low — maybe atlas scale/flags |
| `p+28` | 7 × 94 | records | see below | High |

For `Font1Large.fnt`, `p = 0x0F`, so the first glyph starts at file offset
`0x2B` and the file ends at `0x2BD` (701 = 43 + 94×7).

### Glyph record (7 bytes)

| Offset in record | Size | Type | Example (`!` at `0x2B`: `21 00 00 00 00 08 05`) | Confidence / meaning |
|---|---:|---|---|---|
| +0 | 1 | u8 | `0x21` (`!`) | High — ASCII code, strictly ascending |
| +1 | 4 | u32 **LE** | `0x00000000` | High — atlas offset, decomposes into two u16 pixel values |
| +1 | 2 | u16 LE = low half | `0x0000` (=0), `0x0015` (=21), … | High — U pixel coordinate in atlas (column stride 21 px large / 10 px small) |
| +3 | 2 | u16 LE = high half | `0x0000`, `0x0020` (=32), `0x0040` (=64), … `0x00E0` (=224) | High — V pixel coordinate (row stride 32 px large / 15 px small) |
| +5 | 1 | u8 | `8` | **Unknown** — small per-glyph metric (2–8); possibly horizontal bearing/advance |
| +6 | 1 | u8 | `5` | **Unknown** — small per-glyph metric (5–19); possibly vertical bearing/height |

Evidence for U/V interpretation: values advance by 21 (`Font1Large`) or 10
(`Font1Small`) for 12 glyphs, then V jumps by 32 or 15 and U resets to 0; 8 rows
→ 12×8 = 96 slots ≥ 94 glyphs. Matching TGA atlases in `textures.fun`:
`font1large.tga` = 256×256, 32 bpp; `font1small.tga` = 128×128, 32 bpp;
`verdanafont21_32.tga` = 256×256. 12×21 = 252 ≤ 256 and 8×32 = 256;
12×10 = 120 ≤ 128 and 8×15 = 120 ≤ 128.

Bytes 5/6 did **not** match the ink bounding box measured directly from the
TGA alpha channel (e.g. `!` ink spans x=4…17, y=8…24 in a 21×32 cell, but the
record says 8 and 5), so they are not simply ink width/height. They may be GDI
ABC widths/bearings or a packed metric; left as unknown.

### Byte example (first 48 bytes, `Font1Large.fnt`)

```
53 46 4e 54 46 6f 6e 74 31 4c 61 72 67 65 00 15  SFNTFont1Large..
00 00 00 20 00 00 00 10 00 00 00 00 00 00 00 20  ... ...........
00 00 00 00 01 00 00 00 01 00 00 21 00 00 00 00  ............!...
```

Parser: `../parsers.py parse_fnt` → JSON with decoded header bytes, u/v and
raw offsets for all 94 glyphs.

## 2. `.fx` — particle-system scripts

Three files: `fx/Scripts/init.fx` (shader/FX registration), `pssystem.fx`
(87 KB, 90 systems), `gesystem.fx` (3 systems, geometry-based particles).
Syntax is the same Lua subset as `.model`/`.script`: CRLF, `--` comments,
`function NAME()` blocks, optional `;`, and `if … then … end`.

### Top-level (`init.fx`)

```
LoadShader("fx_puff1");       -- 43× — register a shader function from FX_Shaders.script
LoadScript("Fx\\Scripts\\pssystem.fx");   -- 2× — include other .fx files
DefineFX("FXPSSYSTEM_PUFF0"); -- 86× — publish function of that name as a usable effect
if bloodwounds==1 then LoadShader("fx_blooddrop"); end
```

`DefineFX(name)` references a function defined in `pssystem.fx`/`gesystem.fx`
(e.g. `FXPSSYSTEM_PUFF0`, `FXPSSYSTEM_EXPLOSION0_DEBRIS`).

### Per-system calls (from exhaustive scan of the three files)

| Call | Args | Count | Interpretation (confidence) |
|---|---:|---:|---|
| `MaxParticles(n)` | 1 | 90 | Particle pool size (high) |
| `Shader("name")` | 1 | 89 | Shader from `.script` (high) |
| `Force(x,y,z)` | 3 | 78 | Constant acceleration vector (medium) |
| `AForce(x,y,z)` | 3 | 1 | Angular force (low) |
| `Friction(a,b)` | 2 | 50 | Damping (medium) |
| `Colision()` | 0 | 5 | Enable collision (sic; high) |
| `ShapeBox(dx,dy,dz)` | 3 | 88 | Spawn box extents (high) |
| `VelocityCone(a,b,c)` | 3 | 39 | Cone spawn velocity (medium) |
| `VelocityBox(6 numbers)` | 6 | 36 | Box spawn velocity (medium) |
| `VelocityMultiplier(a,b)` | 2 | 9 | Speed scale (low) |
| `Rotation(lo,hi)` / `Rotation()` | 2 / 0 | 65 / 1 | Initial random rotation / enable rotation (medium) |
| `AVelocity(lo,hi)` | 2 | 1 | Angular velocity (medium) |
| `FlipXY(a,b)` | 2 | 65 | Mirror flags on X/Y axes (medium) |
| `FXLife(a,b)` | 2 | 48 | System lifetime parameters (medium) |
| `UseLightLevel()` | 0 | 4 | Inherit world light level (medium) |
| `Type(n)` | 1 | 12 | Particle render type enum (low) |
| `Sound(n?)` | 4 | 14 | Sound cue parameters (low) |
| `Animation(a,b)` | 2 | 6 | Sprite/animation parameters (low) |
| `Model("path.obj","bone")` | 2 | 1 | Geometry particle source (`FXGESYSTEM_CELL0`, high) |
| `KeyMinLife(t,v)`, `KeyMaxLife(t,v)` | 2 | 91 each | Min/max particle lifetime keyframes (high) |
| `KeySize(t,v)`, `KeySizeX/Y/Z(t,v)` | 2 | 200 + 6 | Scale over normalized time (high) |
| `KeyAlpha(t,v)` | 2 | 87 | Alpha 0–255 over time (high) |
| `KeyRed/Green/Blue(t,v)` | 2 | 142/138/139 | Color channels over time (high) |
| `KeyRotAdd(t,v)` | 2 | 114 | Rotation increment over time (medium) |
| `KeyDebit(t,v)` | 2 | 83 | Emission rate over time ("debit" = flow, medium) |
| `KeyMinScale(t,v)`, `KeyMaxScale(t,v)` | 2 | 19 each | Size bounds over time (medium) |
| `KeyRect(t,u0,v0,u1,v1)` | 5 | 895 | Atlas rectangle keyframe; values like `0.375, 0.0625` = fractional sprite cells (high) |

Keyframe time is normalized `0.0…1.0` in nearly all cases (some life keys use
absolute seconds such as `KeyMinLife(0,12)` in `gesystem.fx`). `KeyRect` UVs are
fractions of the sprite texture (e.g. 1/16 = 0.0625 steps), used for
frame-animated sprites such as smoke or explosions.

### Example (`fx/Scripts/gesystem.fx`, first system)

```lua
function FXGESYSTEM_CELL0()
	Model("Objects\\Weapons\\cell0.obj", "Cylinder01");
	MaxParticles(32);
	Force(0, 0.2, 0);
	AForce(10, 10, 10);
	Friction(0, 1);
	Colision();
	KeyMinLife(0, 12);
	KeyMaxLife(0, 18);
	ShapeBox(0.16, 0.16, 0.16);
	VelocityCone(20, 0.3, 0.6);
	AVelocity(100, 150);
	Rotation();
	KeySizeX(0, 3);
	KeySizeX(1, 20);
	KeyAlpha(0, 255);
	KeyAlpha(1, 0);
end
```

Parser: `../parsers.py --format fx` reuses the generic DSL parser; output JSON
contains function blocks and all calls with parsed literal arguments.
