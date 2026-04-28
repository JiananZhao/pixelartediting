from __future__ import annotations

import base64
import io
import json
from typing import Any, Tuple

import streamlit as st
from PIL import Image, ImageDraw
from streamlit_image_coordinates import streamlit_image_coordinates

from ops_core import (
    APP_NAME,
    PROJECT_EXTENSION,
    DEFAULT_PALETTE,
    SliceInfo,
    SpriteProject,
    adjust_image_alpha,
    box_to_xywh,
    build_sprite_sheet,
    clamp,
    draw_line_brush,
    export_gif_bytes,
    export_png_bytes,
    export_sprite_sheet_bundle_bytes,
    flood_fill,
    hex_to_rgba,
    normalize_box,
    project_from_image_bytes,
    project_from_sprite_sheet_bytes,
    rgba_to_hex,
    tint_image,
    translate_image,
    unique_name,
)
from pixel_canvas_component import has_live_pixel_canvas, pixel_canvas

MAX_HISTORY = 20
TOOLS = ["pencil", "eraser", "fill", "eyedropper", "line", "rect", "ellipse", "move", "slice"]


def init_state() -> None:
    if "project" not in st.session_state:
        st.session_state.project = SpriteProject.create(32, 32, 1)
    if "current_frame" not in st.session_state:
        st.session_state.current_frame = 0
    if "current_layer" not in st.session_state:
        st.session_state.current_layer = 0
    if "tool" not in st.session_state:
        st.session_state.tool = "pencil"
    if "brush_size" not in st.session_state:
        st.session_state.brush_size = 1
    if "zoom" not in st.session_state:
        st.session_state.zoom = 16
    if "fg_color" not in st.session_state:
        st.session_state.fg_color = DEFAULT_PALETTE[0]
    if "bg_color" not in st.session_state:
        st.session_state.bg_color = DEFAULT_PALETTE[-1]
    if "show_grid" not in st.session_state:
        st.session_state.show_grid = True
    if "show_onion" not in st.session_state:
        st.session_state.show_onion = True
    if "history" not in st.session_state:
        st.session_state.history = []
    if "future" not in st.session_state:
        st.session_state.future = []
    if "last_canvas_event" not in st.session_state:
        st.session_state.last_canvas_event = None
    if "last_live_event_id" not in st.session_state:
        st.session_state.last_live_event_id = None
    if "last_live_event_seq" not in st.session_state:
        st.session_state.last_live_event_seq = 0
    if "canvas_epoch" not in st.session_state:
        st.session_state.canvas_epoch = 0
    if "loaded_project_token" not in st.session_state:
        st.session_state.loaded_project_token = None
    if "loaded_image_token" not in st.session_state:
        st.session_state.loaded_image_token = None
    if "sprite_sheet_settings" not in st.session_state:
        st.session_state.sprite_sheet_settings = {"cell_w": 16, "cell_h": 16, "padding": 0, "offset_x": 0, "offset_y": 0}


def reset_editor_state() -> None:
    st.session_state.current_frame = 0
    st.session_state.current_layer = 0
    st.session_state.history = []
    st.session_state.future = []
    st.session_state.last_canvas_event = None
    st.session_state.last_live_event_id = None
    bump_canvas_epoch()
    project: SpriteProject = st.session_state.project
    if project.palette:
        st.session_state.fg_color = project.palette[0]
        st.session_state.bg_color = project.palette[min(1, len(project.palette) - 1)]


def sanitize_indices() -> None:
    project: SpriteProject = st.session_state.project
    st.session_state.current_frame = clamp(int(st.session_state.current_frame), 0, project.frame_count - 1)
    st.session_state.current_layer = clamp(int(st.session_state.current_layer), 0, len(project.layers) - 1)


def bump_canvas_epoch() -> None:
    st.session_state.canvas_epoch = int(st.session_state.get("canvas_epoch", 0)) + 1


def push_history() -> None:
    st.session_state.history.append(st.session_state.project.clone())
    if len(st.session_state.history) > MAX_HISTORY:
        st.session_state.history = st.session_state.history[-MAX_HISTORY:]
    st.session_state.future = []


