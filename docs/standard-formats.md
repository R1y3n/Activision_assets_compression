# Standard / plain-text formats in the game data

Scope: the remaining non-proprietary or lightly-structured formats encountered
in `/tmp/opencode/razor_extract/` and in the `.fun` archives under
`/mnt/windows/Program Files (x86)/temmpp/fdown/razor/fun/`.
The binary BiN `.obj`/`.anim` and `.bsp` formats are explicitly out of scope.

All in-game text files are **7-bit ASCII** (no NULs, no non-ASCII bytes) with
**CRLF** line endings. Logs produced by our extraction tool are LF; those are
not game files.

## 1. `.tga` — uncompressed True-color TGA

Inspected 437 files (416 in `razor_extract/`, 3 extracted from `textures.fun`,
plus duplicates). Every one is a classic, **uncompressed, truecolor** TGA:

| Header bytes | Meaning | Observed values |
|---|---|---|
| `0` | ID length | `0` (always) |
| `1` | color-map type | `0` (none) |
| `2` | image type | `2` (truecolor), uncompressed |
| `3..7` | color-map spec | all `0` |
| `8..11` | origin X/Y | `0, 0` |
| `12..15` | width, height (LE u16) | `16×16`, `32×32`, `64×64`, `128×128`, `256×256`, `512×512` |
| `16` | bits per pixel | `24` (196 files) or `32` (231 files) |
| `17` | image descriptor | `0x00`, `0x08`, or `0x20` |

Descriptor meanings: `0x00` = 24-bit, bottom-left origin; `0x20` = 24-bit,
**top-left** origin; `0x08` = 32-bit, bottom-left origin with 8 alpha bits.
No RLE (`type 10`), no palettes, no image ID fields, and no `TRUEVISION-XFILE`
footer occur. File length is always exactly `18 + width*height*(bpp/8)`
(434/437 checked in `razor_extract`; all 3 archive samples too).

Font atlases are 32-bit (alpha in the 4th byte, normally B/G/R/A ordering from
the descriptor) whereas most world textures are 24-bit.

Evidence: `/tmp/opencode/razor_extract/objects/objects/Weapons/M4/TEXTURE.TGA`,
`/tmp/opencode/research/misc/samples/textures/Font/font1large.tga`
(256×256, 32 bpp, descriptor `0x08`).

Reader: `parsers.py parse_tga` (header/chunk summary, expected-size check).

## 2. `.wav` — RIFF/WAVE PCM

