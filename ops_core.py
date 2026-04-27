from __future__ import annotations

import io
import json
import math
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageSequence

APP_NAME = "Open Pixel Studio Web"
PROJECT_VERSION = 1
PROJECT_EXTENSION = ".opsprite"
DEFAULT_FRAME_DURATION = 100

DEFAULT_PALETTE = [
    "#000000", "#1d1d1d", "#3b3b3b", "#5d5d5d", "#7f7f7f", "#a6a6a6", "#d3d3d3", "#ffffff",
    "#140c1c", "#442434", "#6d2d4b", "#905ea9", "#c85d9c", "#e39aac", "#f7d6e0", "#fff1e8",
    "#3f1f0f", "#6b3e26", "#9a5938", "#c97b4a", "#e6a167", "#f0c98b", "#f5dfb0", "#fff4d6",
    "#1a3a20", "#2d5b3b", "#3f7d4b", "#5aa05a", "#7bc96f", "#b2d98c", "#d9edb3", "#eef7d2",
    "#0f233a", "#1d3f5f", "#2b5d84", "#3a7fb5", "#4aa8d8", "#86cce8", "#b8e8f5", "#def7ff",
    "#2a1438", "#4b2372", "#6b35ad", "#8b63d1", "#b388eb", "#d3b7f7", "#eadcff", "#f7f2ff",
]


def clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def normalize_box(x0: int, y0: int, x1: int, y1: int) -> Tuple[int, int, int, int]:
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def box_to_xywh(box: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def xywh_to_box(x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
    return x, y, x + max(0, w - 1), y + max(0, h - 1)


def point_in_xywh(x: int, y: int, rect: Tuple[int, int, int, int]) -> bool:
    rx, ry, rw, rh = rect
    return rx <= x < rx + rw and ry <= y < ry + rh


def rgba_to_hex(color: Tuple[int, int, int, int] | Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % (color[0], color[1], color[2])


def hex_to_rgba(value: str, alpha: int = 255) -> Tuple[int, int, int, int]:
    value = value.strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) != 6:
        raise ValueError(f"Invalid color: {value}")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16), alpha


def unique_name(base: str, existing: Sequence[str]) -> str:
    if base not in existing:
        return base
    i = 2
    while f"{base} {i}" in existing:
        i += 1
    return f"{base} {i}"


def image_has_pixels(image: Image.Image) -> bool:
    alpha = image.getchannel("A")
    return alpha.getbbox() is not None


def make_blank(size: Tuple[int, int]) -> Image.Image:
    return Image.new("RGBA", size, (0, 0, 0, 0))


def adjust_image_alpha(image: Image.Image, opacity: int) -> Image.Image:
    if opacity >= 255:
        return image
    out = image.copy()
    alpha = out.getchannel("A")
    alpha = alpha.point(lambda p: int(p * opacity / 255))
    out.putalpha(alpha)
    return out


def tint_image(
    image: Image.Image,
    tint_rgb: Tuple[int, int, int],
    blend_amount: float,
    alpha_scale: float,
) -> Image.Image:
    if image.size[0] <= 0 or image.size[1] <= 0:
        return image.copy()
    overlay = Image.new("RGBA", image.size, tint_rgb + (255,))
    out = Image.blend(image.convert("RGBA"), overlay, blend_amount)
    alpha = image.getchannel("A")
    alpha = alpha.point(lambda p: int(p * alpha_scale))
    out.putalpha(alpha)
    return out


def translate_image(image: Image.Image, dx: int, dy: int) -> Image.Image:
    w, h = image.size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    src_x0 = max(0, -dx)
    src_y0 = max(0, -dy)
    src_x1 = min(w, w - dx) if dx >= 0 else w
    src_y1 = min(h, h - dy) if dy >= 0 else h
    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return out
    crop = image.crop((src_x0, src_y0, src_x1, src_y1))
    dst_x = max(0, dx)
    dst_y = max(0, dy)
    out.paste(crop, (dst_x, dst_y), crop)
    return out


def draw_brush(image: Image.Image, x: int, y: int, color: Tuple[int, int, int, int], size: int) -> None:
    draw = ImageDraw.Draw(image)
    half = (size - 1) // 2
    x0 = x - half
    y0 = y - half
    x1 = x0 + size - 1
    y1 = y0 + size - 1
    draw.rectangle((x0, y0, x1, y1), fill=color)


def bresenham_points(x0: int, y0: int, x1: int, y1: int) -> List[Tuple[int, int]]:
    points: List[Tuple[int, int]] = []
    dx = abs(x1 - x0)
    dy = -abs(y1 - y0)
    sx = 1 if x0 < x1 else -1
    sy = 1 if y0 < y1 else -1
    err = dx + dy
    while True:
        points.append((x0, y0))
        if x0 == x1 and y0 == y1:
            break
        e2 = 2 * err
        if e2 >= dy:
            err += dy
            x0 += sx
        if e2 <= dx:
            err += dx
            y0 += sy
    return points


def draw_line_brush(
    image: Image.Image,
    p0: Tuple[int, int],
    p1: Tuple[int, int],
    color: Tuple[int, int, int, int],
    size: int,
) -> None:
    for x, y in bresenham_points(p0[0], p0[1], p1[0], p1[1]):
        draw_brush(image, x, y, color, size)


def flood_fill(image: Image.Image, x: int, y: int, color: Tuple[int, int, int, int]) -> None:
    w, h = image.size
    if not (0 <= x < w and 0 <= y < h):
        return
    pixels = image.load()
    target = pixels[x, y]
    if target == color:
        return
    stack = [(x, y)]
    while stack:
        px, py = stack.pop()
        if px < 0 or py < 0 or px >= w or py >= h:
            continue
        if pixels[px, py] != target:
            continue
        pixels[px, py] = color
        stack.append((px + 1, py))
        stack.append((px - 1, py))
        stack.append((px, py + 1))
        stack.append((px, py - 1))


def extract_palette_from_image(image: Image.Image, limit: int = 64) -> List[str]:
    seen: List[str] = []
    rgba = image.convert("RGBA")
    for pixel in rgba.getdata():
        if pixel[3] == 0:
            continue
        hex_value = rgba_to_hex(pixel)
        if hex_value not in seen:
            seen.append(hex_value)
        if len(seen) >= limit:
            break
    return seen or DEFAULT_PALETTE[:]


@dataclass
class TagInfo:
    name: str
    start: int
    end: int
    direction: str = "forward"
    color: str = "#ffcc00"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "start": self.start,
            "end": self.end,
            "direction": self.direction,
            "color": self.color,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TagInfo":
        return cls(
            name=data.get("name", "Tag"),
            start=int(data.get("start", 0)),
            end=int(data.get("end", 0)),
            direction=data.get("direction", "forward"),
            color=data.get("color", "#ffcc00"),
        )


@dataclass
class SliceInfo:
    name: str
    x: int
    y: int
    w: int
    h: int
    color: str = "#00ffff"
    data: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "color": self.color,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SliceInfo":
        return cls(
            name=data.get("name", "Slice"),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            w=int(data.get("w", 1)),
            h=int(data.get("h", 1)),
            color=data.get("color", "#00ffff"),
            data=data.get("data", ""),
        )


@dataclass
class Layer:
    name: str
    visible: bool = True
    locked: bool = False
    opacity: int = 255
    background: bool = False
    cels: List[Image.Image] = field(default_factory=list)

    def clone(self) -> "Layer":
        return Layer(
            name=self.name,
            visible=self.visible,
            locked=self.locked,
            opacity=self.opacity,
            background=self.background,
            cels=[img.copy() for img in self.cels],
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "visible": self.visible,
            "locked": self.locked,
            "opacity": self.opacity,
            "background": self.background,
        }

    @classmethod
    def from_dict(cls, data: dict, cels: List[Image.Image]) -> "Layer":
        return cls(
            name=data.get("name", "Layer"),
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            opacity=int(data.get("opacity", 255)),
            background=bool(data.get("background", False)),
            cels=cels,
        )


class SpriteProject:
    def __init__(
        self,
        width: int,
        height: int,
        frame_durations: Optional[List[int]] = None,
        layers: Optional[List[Layer]] = None,
        palette: Optional[List[str]] = None,
        tags: Optional[List[TagInfo]] = None,
        slices: Optional[List[SliceInfo]] = None,
    ):
        self.width = int(width)
        self.height = int(height)
        self.frame_durations = frame_durations or [DEFAULT_FRAME_DURATION]
        self.layers = layers or []
        self.palette = palette or DEFAULT_PALETTE[:]
        self.tags = tags or []
        self.slices = slices or []

    @classmethod
    def create(cls, width: int, height: int, frames: int = 1) -> "SpriteProject":
        frames = max(1, int(frames))
        base_layer = Layer(
            name="Layer 1",
            cels=[Image.new("RGBA", (width, height), (0, 0, 0, 0)) for _ in range(frames)],
        )
        return cls(width=width, height=height, frame_durations=[DEFAULT_FRAME_DURATION] * frames, layers=[base_layer])

    @property
    def frame_count(self) -> int:
        return len(self.frame_durations)

    @property
    def size(self) -> Tuple[int, int]:
        return self.width, self.height

    def clone(self) -> "SpriteProject":
        return SpriteProject(
            width=self.width,
            height=self.height,
            frame_durations=self.frame_durations[:],
            layers=[layer.clone() for layer in self.layers],
            palette=self.palette[:],
            tags=[TagInfo.from_dict(tag.to_dict()) for tag in self.tags],
            slices=[SliceInfo.from_dict(s.to_dict()) for s in self.slices],
        )

    def get_cel(self, layer_index: int, frame_index: int) -> Image.Image:
        return self.layers[layer_index].cels[frame_index]

    def set_cel(self, layer_index: int, frame_index: int, image: Image.Image) -> None:
        self.layers[layer_index].cels[frame_index] = image.convert("RGBA")

    def flatten_frame(
        self,
        frame_index: int,
        preview_layer_index: Optional[int] = None,
        preview_image: Optional[Image.Image] = None,
        visible_only: bool = True,
    ) -> Image.Image:
        out = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        for i, layer in enumerate(self.layers):
            if visible_only and not layer.visible:
                continue
            cel = preview_image if preview_layer_index == i and preview_image is not None else layer.cels[frame_index]
            composed = adjust_image_alpha(cel, layer.opacity)
            out = Image.alpha_composite(out, composed)
        return out

    def add_layer(self, index: Optional[int] = None, name: Optional[str] = None) -> int:
        existing = [layer.name for layer in self.layers]
        if name is None:
            name = unique_name("Layer", existing)
        layer = Layer(name=name, cels=[Image.new("RGBA", self.size, (0, 0, 0, 0)) for _ in range(self.frame_count)])
        if index is None:
            index = len(self.layers)
        self.layers.insert(index, layer)
        return index

    def remove_layer(self, index: int) -> None:
        if len(self.layers) <= 1:
            raise ValueError("Project must contain at least one layer")
        del self.layers[index]

    def move_layer(self, index: int, new_index: int) -> int:
        new_index = clamp(new_index, 0, len(self.layers) - 1)
        layer = self.layers.pop(index)
        self.layers.insert(new_index, layer)
        return new_index

    def add_frame(self, index: Optional[int] = None, copy_from: Optional[int] = None) -> int:
        if index is None:
            index = self.frame_count
        index = clamp(index, 0, self.frame_count)
        for layer in self.layers:
            if copy_from is None:
                new_image = Image.new("RGBA", self.size, (0, 0, 0, 0))
            else:
                new_image = layer.cels[copy_from].copy()
            layer.cels.insert(index, new_image)
        duration = self.frame_durations[copy_from] if copy_from is not None else DEFAULT_FRAME_DURATION
        self.frame_durations.insert(index, duration)
        return index

    def remove_frame(self, index: int) -> None:
        if self.frame_count <= 1:
            raise ValueError("Project must contain at least one frame")
        for layer in self.layers:
            del layer.cels[index]
        del self.frame_durations[index]
        kept_tags: List[TagInfo] = []
        for tag in self.tags:
            if tag.end < index:
                kept_tags.append(tag)
                continue
            if tag.start > index:
                kept_tags.append(TagInfo(tag.name, tag.start - 1, tag.end - 1, tag.direction, tag.color))
                continue
            if tag.start == tag.end == index:
                continue
            new_start = tag.start
            new_end = tag.end - 1
            if tag.start > index:
                new_start -= 1
            if new_start <= new_end:
                kept_tags.append(TagInfo(tag.name, new_start, new_end, tag.direction, tag.color))
        self.tags = kept_tags

    def clear_cel(self, layer_index: int, frame_index: int) -> None:
        self.layers[layer_index].cels[frame_index] = Image.new("RGBA", self.size, (0, 0, 0, 0))

    def cel_has_pixels(self, layer_index: int, frame_index: int) -> bool:
        return image_has_pixels(self.layers[layer_index].cels[frame_index])

    def to_zip_bytes(self) -> bytes:
        manifest = {
            "version": PROJECT_VERSION,
            "width": self.width,
            "height": self.height,
            "frame_durations": self.frame_durations,
            "palette": self.palette,
            "tags": [tag.to_dict() for tag in self.tags],
            "slices": [slc.to_dict() for slc in self.slices],
            "layers": [],
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for layer_index, layer in enumerate(self.layers):
                files = []
                for frame_index, image in enumerate(layer.cels):
                    file_name = f"cels/l{layer_index}_f{frame_index}.png"
                    image_bytes = io.BytesIO()
                    image.save(image_bytes, format="PNG")
                    zf.writestr(file_name, image_bytes.getvalue())
                    files.append(file_name)
                layer_payload = layer.to_dict()
                layer_payload["files"] = files
                manifest["layers"].append(layer_payload)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        return buf.getvalue()

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_zip_bytes())

    @classmethod
    def from_zip_bytes(cls, data: bytes) -> "SpriteProject":
        with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            width = int(manifest["width"])
            height = int(manifest["height"])
            frame_durations = [int(x) for x in manifest.get("frame_durations", [DEFAULT_FRAME_DURATION])]
            layers: List[Layer] = []
            for layer_data in manifest.get("layers", []):
                cels = []
                for file_name in layer_data.get("files", []):
                    image = Image.open(io.BytesIO(zf.read(file_name))).convert("RGBA")
                    if image.size != (width, height):
                        image = image.resize((width, height), Image.Resampling.NEAREST)
                    cels.append(image)
                if not cels:
                    cels = [Image.new("RGBA", (width, height), (0, 0, 0, 0)) for _ in range(len(frame_durations))]
                layers.append(Layer.from_dict(layer_data, cels))
        if not layers:
            layers = [Layer(name="Layer 1", cels=[Image.new("RGBA", (width, height), (0, 0, 0, 0)) for _ in range(len(frame_durations))])]
        tags = [TagInfo.from_dict(tag) for tag in manifest.get("tags", [])]
        slices = [SliceInfo.from_dict(slc) for slc in manifest.get("slices", [])]
        palette = manifest.get("palette") or DEFAULT_PALETTE[:]
        return cls(width, height, frame_durations, layers, palette, tags, slices)

    @classmethod
    def load(cls, path: str | Path) -> "SpriteProject":
        return cls.from_zip_bytes(Path(path).read_bytes())


def project_from_image_bytes(data: bytes, name: str = "imported") -> SpriteProject:
    image = Image.open(io.BytesIO(data))
    base = image.convert("RGBA")
    width, height = base.size
    frames: List[Image.Image] = []
    durations: List[int] = []
    if getattr(image, "is_animated", False):
        for frame in ImageSequence.Iterator(image):
            rgba = frame.convert("RGBA")
            if rgba.size != (width, height):
                rgba = rgba.resize((width, height), Image.Resampling.NEAREST)
            frames.append(rgba)
            durations.append(int(frame.info.get("duration", DEFAULT_FRAME_DURATION)))
    else:
        frames = [base]
        durations = [DEFAULT_FRAME_DURATION]
    project = SpriteProject.create(width, height, len(frames))
    project.layers[0].name = name
    project.layers[0].cels = [frame.copy() for frame in frames]
    project.frame_durations = durations
    project.palette = extract_palette_from_image(frames[0])
    return project


def project_from_sprite_sheet_bytes(
    data: bytes,
    cell_w: int,
    cell_h: int,
    padding: int = 0,
    offset_x: int = 0,
    offset_y: int = 0,
    name: str = "sheet",
) -> SpriteProject:
    image = Image.open(io.BytesIO(data)).convert("RGBA")
    frames: List[Image.Image] = []
    y = offset_y
    while y + cell_h <= image.height:
        x = offset_x
        while x + cell_w <= image.width:
            frame = image.crop((x, y, x + cell_w, y + cell_h)).convert("RGBA")
            frames.append(frame)
            x += cell_w + padding
        y += cell_h + padding
    if not frames:
        raise ValueError("No frames found with the provided sprite-sheet settings")
    project = SpriteProject.create(cell_w, cell_h, len(frames))
    project.layers[0].name = name
    project.layers[0].cels = [frame.copy() for frame in frames]
    project.palette = extract_palette_from_image(frames[0])
    return project


def build_sprite_sheet(
    project: SpriteProject,
    frame_indices: Optional[List[int]] = None,
    columns: Optional[int] = None,
    padding: int = 0,
) -> Tuple[Image.Image, dict]:
    frame_indices = frame_indices or list(range(project.frame_count))
    if not frame_indices:
        raise ValueError("No frames to export")
    columns = max(1, columns or len(frame_indices))
    rows = math.ceil(len(frame_indices) / columns)
    sheet_w = columns * project.width + max(0, columns - 1) * padding
    sheet_h = rows * project.height + max(0, rows - 1) * padding
    sheet = Image.new("RGBA", (sheet_w, sheet_h), (0, 0, 0, 0))
    frames_meta = {}
    exported_positions: dict[int, dict] = {}
    for order_index, frame_index in enumerate(frame_indices):
        row = order_index // columns
        col = order_index % columns
        x = col * (project.width + padding)
        y = row * (project.height + padding)
        flattened = project.flatten_frame(frame_index)
        sheet.paste(flattened, (x, y), flattened)
        key = f"frame_{frame_index:03d}"
        frames_meta[key] = {
            "frame": {"x": x, "y": y, "w": project.width, "h": project.height},
            "duration": project.frame_durations[frame_index],
            "source_frame": frame_index,
        }
        exported_positions[frame_index] = {"x": x, "y": y}
    frame_tags = []
    for tag in project.tags:
        if tag.start in exported_positions and tag.end in exported_positions:
            exported_indices = [i for i, source_index in enumerate(frame_indices) if tag.start <= source_index <= tag.end]
            if exported_indices:
                frame_tags.append(
                    {
                        "name": tag.name,
                        "from": min(exported_indices),
                        "to": max(exported_indices),
                        "direction": tag.direction,
                        "color": tag.color,
                    }
                )
    meta = {
        "frames": frames_meta,
        "meta": {
            "app": APP_NAME,
            "size": {"w": sheet_w, "h": sheet_h},
            "frameTags": frame_tags,
            "slices": [slc.to_dict() for slc in project.slices],
        },
    }
    return sheet, meta


def export_gif_bytes(project: SpriteProject, frame_indices: Optional[List[int]] = None) -> bytes:
    frame_indices = frame_indices or list(range(project.frame_count))
    rgba_frames = [project.flatten_frame(i) for i in frame_indices]
    pal_frames = [frame.convert("RGBA").convert("P", palette=Image.Palette.ADAPTIVE, colors=255) for frame in rgba_frames]
    durations = [project.frame_durations[i] for i in frame_indices]
    out = io.BytesIO()
    pal_frames[0].save(
        out,
        format="GIF",
        save_all=True,
        append_images=pal_frames[1:],
        duration=durations,
        loop=0,
        disposal=2,
        transparency=0,
        optimize=False,
    )
    return out.getvalue()


def export_png_bytes(project: SpriteProject, frame_index: int) -> bytes:
    out = io.BytesIO()
    project.flatten_frame(frame_index).save(out, format="PNG")
    return out.getvalue()


def export_sprite_sheet_bundle_bytes(
    project: SpriteProject,
    frame_indices: Optional[List[int]] = None,
    columns: Optional[int] = None,
    padding: int = 0,
) -> bytes:
    sheet, meta = build_sprite_sheet(project, frame_indices=frame_indices, columns=columns, padding=padding)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        img_bytes = io.BytesIO()
        sheet.save(img_bytes, format="PNG")
        zf.writestr("sprite_sheet.png", img_bytes.getvalue())
        zf.writestr("sprite_sheet.json", json.dumps(meta, indent=2))
    return out.getvalue()
