from __future__ import annotations

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
    project: SpriteProject = st.session_state.project
    if project.palette:
        st.session_state.fg_color = project.palette[0]
        st.session_state.bg_color = project.palette[min(1, len(project.palette) - 1)]


def sanitize_indices() -> None:
    project: SpriteProject = st.session_state.project
    st.session_state.current_frame = clamp(int(st.session_state.current_frame), 0, project.frame_count - 1)
    st.session_state.current_layer = clamp(int(st.session_state.current_layer), 0, len(project.layers) - 1)


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
    st.session_state.last_canvas_event = None


def redo() -> None:
    if not st.session_state.future:
        return
    st.session_state.history.append(st.session_state.project.clone())
    st.session_state.project = st.session_state.future.pop()
    sanitize_indices()
    st.session_state.last_canvas_event = None


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


def build_canvas_preview() -> Image.Image:
    project: SpriteProject = st.session_state.project
    frame_index = st.session_state.current_frame
    zoom = int(st.session_state.zoom)
    onion = Image.new("RGBA", project.size, (0, 0, 0, 0))
    if st.session_state.show_onion and frame_index > 0:
        prev_img = tint_image(project.flatten_frame(frame_index - 1), (255, 80, 80), 0.45, 0.35)
        onion = Image.alpha_composite(onion, prev_img)
    if st.session_state.show_onion and frame_index < project.frame_count - 1:
        next_img = tint_image(project.flatten_frame(frame_index + 1), (80, 150, 255), 0.45, 0.35)
        onion = Image.alpha_composite(onion, next_img)
    current = project.flatten_frame(frame_index)
    composited = Image.alpha_composite(onion, current)
    scaled = composited.resize((project.width * zoom, project.height * zoom), Image.Resampling.NEAREST)
    canvas = checkerboard(scaled.width, scaled.height, max(4, zoom))
    canvas.alpha_composite(scaled)

    draw = ImageDraw.Draw(canvas)
    if st.session_state.show_grid and zoom >= 8:
        line_color = (0, 0, 0, 50)
        for x in range(0, canvas.width + 1, zoom):
            draw.line((x, 0, x, canvas.height), fill=line_color)
        for y in range(0, canvas.height + 1, zoom):
            draw.line((0, y, canvas.width, y), fill=line_color)

    slice_width = max(1, zoom // 6)
    for slc in project.slices:
        x0 = slc.x * zoom
        y0 = slc.y * zoom
        x1 = (slc.x + slc.w) * zoom - 1
        y1 = (slc.y + slc.h) * zoom - 1
        draw.rectangle((x0, y0, x1, y1), outline=slc.color, width=slice_width)

    return canvas


def apply_tool(event: dict[str, Any]) -> bool:
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
        st.session_state.current_frame = project.add_frame(index=st.session_state.current_frame + 1)
        st.rerun()
    if c2.button("Duplicate frame", use_container_width=True):
        push_history()
        st.session_state.current_frame = project.add_frame(index=st.session_state.current_frame + 1, copy_from=st.session_state.current_frame)
        st.rerun()
    if c3.button("Delete frame", use_container_width=True, disabled=project.frame_count <= 1):
        push_history()
        project.remove_frame(st.session_state.current_frame)
        sanitize_indices()
        st.rerun()
    if c4.button("Reverse frames", use_container_width=True, disabled=project.frame_count <= 1):
        push_history()
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
        st.session_state.current_layer = project.add_layer(index=st.session_state.current_layer + 1)
        st.rerun()
    if c2.button("Delete layer", use_container_width=True, disabled=len(project.layers) <= 1):
        push_history()
        project.remove_layer(st.session_state.current_layer)
        sanitize_indices()
        st.rerun()
    if c3.button("Clear cel", use_container_width=True):
        push_history()
        project.clear_cel(st.session_state.current_layer, st.session_state.current_frame)
        st.rerun()

    c4, c5 = st.columns(2)
    if c4.button("Layer up", use_container_width=True, disabled=st.session_state.current_layer >= len(project.layers) - 1):
        push_history()
        st.session_state.current_layer = project.move_layer(st.session_state.current_layer, st.session_state.current_layer + 1)
        st.rerun()
    if c5.button("Layer down", use_container_width=True, disabled=st.session_state.current_layer <= 0):
        push_history()
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
                del project.slices[idx]
                st.rerun()


def render_canvas() -> None:
    project: SpriteProject = st.session_state.project
    tool = st.session_state.tool
    cursor = "grab" if tool == "move" else ("pointer" if tool == "eyedropper" else "crosshair")

    st.subheader("Canvas")
    preview = build_canvas_preview()
    st.caption(f"{project.width} x {project.height} px | {project.frame_count} frame(s) | {len(project.layers)} layer(s)")
    event = streamlit_image_coordinates(
        preview,
        key="editor_canvas",
        width=preview.width,
        height=preview.height,
        click_and_drag=True,
        cursor=cursor,
    )

    sig = event_signature(event)
    if sig is not None and sig != st.session_state.last_canvas_event:
        st.session_state.last_canvas_event = sig
        if apply_tool(event):
            st.rerun()


st.set_page_config(page_title=APP_NAME, page_icon="🎨", layout="wide")
init_state()
render_sidebar()
sanitize_indices()

st.title(APP_NAME)
st.caption("A browser-oriented Streamlit port of the original desktop editor. It reuses the project format and Pillow-based editing core, but the UI is rewritten for the web.")

left, right = st.columns([2.4, 1.2])
with left:
    render_canvas()
with right:
    render_frame_controls()
    render_layer_controls()
    render_slice_controls()

sheet, meta = build_sprite_sheet(st.session_state.project)
with st.expander("Sprite sheet preview", expanded=False):
    st.image(sheet, caption=f"{meta['meta']['size']['w']} x {meta['meta']['size']['h']} px", use_container_width=False)
    st.json(meta)