`sound.fun` contains 779 `.wav` files. Headers were scanned directly from the
archive payloads (read-only, via the existing extractor's manifest); all share
one shape:

```
52 49 46 46 <riff_size LE> 57 41 56 45          RIFF....WAVE
66 6d 74 20 14 00 00 00                         fmt , size 20
01 00 <channels> <rate LE> <byte_rate LE> <align> <bits>
<4 filler bytes>                                 part of the 20-byte fmt chunk
64 61 74 61 <data_size LE> <PCM samples>         data...
```

- Audio format is always `1` (integer PCM), never ADPCM or floating point.
- Channels: 697 mono, 82 stereo.
- Sample rates: 16000 Hz (377), 22050 Hz (344), 44100 Hz (31),
  11025 Hz (25), 22254 Hz (2). Bit depths: 16-bit (719), 8-bit (60).
- `fmt ` chunk size is **20**, not the usual 16/18: the last 4 bytes are
  filler, most often `cc cc cc cc` (752 files), sometimes `00 00 13 00` (18)
  or `00 00 cc cc` (9).
- Chains are `fmt ` → `data` (749 files) or `fmt ` → `data` → `smpl` (30 files).

Quirk: for the 30 looped/ambience files with a trailing standard RIFF `smpl`
chunk (sample-loop metadata, 36 or 60 bytes), the `riff_size` field still
stops at the end of the `data` chunk and does **not** include the `smpl`
chunk. A tolerant reader should ignore `riff_size` and walk chunks from
offset 12 until EOF.

Evidence: `/tmp/opencode/research/misc/samples/sound/11k/AnyThere/1.wav`
(16 kHz mono 16-bit 20-byte fmt) and archive entry
`sound/11k/EnvSound/buzz_low1.wav` (extra `smpl` chunk).

Reader: `parsers.py parse_wav` walks all chunks and decodes `fmt `.

## 3. `.lua` — game Lua DSL, not standalone Lua programs

15 unique `.lua` files (12 in `razor_extract/`, 3 in the corpus). ASCII/CRLF,
`--` comments, optional `;`. They are data/config scripts interpreted by the
game, not general Lua libraries. Four distinct idioms occur:

| Location | Idiom | Example calls |
|---|---|---|
| `script/script/Game/weapon.lua`, `level.lua`, `*character.lua`, `mp_*.lua` | `DefineX()` block followed by `Set("key", value)` | `DefineWeapon`, `DefineCharacter`, `DefineLevel`, `DefineMPGame`; 543 `Set` calls in `weapon.lua` |
| `objects/objects/Models/character_anim.lua`, `character_idle.lua` | flat animation table | 74 / 13 `animation("name", "path.anim", ...)` calls (same syntax as `.model`) |
| `sound/sound/Env/env.lua` | environment sound table | 32 `NewEnvironment2/3()` + 1216 `Set2/Set3()` calls |
| `cutscenes/.../Splash1.lua`, `Splash2.lua`, `editor/editor/ai.lua` | imperative UI/editor API | `FontLoad`, `RenderKeyRectangle`, `declare_property`, `declare_choice_property`, … |

The same Lua-like lexer/parser in `parsers.py` handles the flat call blocks.
Semantics of the `Define*`/`Set` keys are engine-specific and were not
reverse-engineered further.

Evidence: `/tmp/opencode/razor_extract/script/script/Game/weapon.lua`,
`/tmp/opencode/research/misc/corpus/objects/objects/Models/character_anim.lua`.

## 4. `.txt` — mission text and credits

19 files. Two shapes:

1. **Tagged text blocks** (briefings, intelligence, multiplayer help):

```
<X: 0 Y: 15 W: 0 H: 0 T:
   Free text, may span multiple lines
>
<X: 0 Y: 225 W: 0 H: 0 T:
   Another block
>
.
```

   `X/Y/W/H` are position/size integers for the in-game text box; `T:` starts
   the payload; `>` closes it. Files often end with a lone `.` terminator.
   Examples: `map/map/Level1/briefing1.txt` (2 blocks),
   `map/map/Level2/briefing2.txt` (6 blocks),
   `map/map/multi/multhelp.txt` (5 blocks).

2. **Plain strings** — `objectives1.txt`, `short1.txt` contain bare lines,
   bullet lines beginning with `*`, and optional final `.`.

`script/script/Others/Credits.txt` is a third, simple variant: `#` starts a
credit line/paragraph and bare lines (e.g. `FUN labs:`) are headings.

No escaping/encoding layer: read ASCII/CRLF and parse the angle-bracket blocks
by hand.

## 5. `.ini`, `.cfg`, `.log`, `.dat`

### `.ini`
Only `editor/editor/editor.ini` (996 bytes). Standard Windows INI: sections
`[Main]`, `[Editor]`, …, `key=value` lines, integer values, no comments or
duplicate keys observed. Python `configparser` reads it directly.

### `.cfg`
The extension is reused for two unrelated syntaxes:

- `editor/editor/render.cfg` (100 bytes): sectionless `KEY=value` lines
  (`API`, `ADAPTER`, `WIDTH`, `HEIGHT`, `WINDOWED`, `BPP_*`).
- `sound/sound/Env.cfg` (4835 bytes, extracted from `sound.fun`): Lua-like
  calls with `--` comments documenting `id, name, 3dmode, mindist, room ratio,
  probability`. 49 `EnvironmentSound(id, "path.wav", mode, dist, ratio[, prob]);`
  calls. Parse with the same call parser as `.fx`.

### `.log`
Two provenances:

- In-game: `cutscenes/cutscenes/Outro/debug.log` (922 bytes, CRLF) — lines like
  `[UNKNOWN ] set active chain no.3 (10706) ...`; a fixed-width 9-character
  severity tag followed by free text.
- Extraction artifacts: `/tmp/opencode/razor_extract/*.log` are LF Python
  logging output from the existing `extractor.py` (`timestamp [LEVEL] message`)
  and are **not** game data.

### `.dat`
Only `textures/texture.dat` (40 bytes) from `textures.fun`:

```
0a 00 00 00 ef 51 b9 b8 71 a1 a9 17 c2 fc dc ee
d4 2a 63 3e e7 1d 22 1a bf fe 0a 8b f6 df 02 d3
44 f4 2c d9 57 18 b8 8a
```

First LE u32 = `10`; remaining 36 bytes are high-entropy with no ASCII, no
recognizable signature, and no internal offsets. It is likely a checksum /
manifest blob tied to the 810 textures. No further structure was inferred;
treat as opaque.

## Out of scope / leftovers

`.obj`, `.anim` (binary BiN) and `.bsp` were not analyzed by request. Editor
state files such as `.history`, `.dockstate`, `.bspshaders` were seen but not
prioritized; `.dockstate` is binary and `.history`/`.bspshaders` are plain
path/name lists.
