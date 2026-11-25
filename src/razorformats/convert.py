"""Converters from the game's proprietary formats to open formats.

The main entry point is :func:`convert_path`.  Meshes become glTF 2.0,
Wavefront OBJ and JSON; textures become PNG; scripts, fonts and level metadata
become JSON.  Everything uses the Python standard library so the conversion
can be reproduced anywhere the reader runs.
"""

from __future__ import annotations

import json
import math
import shutil
from pathlib import Path
from typing import Any, Iterable, Sequence

from .anim import read_anim
from .binfile import parse_obj
from .bsp import inspect_bsp
from .dataformats import parse_model, parse_script, parse_wav
from .fnt import read_fnt
from .fun import FunArchive
from .gltf import AnimTrack, GLTFBuilder
from .objout import write_obj_scene
from .tga import convert_file as tga_to_png_file
from .tga import read_tga, write_png

Mat4 = list[float]


def _write_json(path: Path, value: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False,
                               default=str) + "\n", encoding="utf-8")
    return path


def _copy(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)
    return dst


def _looks_binary(data: bytes) -> bool:
    if b"\x00" in data:
        return True
    sample = data[:4096]
    controls = sum(1 for b in sample if b < 9 or 13 < b < 32)
    return controls > max(1, len(sample) // 20)


def _rewrite_temp_paths(directory: Path, temp_root: Path, label: str) -> None:
    """Replace scratch paths in JSON metadata with archive-relative labels."""
    prefix = str(temp_root)
    for path in directory.rglob("*.json"):
        text = path.read_text(encoding="utf-8")
        if prefix in text:
            path.write_text(text.replace(prefix, label), encoding="utf-8")


def _tga_candidates(mesh_path: Path) -> list[Path]:
    """Find TGAs next to a mesh or one directory above it."""
    folders = [mesh_path.parent]
    if mesh_path.parent.parent != mesh_path.parent:
        folders.append(mesh_path.parent.parent)
    found: list[Path] = []
    for folder in folders:
        found.extend(p for p in folder.iterdir()
                     if p.is_file() and p.suffix.lower() == ".tga")
    return sorted(set(found), key=lambda p: str(p).lower())


def _choose_texture(mesh_path: Path, shaders: Iterable[str],
                    explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit
    candidates = _tga_candidates(mesh_path)
    if not candidates:
        return None
    for shader in shaders:
        name = shader.lower()
        for texture in candidates:
            stem = texture.stem.lower()
            if name and (name in stem or stem in name):
                return texture
    return candidates[0] if len(candidates) == 1 else None


def _prepare_texture(texture: Path, dest: Path) -> Path:
    """Convert/copy a texture into ``dest/textures`` and return its path."""
    folder = dest / "textures"
    folder.mkdir(parents=True, exist_ok=True)
    png = folder / f"{texture.stem}.png"
    if texture.suffix.lower() == ".tga":
        tga_to_png_file(texture, png)
    else:
        _copy(texture, png)
    return png


# --- small 4x4 matrix helpers (column-major, matching glTF) -----------------

_IDENTITY: Mat4 = [1.0, 0.0, 0.0, 0.0,
                   0.0, 1.0, 0.0, 0.0,
                   0.0, 0.0, 1.0, 0.0,
                   0.0, 0.0, 0.0, 1.0]


def _normalize_quat(value: Sequence[float]) -> list[float]:
    x, y, z, w = (float(value[i]) if i < len(value) else 0.0
                  for i in range(4))
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if norm < 1e-12:
        return [0.0, 0.0, 0.0, 1.0]
    return [x / norm, y / norm, z / norm, w / norm]


def _local_matrix(translation: Sequence[float] | None,
                  rotation: Sequence[float] | None) -> Mat4:
    """Compose translation and an ``(x, y, z, w)`` quaternion."""
    matrix = list(_IDENTITY)
    if rotation is not None:
        x, y, z, w = _normalize_quat(rotation)
        matrix[0] = 1.0 - 2.0 * (y * y + z * z)
        matrix[4] = 2.0 * (x * y - z * w)
        matrix[8] = 2.0 * (x * z + y * w)
        matrix[1] = 2.0 * (x * y + z * w)
        matrix[5] = 1.0 - 2.0 * (x * x + z * z)
        matrix[9] = 2.0 * (y * z - x * w)
        matrix[2] = 2.0 * (x * z - y * w)
        matrix[6] = 2.0 * (y * z + x * w)
        matrix[10] = 1.0 - 2.0 * (x * x + y * y)
    if translation is not None:
        matrix[12:15] = [float(v) for v in translation]
    return matrix


def _mat_mul(a: Mat4, b: Mat4) -> Mat4:
    return [sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
            for col in range(4) for row in range(4)]


def _rigid_inverse(matrix: Mat4) -> Mat4:
    """Inverse of a rotation+translation matrix (the bind poses here)."""
    out = list(_IDENTITY)
    for row in range(3):
        for col in range(3):
            out[col * 4 + row] = matrix[row * 4 + col]  # transpose rotation
    t = matrix[12:15]
    for row in range(3):
        out[12 + row] = -sum(out[col * 4 + row] * t[col] for col in range(3))
    return out


def _world_matrix(name: str, nodes: dict[str, dict[str, Any]],
                  cache: dict[str, Mat4],
                  visiting: set[str] | None = None) -> Mat4:
    if name in cache:
        return cache[name]
    visiting = visiting or set()
    if name in visiting:
        return list(_IDENTITY)
    visiting.add(name)
    node = nodes.get(name)
    if node is None:
        return list(_IDENTITY)
    parent = node.get("parent")
    parent_matrix = (_world_matrix(parent, nodes, cache, visiting)
                     if parent and parent != name and parent in nodes
                     else list(_IDENTITY))
    result = _mat_mul(parent_matrix, _local_matrix(node.get("translation"),
                                                   node.get("rotation")))
    visiting.discard(name)
    cache[name] = result
    return result


# --- BiN animated object conversion ----------------------------------------

def _node_specs(obj: dict[str, Any], animation_results: list[dict[str, Any]]
                ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Collect all nodes needed by meshes, skins and animation targets."""
    specs: dict[str, dict[str, Any]] = {}
    parents: dict[str, str] = {}

    def add(name: str, *, parent: str | None = None,
            translation: Sequence[float] | None = None,
            rotation: Sequence[float] | None = None,
            extras: dict[str, Any] | None = None,
            bind: dict[str, Any] | None = None) -> str:
        key = name or f"node{len(specs)}"
        if key not in specs:
            specs[key] = {
                "translation": list(translation or [0.0, 0.0, 0.0]),
                "rotation": list(rotation or [0.0, 0.0, 0.0, 1.0]),
                "extras": extras or {},
                "bind": bind or {"name": key, "parent": parent or "World",
                                 "translation": translation or [0.0, 0.0, 0.0],
                                 "rotation": rotation or [0.0, 0.0, 0.0, 1.0]},
            }
            parents[key] = parent or ""
        return key

    for node in obj["nodes"]:
        extras: dict[str, Any] = {"flags": node["flags"],
                                  "source": "mesh_scene"}
        if node.get("extra_f32_3") is not None:
            extras["extra_f32_3"] = node["extra_f32_3"]
        add(node["name"], parent=node["parent"],
            translation=node["translation"], rotation=node["rotation"],
            extras=extras, bind=node)

    for mesh in obj["meshes"]:
        add(mesh["name"])
        for bone in mesh.get("bones", []):
            add(bone["name"], parent=bone["parent"],
                translation=bone["translation"], rotation=bone["rotation"],
                extras={"source": "skin_bone", "flags": bone["flags"]},
                bind=bone)

    for animation in animation_results:
        for track in animation.get("tracks", []):
            target = track.get("target")
            if target:
                add(target, extras={"source": "animation_target",
                                    "synthetic": target not in specs})
    return specs, parents


def _animation_tracks(animation: dict[str, Any],
                      node_index: dict[str, int]) -> tuple[list[AnimTrack], list[dict[str, Any]]]:
    tracks: list[AnimTrack] = []
    notes: list[dict[str, Any]] = []
    for track in animation.get("tracks", []):
        if track.get("controller") == "AnimCtrl_Texture":
            notes.append({
                "controller": "AnimCtrl_Texture", "type": track.get("type"),
                "channels": track.get("channels", []),
                "keys": len(track.get("keyframes", [])),
                "reason": "texture animation has no glTF channel; "
                          "full keyframes remain in the .anim JSON",
            })
            continue
        if track.get("controller") != "AnimCtrl_KeyFrame":
            notes.append({"skipped": track, "reason": "non-keyframe controller"})
            continue
        target = track.get("target")
        if target not in node_index:
            notes.append({"skipped": track, "reason": "target has no node"})
            continue
        node = node_index[target]
        keys = track.get("keyframes", [])
        times = [k["time"] / 1000.0 for k in keys]  # observed times are ms
        kind = track.get("type")
        if kind == "Pos":
            values = [k["values"][:3] for k in keys]
            tracks.append(AnimTrack(node, "translation", times, values))
        elif kind == "Rot":
            values = [_normalize_quat(k["values"][:4]) for k in keys]
            tracks.append(AnimTrack(node, "rotation", times, values))
        elif kind in ("PosRot", "PosRotVis"):
            tracks.append(AnimTrack(
                node, "translation", times, [k["values"][:3] for k in keys]))
            values = [_normalize_quat(k["values"][3:7]) for k in keys]
            tracks.append(AnimTrack(node, "rotation", times, values))
            if kind == "PosRotVis":
                notes.append({
                    "target": target, "path": "visibility",
                    "times_ms": [k["time"] for k in keys],
                    "values": [k["values"][7] for k in keys],
                    "reason": "glTF 2.0 has no visibility channel",
                })
        else:
            notes.append({"skipped": track, "reason": f"unknown type {kind!r}"})
    return tracks, notes


def convert_obj(path: Path, dest: Path, *, texture: Path | None = None,
                animation_paths: Sequence[Path] = (),
                include_animations_in_json: bool = True) -> list[Path]:
    """Convert a BiN ``.obj`` (with optional animations) to glTF/OBJ/JSON."""
    obj = parse_obj(path.read_bytes())
    animation_results = []
    for anim_path in animation_paths:
        animation_results.append({"source": str(anim_path),
                                  **read_anim(anim_path.read_bytes())})

    dest.mkdir(parents=True, exist_ok=True)
    source_texture = _choose_texture(
        path, (mesh["shader"] for mesh in obj["meshes"]), texture)
    png_path = _prepare_texture(source_texture, dest) if source_texture else None

    builder = GLTFBuilder(name=path.stem)
    specs, parents = _node_specs(obj, animation_results)
    node_index: dict[str, int] = {}
    bind_nodes = {name: spec["bind"] for name, spec in specs.items()}

    # glTF nodes.  Children are filled in after every referenced node exists.
    for name, spec in specs.items():
        node_index[name] = builder.add_node(
            name, translation=spec["translation"], rotation=spec["rotation"],
            extras=spec["extras"])
    for name, parent in parents.items():
        if parent and parent in node_index and parent != name:
            children = builder.nodes[node_index[parent]].setdefault("children", [])
            if node_index[name] not in children:
                children.append(node_index[name])
    roots = [index for name, index in node_index.items()
             if not parents[name] or parents[name] not in node_index]
    builder.add_default_scene(roots)

    texture_index = builder.add_texture(png_path, uri=f"textures/{png_path.name}") \
        if png_path else None
    material_index: dict[str, int] = {}
    for mesh in obj["meshes"]:
        shader = mesh["shader"] or "material"
        if shader not in material_index:
            material_index[shader] = builder.add_material(shader, texture_index)

    # Meshes, skins and their bind-pose matrices.
    world_cache: dict[str, Mat4] = {}
    skin_notes: list[dict[str, Any]] = []
    for mesh in obj["meshes"]:
        positions = [v["position"] for v in mesh["vertices"]]
        normals = [v["normal"] for v in mesh["vertices"]]
        uvs = [v["uv"] for v in mesh["vertices"]]
        indices = [index for face in mesh["faces"] for index in face]
        joints = skin_weights = None
        skin_index = None
        if mesh["type"] == 2 and mesh.get("bones") and mesh.get("weights"):
            bone_names = [bone["name"] for bone in mesh["bones"]]
            joints = [[w["bone0"], w["bone1"], 0, 0] for w in mesh["weights"]]
            skin_weights = [[w["weight"], 1.0 - w["weight"], 0.0, 0.0]
                            for w in mesh["weights"]]
            joint_indices = [node_index[name] for name in bone_names]
            inverse_matrices = [
                _rigid_inverse(_world_matrix(name, bind_nodes, world_cache))
                for name in bone_names]
            skin_index = builder.add_skin(f"{mesh['name']}_skin",
                                          joint_indices, inverse_matrices)
            skin_notes.append({
                "mesh": mesh["name"], "joints": bone_names,
                "weight_encoding": "weight applies to bone0, 1-weight to bone1",
                "quaternion_order": "x, y, z, w (inferred)",
            })
        mesh_index = builder.add_mesh(
            mesh["name"], positions, indices, normals=normals, uvs=uvs,
            material=material_index[mesh["shader"] or "material"],
            joints=joints, skin_weights=skin_weights)
        node = node_index.get(mesh["name"])
        if node is None:
            node = builder.add_node(mesh["name"], mesh=mesh_index)
        else:
            builder.nodes[node]["mesh"] = mesh_index
        if skin_index is not None:
            builder.nodes[node]["skin"] = skin_index

    # Animations (times converted from milliseconds to seconds).
    animation_notes = []
    for animation in animation_results:
        tracks, notes = _animation_tracks(animation, node_index)
        builder.add_animation(Path(animation["source"]).stem, tracks)
        animation_notes.append({"source": animation["source"],
                                "channels": len(tracks), "notes": notes})

    gltf_path = builder.write(dest, name=path.stem)
    texture_map = ({shader: f"textures/{png_path.name}"
                    for shader in material_index} if png_path else {})
    obj_path = write_obj_scene(dest / f"{path.stem}.obj", obj["meshes"],
                               texture_map)
    json_path = _write_json(dest / f"{path.stem}.json", {
        "format": "bin-obj",
        "source": str(path),
        "scene": obj,
        "conversion": {
            "gltf": gltf_path.name,
            "binary_buffer": "model.bin",
            "wavefront_obj": obj_path.name,
            "wavefront_mtl": obj_path.with_suffix(".mtl").name,
            "texture_png": f"textures/{png_path.name}" if png_path else None,
            "animations": animation_notes,
            "skins": skin_notes,
            "coordinate_system": "source coordinates preserved unchanged",
            "time_units": "animation times divided by 1000 (ms -> s)",
        },
        **({"animations": animation_results} if include_animations_in_json
           else {"animation_sources": [a["source"] for a in animation_results]}),
    })
    outputs = [gltf_path, dest / "model.bin", obj_path, json_path]
    if png_path:
        outputs.extend([obj_path.with_suffix(".mtl"), png_path])
    return outputs


# --- other formats ----------------------------------------------------------

def convert_tga(path: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    image = read_tga(path.read_bytes())
    png = tga_to_png_file(path, dest / f"{path.stem}.png")
    meta = _write_json(dest / f"{path.stem}.tga.json", {
        "format": "tga", "source": str(path),
        "width": image.width, "height": image.height,
        "pixel_depth": image.pixel_depth, "image_type": image.image_type,
        "png": png.name,
    })
    return [png, meta]


def convert_fnt(path: Path, dest: Path, *, texture: Path | None = None) -> list[Path]:
    font = read_fnt(path.read_bytes(), str(path))
    dest.mkdir(parents=True, exist_ok=True)
    candidates = [p for p in _tga_candidates(path)
                  if font["name"].lower() in p.stem.lower()]
    atlas = texture or (candidates[0] if candidates else None)
    outputs: list[Path] = []
    if atlas is not None:
        outputs.append(_prepare_texture(atlas, dest))
    meta = _write_json(dest / f"{path.stem}.json", {
        **font,
        "atlas_png": f"textures/{atlas.stem}.png" if atlas else None,
        "note": "The PNG is the original glyph atlas; glyph U/V offsets are in meters above.",
    })
    return outputs + [meta]


def convert_dsl(path: Path, dest: Path, *, kind: str) -> list[Path]:
    data = path.read_bytes()
    if kind == "model":
        parsed = parse_model(data, str(path))
    elif kind == "fx":
        parsed = parse_script(data, str(path), kind="fx")
    else:
        parsed = parse_script(data, str(path), kind="script")
    return [_write_json(dest / f"{path.stem}.json", parsed)]


def convert_bsp(path: Path, dest: Path) -> list[Path]:
    result = inspect_bsp(path.read_bytes(), str(path))
    return [_write_json(dest / f"{path.stem}.json", result)]


def convert_wav(path: Path, dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    header = parse_wav(path.read_bytes(), str(path))
    copied = _copy(path, dest / path.name)
    meta = _write_json(dest / f"{path.stem}.wav.json", header)
    return [copied, meta]


def convert_fun(path: Path, dest: Path) -> list[Path]:
    archive_dest = dest / f"{path.stem}_archive"
    with FunArchive(path) as archive:
        written = archive.extract(archive_dest)
        manifest = archive.manifest()
    meta = _write_json(archive_dest / "manifest.json", {
        "archive": str(path), "entries": manifest,
    })
    return [meta] + written[:10]


_TEXT_SUFFIXES = {".lua", ".txt", ".ini", ".cfg", ".log", ".history",
                  ".bspshaders", ".dat"}
_KNOWN_SUFFIXES = {"obj", "anim", "model", "script", "fx", "fnt", "bsp",
                   "tga", "wav", "fun"}


def sniff_format(path: Path) -> str:
    """Identify a file by content, falling back to its extension.

    Several archive entries use mangled extensions (`.anim_`, `._obj`,
    `._tga_`, `.tani`), so extension alone is not enough.
    """
    ext = path.suffix.lower().lstrip(".")
    if ext in _KNOWN_SUFFIXES:
        return ext
    data = path.read_bytes()
    if data.startswith(b"BiN\x00"):
        try:
            parse_obj(data)
            return "obj"
        except Exception:
            return "anim"
    if data.startswith(b"SFNT"):
        return "fnt"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "wav"
    if (len(data) >= 18 and data[1] == 0 and data[2] in (2, 3, 10, 11)
            and 0 < int.from_bytes(data[12:14], "little") <= 8192
            and 0 < int.from_bytes(data[14:16], "little") <= 8192):
        return "tga"
    return ext or "text"


def convert_one(path: Path, dest: Path, *, fmt: str = "auto",
                texture: Path | None = None,
                animation_paths: Sequence[Path] = (),
                nest: bool = False) -> list[Path]:
    """Convert one file; ``nest`` puts structured outputs in a stem subdirectory."""
    suffix = sniff_format(path)
    if suffix in {"obj", "anim", "model", "script", "fx", "fnt", "bsp"} \
            and nest:
        dest = dest / path.stem
    if suffix == "obj" and fmt in ("auto", "gltf", "obj"):
        return convert_obj(path, dest, texture=texture,
                           animation_paths=animation_paths)
    if suffix == "anim" and fmt in ("auto", "json"):
        return [_write_json(dest / f"{path.stem}.json",
                            {"format": "bin-anim", "source": str(path),
                             **read_anim(path.read_bytes())})]
    if suffix == "tga" and fmt in ("auto", "png"):
        return convert_tga(path, dest)
    if suffix == "fnt":
        return convert_fnt(path, dest, texture=texture)
    if suffix == "model":
        return convert_dsl(path, dest, kind="model")
    if suffix == "fx":
        return convert_dsl(path, dest, kind="fx")
    if suffix == "script":
        return convert_dsl(path, dest, kind="script")
    if suffix == "bsp":
        return convert_bsp(path, dest)
    if suffix == "wav":
        return convert_wav(path, dest)
    if suffix == "fun":
        return convert_fun(path, dest)
    if f".{suffix}" in _TEXT_SUFFIXES:
        dest.mkdir(parents=True, exist_ok=True)
        return [_copy(path, dest / path.name)]
    dest.mkdir(parents=True, exist_ok=True)
    return [_copy(path, dest / path.name)]


# --- whole-archive pipeline -------------------------------------------------

def _norm_ref(value: str) -> str:
    """Normalize a game path reference for case-insensitive lookup."""
    return value.replace("\\", "/").lstrip("./").lower()


def _iter_tree(node: Any):
    """Yield a JSON DSL tree node and all of its descendants."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_tree(value)
    elif isinstance(node, list):
        for value in node:
            yield from _iter_tree(value)


def _model_references(parsed: dict[str, Any]) -> tuple[list[str], list[tuple[str | None, str]]]:
    """Extract mesh and animation references from a parsed ``.model`` script."""
    meshes: list[str] = []
    animations: list[tuple[str | None, str]] = []
    current: str | None = None
    for node in _iter_tree(parsed.get("tree", {})):
        if node.get("kind") != "call":
            continue
        name = str(node.get("name", "")).lower()
        args = node.get("args", [])
        if name == "mesh" and args and isinstance(args[0], str):
            current = args[0]
            meshes.append(current)
        elif name == "animation" and len(args) >= 2 and isinstance(args[1], str):
            animations.append((current, args[1]))
    return meshes, animations


def _shader_textures(parsed: dict[str, Any]) -> dict[str, list[str]]:
    """Map shader function names to Texture(...) paths in a script."""
    result: dict[str, list[str]] = {}
    for node in _iter_tree(parsed.get("tree", {})):
        if node.get("kind") != "function":
            continue
        textures: list[str] = []
        for child in _iter_tree(node.get("body", [])):
            if child.get("kind") != "call":
                continue
            call = str(child.get("name", "")).lower()
            args = child.get("args", [])
            if call == "texture" and args and isinstance(args[0], str):
                textures.append(args[0])
            elif call == "textureanim" and len(args) > 1 and isinstance(args[1], str):
                textures.append(args[1])
        if textures:
            result[str(node.get("name", "")).upper()] = textures
    return result


def _make_resolver(index: dict[str, Path]):
    def resolve(reference: str | None) -> Path | None:
        if not reference or not isinstance(reference, str):
            return None
        key = _norm_ref(reference)
        if key in index:
            return index[key]
        basename = key.rsplit("/", 1)[-1]
        matches = [path for norm, path in index.items()
                   if norm.rsplit("/", 1)[-1] == basename]
        return matches[0] if len(matches) == 1 else None
    return resolve


def convert_archive(path: str | Path, out: str | Path) -> list[Path]:
    """Unpack a ``.fun`` archive and convert every entry to open formats.

    Mesh/animation/texture relationships are recovered from the archive's own
    ``.model`` and shader ``.script`` files, so a single archive is enough
    input.  Original proprietary files are extracted to a temporary directory
    and are not copied into the output tree.
    """
    import tempfile

    path = Path(path)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    errors: list[dict[str, str]] = []
    unconverted: list[dict[str, str]] = []
    counts: dict[str, int] = {}

    def emit(kind: str, paths: Sequence[Path]) -> None:
        counts[kind] = counts.get(kind, 0) + 1
        outputs.extend(paths)

    with tempfile.TemporaryDirectory(prefix="razor_extract_") as temp:
        extracted = Path(temp)
        with FunArchive(path) as archive:
            written_paths = archive.extract(extracted)

        index = {_norm_ref(str(p.relative_to(extracted))): p
                 for p in extracted.rglob("*") if p.is_file()}
        resolve = _make_resolver(index)

        # Shaders -> texture paths, and models -> mesh/animation paths.
        shader_map: dict[str, list[Path]] = {}
        for script in sorted(extracted.rglob("*")):
            if not script.is_file() or script.suffix.lower() not in (".script", ".fx"):
                continue
            try:
                parsed = parse_script(script.read_bytes(), str(script))
            except Exception:
                continue
            for shader, refs in _shader_textures(parsed).items():
                paths = [p for ref in refs if (p := resolve(ref)) is not None]
                if paths:
                    shader_map.setdefault(shader, []).extend(paths)

        mesh_animations: dict[str, list[Path]] = {}
        for model in sorted(extracted.rglob("*")):
            if not model.is_file() or model.suffix.lower() != ".model":
                continue
            try:
                parsed = parse_model(model.read_bytes(), str(model))
            except Exception:
                continue
            _, animations = _model_references(parsed)
            for mesh_ref, anim_ref in animations:
                if mesh_ref is None:
                    continue
                mesh = resolve(mesh_ref)
                anim = resolve(anim_ref)
                if mesh is not None and anim is not None:
                    key = _norm_ref(str(mesh.relative_to(extracted)))
                    bucket = mesh_animations.setdefault(key, [])
                    if anim not in bucket:
                        bucket.append(anim)

        # Textures first: the PNG paths are reused by mesh conversions.
        png_by_source: dict[Path, Path] = {}
        for source in written_paths:
            if not source.is_file() or sniff_format(source) != "tga":
                continue
            relative = source.relative_to(extracted)
            try:
                produced = convert_tga(source, out / "textures" / relative.parent)
                emit("tga", produced)
                png_by_source[source.resolve()] = produced[0]
            except Exception as exc:
                errors.append({"file": str(relative), "error": str(exc)})

        # Every remaining entry, each in its own open-format subtree.
        for source in written_paths:
            if not source.is_file():
                continue
            relative = source.relative_to(extracted)
            kind = sniff_format(source)
            if kind == "tga":
                continue
            try:
                if kind == "obj":
                    from .binfile import parse_obj as _parse_obj
                    shaders = [m.get("shader", "") for m in
                               _parse_obj(source.read_bytes())["meshes"]]
                    texture: Path | None = None
                    for shader in shaders:
                        for candidate in shader_map.get(str(shader).upper(), []):
                            texture = png_by_source.get(candidate.resolve(), candidate)
                            break
                        if texture is not None:
                            break
                    key = _norm_ref(str(relative))
                    animations = mesh_animations.get(key, [])
                    dest = out / "models" / relative.parent / source.stem
                    emit("obj", convert_obj(
                        source, dest, texture=texture, animation_paths=animations,
                        include_animations_in_json=False))
                elif kind == "anim":
                    emit("anim", convert_one(
                        source, out / "animations" / relative.parent))
                elif kind == "model" or kind in ("script", "fx"):
                    emit(kind, convert_one(
                        source, out / "scripts" / relative.parent))
                elif kind == "bsp":
                    emit("bsp", convert_one(
                        source, out / "levels" / relative.parent))
                elif kind == "wav":
                    emit("wav", convert_one(
                        source, out / "audio" / relative.parent))
                elif kind == "fnt":
                    emit("fnt", convert_one(
                        source, out / "fonts" / relative.parent))
                elif kind == "fun":
                    emit("fun", convert_fun(
                        source, out / "archives" / relative.parent))
                else:
                    if _looks_binary(source.read_bytes()):
                        unconverted.append({
                            "file": str(relative),
                            "reason": "unrecognised binary; no open-format equivalent",
                        })
                        continue
                    emit("other", convert_one(
                        source, out / "other" / relative.parent))
            except Exception as exc:
                errors.append({"file": str(relative), "error": str(exc)})

        _rewrite_temp_paths(out, extracted, path.name)

    manifest = _write_json(out / "conversion-manifest.json", {
        "archive": str(path),
        "archive_bytes": path.stat().st_size,
        "entries": len(written_paths),
        "counts": counts,
        "outputs": [str(p.relative_to(out)) for p in outputs],
        "errors": errors,
        "unconverted": unconverted,
        "notes": [
            "Original proprietary files were extracted to a temporary directory and removed.",
            "Mesh/animation links come from .model scripts; texture links from shader .script files.",
            "Source paths in metadata are archive-relative; scratch paths are stripped.",
        ],
    })
    return [manifest] + outputs


def convert_path(source: str | Path, out: str | Path, *, fmt: str = "auto",
                 texture: str | Path | None = None,
                 animations: Sequence[str | Path] = ()) -> list[Path]:
    """Convert a file or a directory tree, returning all output paths."""
    source = Path(source)
    out = Path(out)
    texture_path = Path(texture) if texture else None
    animation_paths = [Path(p) for p in animations]
    if source.is_file():
        return convert_one(source, out, fmt=fmt, texture=texture_path,
                           animation_paths=animation_paths)
    if not source.is_dir():
        raise FileNotFoundError(source)
    outputs: list[Path] = []
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        relative = path.relative_to(source)
        dest = out / relative.parent
        outputs.extend(convert_one(path, dest, fmt=fmt, nest=True))
    return outputs
