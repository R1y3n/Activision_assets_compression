# Archive inventory

Inventory of the supplied archives at
`/mnt/windows/Program Files (x86)/temmpp/fdown/razor/fun/`, produced by
walking each `.fun` container with the format reader. Machine-readable
details (entry counts and extension histograms) are in
[`archive-inventory.json`](archive-inventory.json).

| Archive | Bytes | Entries | Contents |
|---|---:|---:|---|
| `bsp.fun` | 51,463,045 | 26 | 26 `.bsp` levels |
| `cutscenes.fun` | 3,462,652 | 60 | `.obj`, `.anim`, `.model`, `.tga`, `.script`, `.lua`, `.log` |
| `editor.fun` | 83,706 | 10 | scripts, fonts/textures, `.ini`, `.cfg` |
| `fonts.fun` | 6,554 | 14 | 14 `.fnt` bitmap fonts |
| `fx.fun` | 648,499 | 36 | 32 `.tga`, 3 `.fx`, 1 `.script` |
| `map.fun` | 833,327 | 28 | 18 `.txt`, 8 `.tga`, 2 `.script` |
| `objects.fun` | 36,266,256 | 1,599 | 510 `.anim`, 385 `.tga`, 330 `.obj`, 193 `.model`, 174 `.script` |
| `patch1.fun` | 216 | 1 | 1 `.script` |
| `script.fun` | 8,825 | 8 | 7 `.lua`, 1 `.txt` |
| `shaders.fun` | 56,906 | 28 | 28 `.script` |
| `sound.fun` | 38,464,623 | 781 | 779 `.wav`, 1 `.lua`, 1 `.cfg` |
| `textures.fun` | 43,706,243 | 811 | 810 `.tga`, 1 `.dat` |

Total: **3,402 entries** across 12 archives. The separate
`Shadow-Force-Razor-Unit_Win_EN_Pre-Installed.zip` is the original installer
and is not part of the format analysis.
