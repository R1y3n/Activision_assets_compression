# Format index

All formats below were reverse-engineered from the `.fun` archives of
*Shadow Force: Razor Unit*. Detailed documents are added as each format is
verified against real files.

| Format | Extension(s) | Kind | Document | Reader |
|---|---|---|---|---|
| FUN archive | `.fun` | obfuscated ZIP-like container | [fun-archive.md](fun-archive.md) | `razorformats.fun` |
| BiN object | `.obj` | binary mesh / skeleton | [bin-obj.md](bin-obj.md) | `razorformats.obj` |
| BiN animation | `.anim` | binary keyframe animation | [bin-anim.md](bin-anim.md) | `razorformats.anim` |
| Model definition | `.model` | text scene/entity script | [model-script.md](model-script.md) | `razorformats.model` |
| Shader / config script | `.script` | text config DSL | [model-script.md](model-script.md) | `razorformats.script` |
| Level | `.bsp` | binary level archive | [bsp.md](bsp.md) | `razorformats.bsp` |
| Font | `.fnt` | binary bitmap font | [font-fx.md](font-fx.md) | `razorformats.fnt` |
| Effect | `.fx` | text particle config | [font-fx.md](font-fx.md) | `razorformats.fx` |
| Texture | `.tga` | standard TGA | [standard-formats.md](standard-formats.md) | `razorformats.tga` |
| Audio | `.wav` | standard RIFF/WAVE | [standard-formats.md](standard-formats.md) | `razorformats.wav` |
| Lua / ini / cfg / txt | `.lua`, `.ini`, `.cfg`, `.txt` | plain text | [standard-formats.md](standard-formats.md) | (read as text) |

A few entries use mangled extensions: `run_back.anim_` is a normal BiN
animation, `_laser_display_64._tga_` is a TGA, while `lod1.tani` and
`__ammo._obj` are ASCII converter inputs. The CLI detects all of these by
magic bytes rather than by extension.

## Conversion targets

## Lossless repacking

For shrinking the original game build without changing what the game reads, see
[repacking.md](repacking.md). It documents the runnable repacked-folder mode and
the smaller `.tar.xz` bundle mode.

## Conversion targets

The converter emits vendor-neutral open formats:

* **glTF 2.0** (`.gltf` + `.bin`) for meshes, materials and animations,
* **Wavefront OBJ** (`.obj` + `.mtl`) as a simple fallback,
* **PNG** for extracted TGA textures (pure-Python encoder),
* **JSON** for scripts, manifests and inspector output.
