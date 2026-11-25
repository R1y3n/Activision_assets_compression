# `.model` and `.script` — FUN Labs Lua-like text DSLs

Scope: files from `cutscenes.fun`, `objects.fun`, `shaders.fun`, `fx.fun`,
`map.fun`, `editor.fun`, `patch1.fun`. All samples are ASCII/Latin-1 text with
**CRLF** line endings, tabs for indentation, `--` line comments, and are parsed
by the game with a small embedded Lua-compatible interpreter (Lua 5.x syntax
subset; `function`, `if … then … else … end`, `;`, string escapes).

## 1. `.model` — model definition script

### Grammar (derived)

```ebnf
file        := stmt* ;
stmt        := call [ ';' ]
             | 'if' expr 'then' stmt* [ 'else' stmt* ] 'end' ;
call        := IDENT '(' [ arg { ',' arg } ] ')' ;
arg         := STRING | NUMBER | IDENT | expr ;      (* expr = raw token run *)
comment     := '--' … end-of-line ;
```

Unicode/encoding: plain ASCII. Paths are Windows-style with `\\` **inside Lua
string literals** (one backslash at runtime), e.g. the bytes
`mesh("Cutscenes\\Outro\\STRING.obj")`; forward slashes also occur
(`objects/models/GI/model.obj`). Case of path/directory varies; the original
Windows filesystem was case-insensitive.

### Commands observed (193 `.model` in `objects.fun` + 14 in `cutscenes.fun`)

