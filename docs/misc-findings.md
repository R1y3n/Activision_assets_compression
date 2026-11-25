# Findings — Shadow Force: Razor Unit data formats

Read-only investigation of the extracted game tree (`/tmp/opencode/razor_extract/`)
and the FUN archives at
`/mnt/windows/Program Files (x86)/temmpp/fdown/razor/fun/`.
All output lives in `/tmp/opencode/research/misc/`. No game file was modified.

Scope note: `.obj`/`.anim` (binary BiN) and `.bsp` were **not** analyzed, as
requested.

## Deliverables

| Path | Contents |
|---|---|
| `parsers.py` | Python 3 JSON CLI parser for `.model`, `.script`, `.fx`, `.fnt`, `.tga`, `.wav`. |
| `docs/model_script.md` | `.model` and `.script` Lua-like DSL grammar, vocabulary, counts, examples. |
| `docs/font_fx.md` | `.fnt` binary layout, `.fx` particle DSL vocabulary and keyframes. |
| `docs/standard_formats.md` | `.tga`, `.wav`, `.lua`, `.txt`, `.ini`, `.cfg`, `.log`, `.dat`. |
| `corpus/objects/` | all 193 `.model`, 174 `.script`, 15 `.lua` from `objects.fun` (read-only copies). |
| `corpus/sound/sound/Env.cfg` | extracted Lua-like sound-environment config. |
| `corpus/textures/textures/texture.dat` | extracted opaque 40-byte `.dat`. |
| `samples/` | selected `.model`, `.tga`, `.wav`, `.fnt` samples. |
| `extract_samples.py`, `extract_kind.py` | helpers that call the existing read-only `extractor.py`. |

## Parser usage

```sh
cd /tmp/opencode/research/misc
python3 parsers.py FILE                       # full JSON tree
python3 parsers.py --fields FILE              # compact summary
python3 parsers.py --format fx FILE           # force a DSL dialect
python3 parsers.py --selftest --fields        # representative files
python3 -m py_compile parsers.py
```

`parse_file(path, fmt=None)` is the importable entry point; `detect()` sniffs
`SFNT`/`RIFF…WAVE`/TGA by magic when the extension is unknown.

Validation: `py_compile` clean; `--selftest --fields` clean; a full sweep of
**1125** files under `razor_extract/`, `corpus/` and `samples/`
(345 `.model`, 342 `.script`, 3 `.fx`, 14 `.fnt`, 419 `.tga`, 2 `.wav`)
parsed with zero errors. All **779** `.wav` payloads in `sound.fun` were also
parsed in memory with zero errors, including the 30 files with a trailing
`smpl` chunk.

## Confidence summary

| Format | Confidence | Basis |
|---|---|---|
| `.model` grammar/commands | **High** | exhaustive over all 207 available files; only 7 distinct call names. |
| `.model` animation numeric args | Medium | arity/tail statistics only; no engine code. |
| `.script` grammar/vocabulary | **High** | exhaustive over 384 files; 3581 `function` blocks, stable `Begin*/End*` nesting. |
| `.script` animated-transform args | Medium | values may be numbers, `"Time"` or table indices. |
| `.fnt` signature/name/28-byte header/94×7 records | **High** | identical across all 14 files; arithmetic checks out exactly. |
| `.fnt` U/V atlas coordinates | **High** | monotonic strides match atlas dimensions in paired `.tga`. |
| `.fnt` header raw fields and glyph bytes 5/6 | **Low** | endianness/meaning unresolved; parser emits raw hex without guessing. |
| `.fx` call vocabulary/keyframes | **High** (syntax), Medium (semantics) | exhaustive over the only 3 files. |
| `.tga` | **High** | all 437 files are uncompressed truecolor, exact expected sizes. |
| `.wav` | **High** | all 779 archive payloads follow one RIFF shape with known quirks. |
| `.lua`/`.txt`/`.ini`/`.cfg`/`.log` | **High** for syntax, Low for game semantics | plain text, direct inspection. |
| `.dat` | **None** | 40 opaque bytes; left undescribed. |

## Key results by format

1. **`.model`** — Lua-like text, not a binary model. `model("NAME")`,
   `mesh("*.obj")`, optional `reference("*.obj")`, 0..475 `animation()` calls,
   `LoadScript()`, rare `set()`/`parent()`, and `if skin==N then` variants.
   Animation arities 2/4/5/6; the 6th arg is always `NO_LOOP`.
   Evidence: `/tmp/opencode/research/misc/corpus/objects/objects/Models/GI/model.model`.

2. **`.script`** — shader state machine in Lua syntax: `function NAME()` containing
   `BeginShader`/`BeginStage`/`BeginBlend`/`BeginVertexDef` blocks and ~40
   builtins with `SHADER_*` constants. `insideEditor == 1` branches exist.
   Evidence: `/tmp/opencode/razor_extract/shaders/shaders/RainShaders.script`.

3. **`.fnt`** — `SFNT` + NUL-terminated name + 28-byte header + exactly 94
   records (`0x21`..`0x7E`) of 7 bytes each. The LE u32 at record+1 packs a u16
   U and u16 V atlas offset; strides are 21×32 (large) or 10×15/16 (small).
   Evidence: `/tmp/opencode/razor_extract/fonts/fonts/Font1Large.fnt`,
   `/tmp/opencode/research/misc/samples/textures/Font/font1large.tga`.

4. **`.fx`** — same Lua subset; `LoadShader`/`DefineFX`/`LoadScript` at top level,
   90 particle systems with `MaxParticles`, `ShapeBox`, `Velocity*`, `Key*`
   normalized-time keyframes. Evidence: `/tmp/opencode/razor_extract/fx/fx/Scripts/pssystem.fx`.

5. **`.tga`** — 437/437 files are uncompressed truecolor type 2, no ID, no
   palette, no footer; 24 or 32 bpp; `18 + w*h*bpp/8` bytes exactly.

6. **`.wav`** — 779/779 are PCM format 1 with a nonstandard **20-byte `fmt `**
   chunk (4 filler bytes, usually `cc cc cc cc`). 30 files have a trailing
   `smpl` chunk but their `riff_size` omits it; readers must walk chunks from
   offset 12 rather than trust `riff_size`.

7. **Plain text** — `.lua` is a game configuration DSL (`DefineWeapon()/Set()`,
   `DefineLevel()/Set()`, `NewEnvironment2/3()`), not standalone Lua;
   `.txt` mission text uses `<X: n Y: n W: n H: n T: … >` blocks; `.cfg` has two
   unrelated syntaxes (`KEY=value` and Lua-like `EnvironmentSound(...)`);
   `.ini` is standard Windows INI; `.log` is CRLF `[LEVEL] …` game logging.

## Unresolved questions

- Engine semantics of `.model` animation blend/speed tuples and of `.script`
  `Translate`/`Scale`/`Rotate`/`DefFunc` table-driven arguments.
- Meaning of `.fnt` header fields 5–7 and glyph bytes 5/6 (likely font metrics,
  not ink bounds).
- Purpose and provenance of the 40-byte `textures/texture.dat` blob.
- `.obj`/`.anim` BiN and `.bsp` (explicitly out of scope), plus editor state
  files `.dockstate` (binary), `.history`, `.bspshaders`.