def undo() -> None:
    if not st.session_state.history:
        return
    st.session_state.future.append(st.session_state.project.clone())
    st.session_state.project = st.session_state.history.pop()
    sanitize_indices()
    bump_canvas_epoch()
    st.session_state.last_canvas_event = None
    st.session_state.last_live_event_id = None


def redo() -> None:
    if not st.session_state.future:
        return
    st.session_state.history.append(st.session_state.project.clone())
    st.session_state.project = st.session_state.future.pop()
    sanitize_indices()
    bump_canvas_epoch()
    st.session_state.last_canvas_event = None
    st.session_state.last_live_event_id = None


def raw_to_sprite(x: int, y: int, zoom: int) -> Tuple[int, int]:
    return int(x // zoom), int(y // zoom)


def clamp_point(point: Tuple[int, int], project: SpriteProject) -> Tuple[int, int]:
    return clamp(point[0], 0, project.width - 1), clamp(point[1], 0, project.height - 1)


def event_signature(event: dict[str, Any] | None) -> str | None:
    if event is None:
        return None
    return json.dumps(event, sort_keys=True, default=str)


def checkerboard(width: int, height: int, cell: int) -> Image.Image:
    light = (230, 230, 230, 255)
    dark = (200, 200, 200, 255)
    image = Image.new("RGBA", (width, height), light)
    draw = ImageDraw.Draw(image)
    for y in range(0, height, cell):
        for x in range(0, width, cell):
            if ((x // cell) + (y // cell)) % 2:
                draw.rectangle((x, y, min(width - 1, x + cell - 1), min(height - 1, y + cell - 1)), fill=dark)
    return image


def build_onion_sprite() -> Image.Image:
    project: SpriteProject = st.session_state.project
    frame_index = st.session_state.current_frame
    onion = Image.new("RGBA", project.size, (0, 0, 0, 0))
    if st.session_state.show_onion and frame_index > 0:
        prev_img = tint_image(project.flatten_frame(frame_index - 1), (255, 80, 80), 0.45, 0.35)
        onion = Image.alpha_composite(onion, prev_img)
    if st.session_state.show_onion and frame_index < project.frame_count - 1:
        next_img = tint_image(project.flatten_frame(frame_index + 1), (80, 150, 255), 0.45, 0.35)
        onion = Image.alpha_composite(onion, next_img)
    return onion


def build_canvas_layers() -> tuple[Image.Image, Image.Image, Image.Image]:
    project: SpriteProject = st.session_state.project
    frame_index = st.session_state.current_frame
    layer_index = st.session_state.current_layer
    zoom = int(st.session_state.zoom)
    canvas_size = (project.width * zoom, project.height * zoom)

    lower_sprite = build_onion_sprite()
    active_sprite = Image.new("RGBA", project.size, (0, 0, 0, 0))
    upper_sprite = Image.new("RGBA", project.size, (0, 0, 0, 0))

    for idx, layer in enumerate(project.layers):
        if not layer.visible:
            continue
        cel = adjust_image_alpha(project.get_cel(idx, frame_index), layer.opacity)
        if idx < layer_index:
            lower_sprite = Image.alpha_composite(lower_sprite, cel)
        elif idx == layer_index:
            active_sprite = Image.alpha_composite(active_sprite, cel)
        else:
            upper_sprite = Image.alpha_composite(upper_sprite, cel)

    lower_scaled = lower_sprite.resize(canvas_size, Image.Resampling.NEAREST)
    active_scaled = active_sprite.resize(canvas_size, Image.Resampling.NEAREST)
    upper_scaled = upper_sprite.resize(canvas_size, Image.Resampling.NEAREST)

    lower_canvas = checkerboard(lower_scaled.width, lower_scaled.height, max(4, zoom))
    lower_canvas.alpha_composite(lower_scaled)

    active_canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    active_canvas.alpha_composite(active_scaled)

    upper_canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    upper_canvas.alpha_composite(upper_scaled)

    draw = ImageDraw.Draw(upper_canvas)
    if st.session_state.show_grid and zoom >= 8:
        line_color = (0, 0, 0, 50)
        for x in range(0, upper_canvas.width + 1, zoom):
            draw.line((x, 0, x, upper_canvas.height), fill=line_color)
        for y in range(0, upper_canvas.height + 1, zoom):
            draw.line((0, y, upper_canvas.width, y), fill=line_color)

    slice_width = max(1, zoom // 6)
    for slc in project.slices:
        x0 = slc.x * zoom
        y0 = slc.y * zoom
        x1 = (slc.x + slc.w) * zoom - 1
        y1 = (slc.y + slc.h) * zoom - 1
        draw.rectangle((x0, y0, x1, y1), outline=slc.color, width=slice_width)

    return lower_canvas, active_canvas, upper_canvas


def build_canvas_preview() -> Image.Image:
    lower_canvas, active_canvas, upper_canvas = build_canvas_layers()
    preview = lower_canvas.copy()
    preview.alpha_composite(active_canvas)
    preview.alpha_composite(upper_canvas)
    return preview


def image_to_data_url(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    encoded = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def normalize_live_point(point: Any, project: SpriteProject) -> Tuple[int, int]:
    if not isinstance(point, dict):
        return 0, 0
    x = int(point.get("x", 0))
    y = int(point.get("y", 0))
    return clamp_point((x, y), project)


def apply_live_canvas_event(event: dict[str, Any]) -> bool:
    project: SpriteProject = st.session_state.project
    layer_index = st.session_state.current_layer
    frame_index = st.session_state.current_frame
    layer = project.layers[layer_index]
    tool = str(event.get("tool", st.session_state.tool))

    if layer.locked:
        st.toast("Selected layer is locked.")
        return False

    start = normalize_live_point(event.get("start"), project)
    end = normalize_live_point(event.get("end"), project)

    if tool == "eyedropper":
        color = project.flatten_frame(frame_index).getpixel(end)
        if len(color) >= 4 and color[3] == 0:
            st.session_state.fg_color = "#000000"
        else:
            st.session_state.fg_color = rgba_to_hex(color)
        return True

    if tool == "move" and start == end:
        return False

    push_history()

    if tool == "slice":
        box = normalize_box(start[0], start[1], end[0], end[1])
        x, y, w, h = box_to_xywh(box)
        name = unique_name("Slice", [slc.name for slc in project.slices])
        project.slices.append(SliceInfo(name=name, x=x, y=y, w=w, h=h))
        return True

    image = project.get_cel(layer_index, frame_index).copy()
    fg = hex_to_rgba(st.session_state.fg_color)
    transparent = (0, 0, 0, 0)
    brush = int(st.session_state.brush_size)

    if tool in {"pencil", "eraser"}:
        points: list[Tuple[int, int]] = []
        raw_path = event.get("path", [])
        if isinstance(raw_path, list):
            for point in raw_path:
                points.append(normalize_live_point(point, project))
        if not points:
            points = [start, end]
        if points[0] != start:
            points.insert(0, start)
        if points[-1] != end:
            points.append(end)
        color = fg if tool == "pencil" else transparent
        last = points[0]
        draw_line_brush(image, last, last, color, brush)
        for point in points[1:]:
            draw_line_brush(image, last, point, color, brush)
            last = point
    elif tool == "fill":
        flood_fill(image, end[0], end[1], fg)
    elif tool == "line":
        draw_line_brush(image, start, end, fg, brush)
    elif tool == "rect":
        draw = ImageDraw.Draw(image)
        draw.rectangle(normalize_box(start[0], start[1], end[0], end[1]), outline=fg, width=brush)
    elif tool == "ellipse":
        draw = ImageDraw.Draw(image)
        draw.ellipse(normalize_box(start[0], start[1], end[0], end[1]), outline=fg, width=brush)
    elif tool == "move":
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        image = translate_image(image, dx, dy)
    else:
        return False

    project.set_cel(layer_index, frame_index, image)
    return True


def apply_pointer_tool(event: dict[str, Any]) -> bool:
    project: SpriteProject = st.session_state.project
    layer_index = st.session_state.current_layer
    frame_index = st.session_state.current_frame
    layer = project.layers[layer_index]

    if layer.locked:
        st.toast("Selected layer is locked.")
        return False

    zoom = int(st.session_state.zoom)
    sx_raw = int(event.get("x1", event.get("x", 0)))
    sy_raw = int(event.get("y1", event.get("y", 0)))
    ex_raw = int(event.get("x2", event.get("x", sx_raw)))
    ey_raw = int(event.get("y2", event.get("y", sy_raw)))

    start = raw_to_sprite(sx_raw, sy_raw, zoom)
    end = raw_to_sprite(ex_raw, ey_raw, zoom)
    clamped_start = clamp_point(start, project)
    clamped_end = clamp_point(end, project)

    tool = st.session_state.tool

    if tool == "eyedropper":
        color = project.flatten_frame(frame_index).getpixel(clamped_end)
        if len(color) >= 4 and color[3] == 0:
            st.session_state.fg_color = "#000000"
        else:
            st.session_state.fg_color = rgba_to_hex(color)
        return True

    push_history()

    if tool == "slice":
        box = normalize_box(clamped_start[0], clamped_start[1], clamped_end[0], clamped_end[1])
        x, y, w, h = box_to_xywh(box)
        if w <= 0 or h <= 0:
            return False
        name = unique_name("Slice", [slc.name for slc in project.slices])
        project.slices.append(SliceInfo(name=name, x=x, y=y, w=w, h=h))
        return True

    image = project.get_cel(layer_index, frame_index).copy()
    fg = hex_to_rgba(st.session_state.fg_color)
    transparent = (0, 0, 0, 0)
    brush = int(st.session_state.brush_size)

    if tool == "pencil":
        draw_line_brush(image, clamped_start, clamped_end, fg, brush)
    elif tool == "eraser":
        draw_line_brush(image, clamped_start, clamped_end, transparent, brush)
    elif tool == "fill":
        flood_fill(image, clamped_end[0], clamped_end[1], fg)
    elif tool == "line":
        draw_line_brush(image, clamped_start, clamped_end, fg, brush)
    elif tool == "rect":
        draw = ImageDraw.Draw(image)
        draw.rectangle(normalize_box(clamped_start[0], clamped_start[1], clamped_end[0], clamped_end[1]), outline=fg, width=brush)
    elif tool == "ellipse":
        draw = ImageDraw.Draw(image)
        draw.ellipse(normalize_box(clamped_start[0], clamped_start[1], clamped_end[0], clamped_end[1]), outline=fg, width=brush)
    elif tool == "move":
        dx = int(round((ex_raw - sx_raw) / zoom))
        dy = int(round((ey_raw - sy_raw) / zoom))
        image = translate_image(image, dx, dy)
    else:
        return False

    project.set_cel(layer_index, frame_index, image)
    return True


def maybe_load_project_upload() -> None:
    uploaded = st.sidebar.file_uploader("Load project (.opsprite)", type=[PROJECT_EXTENSION.lstrip(".")])
    if uploaded is None:
        return
    token = (uploaded.name, uploaded.size)
    if token == st.session_state.loaded_project_token:
        return
    st.session_state.project = SpriteProject.from_zip_bytes(uploaded.getvalue())
    st.session_state.loaded_project_token = token
    st.session_state.loaded_image_token = None
    reset_editor_state()
    st.rerun()


def maybe_load_image_upload() -> None:
    with st.sidebar.expander("Import PNG, GIF, JPG, or sprite sheet", expanded=False):
        as_sheet = st.checkbox("Import as sprite sheet", value=False)
        settings = st.session_state.sprite_sheet_settings
        if as_sheet:
            c1, c2 = st.columns(2)
            settings["cell_w"] = c1.number_input("Cell width", min_value=1, max_value=2048, value=int(settings["cell_w"]))
            settings["cell_h"] = c2.number_input("Cell height", min_value=1, max_value=2048, value=int(settings["cell_h"]))
            c3, c4, c5 = st.columns(3)
            settings["padding"] = c3.number_input("Padding", min_value=0, max_value=256, value=int(settings["padding"]))
            settings["offset_x"] = c4.number_input("Offset X", min_value=0, max_value=4096, value=int(settings["offset_x"]))
            settings["offset_y"] = c5.number_input("Offset Y", min_value=0, max_value=4096, value=int(settings["offset_y"]))
        uploaded = st.file_uploader("Import image", type=["png", "gif", "jpg", "jpeg", "webp"])
        if uploaded is None:
            return
        token = (uploaded.name, uploaded.size, as_sheet, tuple(sorted(settings.items())))
        if token == st.session_state.loaded_image_token:
            return
        data = uploaded.getvalue()
        if as_sheet:
            project = project_from_sprite_sheet_bytes(
                data,
                cell_w=int(settings["cell_w"]),
                cell_h=int(settings["cell_h"]),
                padding=int(settings["padding"]),
                offset_x=int(settings["offset_x"]),
                offset_y=int(settings["offset_y"]),
                name=uploaded.name,
            )
        else:
            project = project_from_image_bytes(data, name=uploaded.name)
        st.session_state.project = project
        st.session_state.loaded_image_token = token
        st.session_state.loaded_project_token = None
        reset_editor_state()
        st.rerun()


def new_project_panel() -> None:
    with st.sidebar.expander("New project", expanded=False):
        width = st.number_input("Width", min_value=1, max_value=512, value=32, step=1)
        height = st.number_input("Height", min_value=1, max_value=512, value=32, step=1)
        frames = st.number_input("Frames", min_value=1, max_value=256, value=1, step=1)
        if st.button("Create new project", use_container_width=True):
            st.session_state.project = SpriteProject.create(int(width), int(height), int(frames))
            st.session_state.loaded_project_token = None
            st.session_state.loaded_image_token = None
            reset_editor_state()
            st.rerun()


def render_sidebar() -> None:
    maybe_load_project_upload()
    maybe_load_image_upload()
    new_project_panel()

    project: SpriteProject = st.session_state.project
    sanitize_indices()

    st.sidebar.header("Tools")
    st.session_state.tool = st.sidebar.selectbox("Tool", TOOLS, index=TOOLS.index(st.session_state.tool))
    st.session_state.brush_size = st.sidebar.slider("Brush size", min_value=1, max_value=16, value=int(st.session_state.brush_size))
    st.session_state.zoom = st.sidebar.slider("Zoom", min_value=4, max_value=48, value=int(st.session_state.zoom), step=1)
    st.session_state.show_grid = st.sidebar.checkbox("Show grid", value=bool(st.session_state.show_grid))
    st.session_state.show_onion = st.sidebar.checkbox("Show onion skin", value=bool(st.session_state.show_onion))

    st.sidebar.header("Colors")
    fg_col, bg_col = st.sidebar.columns(2)
    st.session_state.fg_color = fg_col.color_picker("FG", value=str(st.session_state.fg_color))
    st.session_state.bg_color = bg_col.color_picker("BG", value=str(st.session_state.bg_color))
    if project.palette:
        st.sidebar.caption("Palette")
        palette_choice = st.sidebar.selectbox("Quick swatch", options=project.palette, index=0)
        if st.sidebar.button("Use swatch as FG", use_container_width=True):
            st.session_state.fg_color = palette_choice
            st.rerun()

    if has_live_pixel_canvas():
        st.sidebar.info("The canvas now uses a custom live pixel component. Brush strokes and drag previews render in the browser immediately and are committed back to the sprite on mouse release.")

    st.sidebar.header("History")
    c1, c2 = st.sidebar.columns(2)
    if c1.button("Undo", use_container_width=True, disabled=not st.session_state.history):
        undo()
        st.rerun()
    if c2.button("Redo", use_container_width=True, disabled=not st.session_state.future):
        redo()
        st.rerun()

    st.sidebar.header("Export")
    st.sidebar.download_button(
        "Download project (.opsprite)",
        data=project.to_zip_bytes(),
        file_name=f"project{PROJECT_EXTENSION}",
        mime="application/zip",
        use_container_width=True,
    )
    st.sidebar.download_button(
        "Download current frame (.png)",
        data=export_png_bytes(project, st.session_state.current_frame),
        file_name=f"frame_{st.session_state.current_frame + 1:03d}.png",
        mime="image/png",
        use_container_width=True,
    )
    st.sidebar.download_button(
        "Download animation (.gif)",
        data=export_gif_bytes(project),
        file_name="animation.gif",
        mime="image/gif",
        use_container_width=True,
    )
    st.sidebar.download_button(
        "Download sprite sheet bundle (.zip)",
        data=export_sprite_sheet_bundle_bytes(project),
        file_name="sprite_sheet_bundle.zip",
        mime="application/zip",
        use_container_width=True,
    )


def render_frame_controls() -> None:
    project: SpriteProject = st.session_state.project
    sanitize_indices()

    st.subheader("Frames")
    frame_options = list(range(project.frame_count))
    st.session_state.current_frame = st.selectbox(
        "Active frame",
        frame_options,
        index=int(st.session_state.current_frame),
        format_func=lambda i: f"Frame {i + 1} ({project.frame_durations[i]} ms)",
    )
    duration = st.number_input(
        "Frame duration (ms)",
        min_value=10,
        max_value=5000,
        step=10,
        value=int(project.frame_durations[st.session_state.current_frame]),
    )
    project.frame_durations[st.session_state.current_frame] = int(duration)

    c1, c2, c3, c4 = st.columns(4)
    if c1.button("Add blank frame", use_container_width=True):
        push_history()
        bump_canvas_epoch()
        st.session_state.current_frame = project.add_frame(index=st.session_state.current_frame + 1)
        st.rerun()
    if c2.button("Duplicate frame", use_container_width=True):
        push_history()
        bump_canvas_epoch()
        st.session_state.current_frame = project.add_frame(index=st.session_state.current_frame + 1, copy_from=st.session_state.current_frame)
        st.rerun()
    if c3.button("Delete frame", use_container_width=True, disabled=project.frame_count <= 1):
        push_history()
        bump_canvas_epoch()
        project.remove_frame(st.session_state.current_frame)
        sanitize_indices()
        st.rerun()
    if c4.button("Reverse frames", use_container_width=True, disabled=project.frame_count <= 1):
        push_history()
        bump_canvas_epoch()
        project.frame_durations.reverse()
        for layer in project.layers:
            layer.cels.reverse()
        st.session_state.current_frame = project.frame_count - 1 - st.session_state.current_frame
        st.rerun()


def render_layer_controls() -> None:
    project: SpriteProject = st.session_state.project
    sanitize_indices()

    st.subheader("Layers")
    layer_options = list(range(len(project.layers)))
    st.session_state.current_layer = st.selectbox(
        "Active layer",
        layer_options,
        index=int(st.session_state.current_layer),
        format_func=lambda i: f"{i + 1}. {project.layers[i].name}",
    )
    layer = project.layers[st.session_state.current_layer]
    layer.name = st.text_input("Layer name", value=layer.name)
    layer.visible = st.checkbox("Visible", value=layer.visible)
    layer.locked = st.checkbox("Locked", value=layer.locked)
    layer.opacity = st.slider("Opacity", min_value=0, max_value=255, value=int(layer.opacity))

    c1, c2, c3 = st.columns(3)
    if c1.button("Add layer", use_container_width=True):
        push_history()
        bump_canvas_epoch()
        st.session_state.current_layer = project.add_layer(index=st.session_state.current_layer + 1)
        st.rerun()
    if c2.button("Delete layer", use_container_width=True, disabled=len(project.layers) <= 1):
        push_history()
        bump_canvas_epoch()
        project.remove_layer(st.session_state.current_layer)
        sanitize_indices()
        st.rerun()
    if c3.button("Clear cel", use_container_width=True):
        push_history()
        bump_canvas_epoch()
        project.clear_cel(st.session_state.current_layer, st.session_state.current_frame)
        st.rerun()

    c4, c5 = st.columns(2)
    if c4.button("Layer up", use_container_width=True, disabled=st.session_state.current_layer >= len(project.layers) - 1):
        push_history()
        bump_canvas_epoch()
        st.session_state.current_layer = project.move_layer(st.session_state.current_layer, st.session_state.current_layer + 1)
        st.rerun()
    if c5.button("Layer down", use_container_width=True, disabled=st.session_state.current_layer <= 0):
        push_history()
        bump_canvas_epoch()
        st.session_state.current_layer = project.move_layer(st.session_state.current_layer, st.session_state.current_layer - 1)
        st.rerun()


def render_slice_controls() -> None:
    project: SpriteProject = st.session_state.project
    with st.expander("Slices", expanded=False):
        if not project.slices:
            st.caption("Use the slice tool and drag on the canvas to create a slice.")
            return
        for idx, slc in enumerate(project.slices):
            cols = st.columns([2, 2, 2, 1])
            cols[0].write(f"**{slc.name}**")
            cols[1].write(f"x={slc.x}, y={slc.y}")
            cols[2].write(f"w={slc.w}, h={slc.h}")
            if cols[3].button("Delete", key=f"delete_slice_{idx}", use_container_width=True):
                push_history()
                bump_canvas_epoch()
                del project.slices[idx]
                st.rerun()


def render_legacy_pointer_canvas(preview: Image.Image, cursor: str) -> None:
    event = streamlit_image_coordinates(
        preview,
        key="editor_canvas_drag",
        width=preview.width,
        height=preview.height,
        click_and_drag=True,
        cursor=cursor,
    )

    sig = event_signature(event)
    if sig is not None and sig != st.session_state.last_canvas_event:
        st.session_state.last_canvas_event = sig
        if apply_pointer_tool(event):
            st.rerun()


def extract_component_event(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    event = getattr(result, "event", None)
    if isinstance(event, dict):
        return event
    if isinstance(result, dict):
        maybe_event = result.get("event")
        if isinstance(maybe_event, dict):
            return maybe_event
    return None


def render_live_canvas(cursor: str) -> None:
    lower_canvas, active_canvas, upper_canvas = build_canvas_layers()
    data = {
        "width": lower_canvas.width,
        "height": lower_canvas.height,
        "sprite_width": st.session_state.project.width,
        "sprite_height": st.session_state.project.height,
        "zoom": int(st.session_state.zoom),
        "tool": st.session_state.tool,
        "brush_size": int(st.session_state.brush_size),
        "fg_css": str(st.session_state.fg_color),
        "slice_color": "#00ffff",
        "cursor": cursor,
        "layer_locked": bool(st.session_state.project.layers[st.session_state.current_layer].locked),
        "acked_event_seq": int(st.session_state.last_live_event_seq),
        "canvas_context": f"{int(st.session_state.canvas_epoch)}:{st.session_state.current_frame}:{st.session_state.current_layer}:{st.session_state.project.width}:{st.session_state.project.height}",
        "base_image": image_to_data_url(lower_canvas),
        "active_image": image_to_data_url(active_canvas),
        "guides_image": image_to_data_url(upper_canvas),
    }
    result = pixel_canvas(data, key="editor_live_canvas")
    event = extract_component_event(result)
    if event is None:
        return
    event_id = str(event.get("id", ""))
    event_seq = int(event.get("seq", 0) or 0)
    if event_seq:
        if event_seq <= int(st.session_state.last_live_event_seq):
            return
        st.session_state.last_live_event_seq = event_seq
    elif not event_id or event_id == st.session_state.last_live_event_id:
        return
    st.session_state.last_live_event_id = event_id
    if apply_live_canvas_event(event):
        st.rerun()


def render_canvas() -> None:
    project: SpriteProject = st.session_state.project
    tool = st.session_state.tool
    cursor = "grab" if tool == "move" else ("pointer" if tool == "eyedropper" else "crosshair")

    st.subheader("Canvas")
    st.caption(f"{project.width} x {project.height} px | {project.frame_count} frame(s) | {len(project.layers)} layer(s)")

    if has_live_pixel