| Call | Args | Count (objects archive) | Meaning (confidence) |
|---|---|---|---|
| `model("NAME")` | 1 string | 193 | Registers the model under `NAME`. Often **not** the filename: e.g. `objects/Models/ArabTerro01/model.model` declares `model("agent")`; four Outro files declare `model("American_Heli")`. (high) |
| `mesh("path.obj")` | 1 string | 193 | Loads the visible mesh (binary BiN `.obj`, other agent's scope). (high) |
| `reference("path.obj")` | 1 string | 129 | Secondary/skeleton reference mesh, typically `ref.obj` next to `model.obj`; present on character models, absent on weapons/cutscenes. (medium) |
| `animation("name","path.anim", …)` | 2, 4, 5 or 6 args | 459 | Registers a named animation clip. Optional trailing numbers: `blend_in`, `blend_out`, `speed`; optional bare identifier `NO_LOOP`. (high for registration; medium for numeric semantics) |
| `LoadScript("path")` | 1 string | 254 | Executes another script: `.lua` animation tables (`character_anim.lua`, `character_idle.lua`) or another `.model` for skin variants. (high) |
| `set("key","value")` | 2 strings | 4 | Only `set("item angle", "15 -180 0")` and `set("item offset", "-0.2 0.4 0.0")` on the two flag models; likely attachment transform for item bones. (medium/low) |
| `parent("Bip01")` | 1 string | 2 | Attaches the model to a named bone of the parent character (`Bip01`, a 3ds Max Biped bone name). (medium) |

`animation` arity distribution in `objects.fun`: 2 args = 106, 4 = 147,
5 = 4, 6 = 202 (475 animations across all 207 `.model` files: 106/150/6/213).
The 6th argument is **always** `NO_LOOP`. Typical tails:

```
animation("blink", "objects/models/GI/blink.anim")                       -- 2 args
animation("idle",  "objects/weapons/m4/st/still.anim", 0.8, 0.3)          -- 4 args
animation("menu_idle", "Objects/Menu/Insigna/rotate.anim", 0.0, -0.0, 0.3) -- 5 args
animation("fire", "objects/weapons/m4/st/firing.anim", 0.3, 0.3, 1.0, NO_LOOP)
```

Common numeric tuples: `(0.3, 0.3, 1.0, NO_LOOP)` (152×), `(0.3, -1, 1.0, NO_LOOP)`
(35×). `-1` as second value on one-shot weapon fire animations strongly suggests
"no blend-out / use default" rather than a negative time. Speed values seen:
`0.3`, `0.5`, `1`, `1.0`, `1.3`. **Unknown:** exact units/order of the first two
values (likely blend-in/out times in seconds); no source engine code available.

### Conditional/skin mechanism

35 `if skin==N then … end` blocks occur across 13 character models. Example,
`objects/Models/GI/model.model` (byte-for-byte):

```lua
if skin==0 then\r\n
\tmodel("GI")\r\n
\t\tmesh("objects/models/GI/model.obj")\r\n
\t\treference("objects/models/GI/ref.obj")\r\n
\t\tLoadScript("objects/models/character_anim.lua")\r\n
end\r\n
if skin==1 then\r\n
\tLoadScript("objects/models/GIUnderCover/model.model")\r\n
end\r\n
```

`AgentCostume1` has three branches (`skin==0/1/2`) that `LoadScript()` other
`.model` files. There is no `else` branch in the sample; each `if` is
independent. `skin` is an engine-provided global.

### Evidence byte example (`cutscenes/Outro/STRING.model`, 139 bytes)

```
00000000  6d 6f 64 65 6c 28 22 53  54 52 49 4e 47 22 29 0d  model("STRING").
00000010  0a 09 6d 65 73 68 28 22  43 75 74 73 63 65 6e 65  .	mesh("Cutscene
00000020  73 5c 5c 4f 75 74 72 6f  5c 5c 53 54 52 49 4e 47  s\\Outro\\STRING
00000030  2e 6f 62 6a 22 29 0d 0a  09 61 6e 69 6d 61 74 69  .obj")..	animati
00000040  6f 6e 28 22 53 54 52 49  4e 47 22 09 09 09 22 43  on("STRING"..."
```

Confidence: high for lexical grammar/command set (exhaustive over all 207
available `.model` files); medium for parameter semantics. No shader or
texture calls appear in `.model` files — those live in `.obj`/`.script`.

## 2. `.script` — shader definition script

384 `.script` files examined (28 in `shaders.fun`, 174 in `objects.fun`, plus
cutscene/map/editor/fx/patch copies). Same Lua-like syntax; every shader is a
`function NAME()` block, and calls inside it build a state machine.

```ebnf
file       := (assignment | function)* ;
function   := 'function' IDENT '(' ')' stmt* 'end' [ ';' ] ;
stmt       := call | block | 'if' expr 'then' stmt* ['else' stmt*] 'end' ;
block      := 'BeginShader' '(' ')' stmt* 'EndShader' '(' ')'
            | 'BeginStage' '(' ')' stmt* 'EndStage' '(' ')'
            | 'BeginBlend' '(' expr ')' stmt* 'EndBlend' '(' ')'
            | 'BeginVertexDef' '(' ')' stmt* 'EndVertexDef' '(' ')' ;
```

Semicolons are optional (`EndShader()` and even `end;` occur). Global variables
are used, notably `insideEditor == 1` to conditionally include/emulate
editor-only rendering. `SHADER_*` constants can be combined with `+`:
`BeginBlend(SHADER_BLEND + SHADER_ALPHATEST)` (14 occurrences).

### Builtin vocabulary (call counts over all 384 files)

| Builtin | Args | Count | Notes |
|---|---:|---:|---|
| `BeginShader` / `EndShader` | 0 | 3766 each | Outer shader definition |
| `BeginStage` / `EndStage` | 0 | 3947 / 3943 | Texture stage; multiple per shader (water has 2) |
| `BeginBlend` / `EndBlend` | 1 / 0 | 1738 each | e.g. `SHADER_BLEND`, `SHADER_ALPHATEST`, sums |
| `BeginVertexDef` / `EndVertexDef` | 0 | 6 each | Vertex deformation functions |
| `BufferType` | 1 | 3687 | `SHADER_BSP`, `SHADER_MESH`, `SHADER_FX` |
| `Material` | 1 | 3239 | Usually `Material(0)`; selects material index |
| `TessSize` | 1 | 3233 | Usually `0.0` |
| `Cull` | 1 | 1819 | `SHADER_NONE` (most), other modes |
| `LightMap` | 0 | 1606 | BSP lightmap flag |
| `Texture` | 1 | 3886 | `"Path\\To\\Texture.tga"` |
| `TextureAnim` | 33 (or 3+) | 53 | First arg fps, then 32 texture paths (water) |
| `ColorGen` | 1 | 2046 | `SHADER_VERTEX_LIGHT`, `SHADER_VERTEX_COLOR` |
| `StageOp` | 1 | 276 | `SHADER_ALPHABLEND`, `SHADER_MODULATE`, `SHADER_ADD`, `SHADER_BLEND` |
| `AlphaSource` | 0 | 712 | Use texture alpha |
| `AlphaCompare` | 1 | 152 | Threshold `0..255`, e.g. `128.000000` |
| `SrcBlend`/`DestBlend` | 1 | 362 each | `SHADER_SRCALPHA`, `SHADER_INVSRCALPHA`, `SHADER_ONE` |
| `ZWriteDisable` | 0 | 534 | |
| `ZFunc` | 1 | 459 | `SHADER_ALWAYS`, `SHADER_EQUAL` |
| `NoMipMap` | 0 | 483 | |
| `Noresize` | 0 | 407 | |
| `FogDisable` | 0 | 421 | |
| `DontCastShadows` | 0 | 84 | |
| `AddressMode` | 1 | 482 | `SHADER_CLAMP` |
| `MappingSource` | 1 | 113 | `SHADER_SCREEN`, `SHADER_PLANAR_ENV` |
| `Scale` | 4 or 12/38 | 99 | UV scale `(du,dv,su,sv)`; multi-key form occurs |
| `Translate` | 2/4/6/… | 90 | `(0,"Time",-3.5,0)` or `("Table",3, …)` table-driven |
| `Rotate` | 3/5/7 | 17 | UV rotation; may be table-driven |
| `DefFunc` | 6–8 | 9 | `(SHADER_COMPONENT_X,"WaveUV",0.5,8,0,1,0)` |
| `DefOp` | 2 | 2 | `(SHADER_COMPONENT_NORMAL, SHADER_MODULATE)` |
| `Line` | 0 | 3 | Wireframe/line primitive (`Common_Shaders.script`) |
| `MaterialDiffuse` | 4 (also 7/11/15/16) | 140 | RGB/RGBA floats; extended forms use `"Sin"` wave tuples |
| `MaterialSpecular` | 3 | 68 | RGB floats |
| `MaterialEmissive` | 3 (also 7/15) | 55+ | RGB floats |
| `MaterialShines` | 1 | 60 | Specular exponent, e.g. `90.000000` |

`PolygonOffset()` appears only inside comments. Lowercase `texture(`/`shadow(`
hits in naive scans are comments such as `… texture (opaq)` or function names
ending in those words.

### Constants seen

`SHADER_BSP`, `SHADER_MESH`, `SHADER_FX`, `SHADER_NONE`, `SHADER_BLEND`,
`SHADER_ALPHATEST`, `SHADER_ALPHABLEND`, `SHADER_SRCALPHA`,
`SHADER_INVSRCALPHA`, `SHADER_ONE`, `SHADER_ALWAYS`, `SHADER_EQUAL`,
`SHADER_CLAMP`, `SHADER_SCREEN`, `SHADER_VERTEX_LIGHT`,
`SHADER_VERTEX_COLOR`, `SHADER_LIGHTMAP`, `SHADER_MODULATE`, `SHADER_ADD`,
`SHADER_DETAILOP`, `SHADER_CUSTOM`, `SHADER_ENV`, `SHADER_PLANAR_ENV`,
`SHADER_COMPONENT_X`, `SHADER_COMPONENT_Z`, `SHADER_COMPONENT_NORMAL`,
`SHADER_AXISU`.

### Example (`shaders/RainShaders.script`, first function)

```lua
function DefaultRainShader()
	BeginShader();
		NoMipMap();
		Cull (SHADER_NONE);
		BeginStage();
			Texture ("Textures\\DefaultRainTexture.TGA");
			AlphaSource();
			Translate (0, "Time", -3.5, 0);
		EndStage();
		BeginBlend (SHADER_ALPHATEST);
			AlphaCompare (64);
		EndBlend();
	EndShader();
end
```

### Conditional example (`editor/zone.script`)

```lua
function A_Zoner()
	if insideEditor == 1 then
		BeginShader();
			BufferType(SHADER_BSP);
			… Texture("Textures\\Maps\\Misc\\Z-zone.tga"); …
		EndShader();
	else
		BeginShader()
		EndShader();            -- empty shader outside editor
	end
end
```

Confidence: high for syntax/vocabulary; medium for parameter semantics of the
animated-transform calls (`Translate`/`Scale`/`Rotate`/`DefFunc`) — values can
be plain numbers, `"Time"`, or table indices (`"Table", 3, …`) and the engine's
interpreter is unavailable.

Parser: `../parsers.py` (`parse_model`, `parse_script`) returns a full JSON tree
with `function`, `if`, `block`, `call` nodes and line numbers; `--fields` emits
compact summaries.
