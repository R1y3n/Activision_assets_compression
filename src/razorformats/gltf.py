"""Small dependency-free glTF 2.0 writer.

Only the subset needed for converted game models is implemented: indexed
triangle meshes with POSITION/NORMAL/TEXCOORD_0 attributes, PBR materials with
a base-colour PNG texture, node hierarchies and LINEAR/STEP animation tracks.
Binary vertex data is written to a separate `.bin` buffer, JSON to `.gltf`.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

COMPONENT_TYPES = {
    "float": 5126,
    "uchar": 5121,
    "ushort": 5123,
    "uint": 5125,
    "short": 5122,
}
TYPE_SIZES = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


@dataclass
class AnimTrack:
    """One animated node transform channel."""

    node: int
    path: str  # translation | rotation | scale
    times: Sequence[float]
    values: Sequence[Sequence[float]]
    interpolation: str = "LINEAR"


@dataclass
class GLTFBuilder:
    name: str = "model"
    buffer: bytearray = field(default_factory=bytearray)
    buffer_views: list[dict[str, Any]] = field(default_factory=list)
    accessors: list[dict[str, Any]] = field(default_factory=list)
    meshes: list[dict[str, Any]] = field(default_factory=list)
    nodes: list[dict[str, Any]] = field(default_factory=list)
    materials: list[dict[str, Any]] = field(default_factory=list)
    skins: list[dict[str, Any]] = field(default_factory=list)
    animations: list[dict[str, Any]] = field(default_factory=list)
    textures: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, Any]] = field(default_factory=list)
    scenes: list[dict[str, Any]] = field(default_factory=list)
    _image_paths: dict[str, int] = field(default_factory=dict)

    def _view(self, data: bytes, target: int | None = None) -> int:
        while len(self.buffer) % 4:
            self.buffer.append(0)
        offset = len(self.buffer)
        self.buffer.extend(data)
        view: dict[str, Any] = {"buffer": 0, "byteOffset": offset,
                                "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        self.buffer_views.append(view)
        return len(self.buffer_views) - 1

    def add_accessor(
        self,
        values: Iterable[Sequence[float]] | Sequence[int],
        component_type: str,
        gltf_type: str,
        *,
        target: int | None = None,
        normalized: bool = False,
    ) -> int:
        """Add a typed accessor; `values` is a flat int list or rows of floats."""
        fmt = {"float": "f", "uchar": "B", "ushort": "H", "uint": "I",
               "short": "h"}[component_type]
        rows = [v if isinstance(v, (list, tuple)) else [v] for v in values]
        flat = [x for row in rows for x in row]
        comps = TYPE_SIZES[gltf_type]
        if len(flat) % comps:
            raise ValueError(f"accessor data length {len(flat)} is not a multiple of {comps}")
        data = struct.pack("<" + fmt * len(flat), *flat)
        view = self._view(data, target)
        accessor: dict[str, Any] = {
            "bufferView": view,
            "componentType": COMPONENT_TYPES[component_type],
            "count": len(flat) // comps,
            "type": gltf_type,
        }
        if normalized:
            accessor["normalized"] = True
        if component_type == "float":
            accessor["min"] = [min(row[i] for row in rows) for i in range(comps)]
            accessor["max"] = [max(row[i] for row in rows) for i in range(comps)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def add_texture(self, png_path: str | Path,
                    uri: str | None = None) -> int | None:
        """Register an external PNG image; returns a glTF texture index."""
        key = str(png_path).replace("\\", "/")
        if key in self._image_paths:
            return self._image_paths[key]
        self.images.append({"uri": uri or key.rsplit("/", 1)[-1]})
        self.textures.append({"source": len(self.images) - 1})
        index = len(self.textures) - 1
        self._image_paths[key] = index
        return index

    def add_material(self, name: str, texture_index: int | None = None,
                     double_sided: bool = True) -> int:
        material: dict[str, Any] = {"name": name,
                                    "pbrMetallicRoughness": {
                                        "baseColorFactor": [1, 1, 1, 1],
                                        "metallicFactor": 0.0,
                                        "roughnessFactor": 1.0,
                                    }}
        if texture_index is not None:
            material["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": texture_index}
        if double_sided:
            material["doubleSided"] = True
        self.materials.append(material)
        return len(self.materials) - 1

    def add_mesh(
        self,
        name: str,
        positions: Sequence[Sequence[float]],
        indices: Sequence[int],
        normals: Sequence[Sequence[float]] | None = None,
        uvs: Sequence[Sequence[float]] | None = None,
        material: int | None = None,
        joints: Sequence[Sequence[int]] | None = None,
        skin_weights: Sequence[Sequence[float]] | None = None,
    ) -> int:
        pos_acc = self.add_accessor(positions, "float", "VEC3", target=34962)
        idx_acc = (self.add_accessor(indices, "ushort", "SCALAR", target=34963)
                   if max(indices, default=0) < 65536 else
                   self.add_accessor(indices, "uint", "SCALAR", target=34963))
        attrs: dict[str, int] = {"POSITION": pos_acc}
        if normals and len(normals) == len(positions):
            attrs["NORMAL"] = self.add_accessor(normals, "float", "VEC3", target=34962)
        if uvs and len(uvs) == len(positions):
            attrs["TEXCOORD_0"] = self.add_accessor(uvs, "float", "VEC2", target=34962)
        if joints and len(joints) == len(positions):
            attrs["JOINTS_0"] = self.add_accessor(joints, "uchar", "VEC4", target=34962)
        if skin_weights and len(skin_weights) == len(positions):
            attrs["WEIGHTS_0"] = self.add_accessor(skin_weights, "float", "VEC4", target=34962)
        primitive: dict[str, Any] = {"attributes": attrs, "indices": idx_acc,
                                     "mode": 4}
        if material is not None:
            primitive["material"] = material
        self.meshes.append({"name": name, "primitives": [primitive]})
        return len(self.meshes) - 1

    def add_skin(self, name: str, joints: Sequence[int],
                 inverse_bind_matrices: Sequence[Sequence[float]]) -> int:
        """Register a skin; matrices are column-major 4x4 bind-pose inverses."""
        if len(joints) != len(inverse_bind_matrices):
            raise ValueError("joint/matrix count mismatch")
        matrices = self.add_accessor(inverse_bind_matrices, "float", "MAT4")
        self.skins.append({"name": name, "joints": list(joints),
                           "inverseBindMatrices": matrices})
        return len(self.skins) - 1

    def add_node(self, name: str, mesh: int | None = None,
                 children: Sequence[int] | None = None,
                 translation: Sequence[float] | None = None,
                 rotation: Sequence[float] | None = None,
                 scale: Sequence[float] | None = None,
                 extras: dict[str, Any] | None = None) -> int:
        node: dict[str, Any] = {"name": name}
        if mesh is not None:
            node["mesh"] = mesh
        if children:
            node["children"] = list(children)
        for key, value in (("translation", translation), ("rotation", rotation),
                           ("scale", scale)):
            if value is not None:
                node[key] = list(value)
        if extras:
            node["extras"] = extras
        self.nodes.append(node)
        return len(self.nodes) - 1

    def add_animation(self, name: str, tracks: Sequence[AnimTrack]) -> int | None:
        samplers: list[dict[str, Any]] = []
        channels: list[dict[str, Any]] = []
        comps = {"translation": 3, "scale": 3, "rotation": 4}
        for track in tracks:
            if not track.times or track.node < 0 or track.path not in comps:
                continue
            count = comps[track.path]
            values = [list(v) + [0.0] * (count - len(v)) for v in track.values]
            if len(values) != len(track.times):
                raise ValueError("animation time/value count mismatch")
            inp = self.add_accessor(track.times, "float", "SCALAR")
            out = self.add_accessor(values, "float",
                                    {3: "VEC3", 4: "VEC4"}[count])
            samplers.append({"input": inp, "output": out,
                             "interpolation": track.interpolation})
            channels.append({"sampler": len(samplers) - 1,
                             "target": {"node": track.node, "path": track.path}})
        if not channels:
            return None
        self.animations.append({"name": name, "samplers": samplers,
                                "channels": channels})
        return len(self.animations) - 1

    def add_default_scene(self, roots: Sequence[int]) -> None:
        self.scenes.append({"nodes": list(roots)})

    def to_dict(self) -> dict[str, Any]:
        root: dict[str, Any] = {
            "asset": {"version": "2.0", "generator": "razor-openformats"},
            "scene": 0 if self.scenes else None,
            "scenes": self.scenes,
            "nodes": self.nodes,
            "meshes": self.meshes,
            "materials": self.materials,
            "skins": self.skins,
            "animations": self.animations,
            "accessors": self.accessors,
            "bufferViews": self.buffer_views,
            "buffers": [{"byteLength": len(self.buffer), "uri": "model.bin"}],
            "images": self.images,
            "textures": self.textures,
        }
        return {key: value for key, value in root.items()
                if value is not None and value != []}

    def write(self, directory: str | Path, *, name: str | None = None,
              copy_images: bool = True) -> Path:
        """Write `<directory>/<name>.gltf`, `model.bin` and referenced PNGs."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        name = name or self.name
        for path_str, texture_index in self._image_paths.items():
            src = Path(path_str)
            if copy_images and src.is_file():
                uri = self.images[texture_index]["uri"]
                dst = directory / uri
                dst.parent.mkdir(parents=True, exist_ok=True)
                if src.resolve() != dst.resolve():
                    dst.write_bytes(src.read_bytes())
        gltf_path = directory / f"{name}.gltf"
        gltf_path.write_text(json.dumps(self.to_dict(), indent=2) + "\n",
                             encoding="utf-8")
        (directory / "model.bin").write_bytes(bytes(self.buffer))
        return gltf_path
