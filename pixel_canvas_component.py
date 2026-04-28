from __future__ import annotations

from typing import Any

import streamlit as st

_HAS_COMPONENT_V2 = hasattr(st, "components") and hasattr(st.components, "v2")

_HTML = """
<div id="ops-pixel-root">
  <canvas id="ops-pixel-canvas" tabindex="0"></canvas>
</div>
"""

_CSS = """
:host {
  display: block;
}

#ops-pixel-root {
  display: inline-block;
  line-height: 0;
  user-select: none;
  -webkit-user-select: none;
}

#ops-pixel-canvas {
  display: block;
  image-rendering: pixelated;
  image-rendering: crisp-edges;
  touch-action: none;
  outline: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 4px;
  background: transparent;
}
"""

_JS = r"""
function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function samePoint(a, b) {
  return !!a && !!b && a.x === b.x && a.y === b.y;
}

function ensureCanvasSize(canvas, width, height) {
  if (canvas.width !== width) {
    canvas.width = width;
  }
  if (canvas.height !== height) {
    canvas.height = height;
  }
}

function copyCanvas(sourceCanvas, targetCanvas) {
  const ctx = targetCanvas.getContext("2d");
  ctx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);
  ctx.drawImage(sourceCanvas, 0, 0);
}

function drawImageToCanvas(canvas, image) {
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
}

function loadImage(src) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = src;
  });
}

function bresenhamPoints(x0, y0, x1, y1) {
  const points = [];
  let dx = Math.abs(x1 - x0);
  let dy = -Math.abs(y1 - y0);
  let sx = x0 < x1 ? 1 : -1;
  let sy = y0 < y1 ? 1 : -1;
  let err = dx + dy;
  let currentX = x0;
  let currentY = y0;

  while (true) {
    points.push({ x: currentX, y: currentY });
    if (currentX === x1 && currentY === y1) {
      break;
    }
    const e2 = 2 * err;
    if (e2 >= dy) {
      err += dy;
      currentX += sx;
    }
    if (e2 <= dx) {
      err += dx;
      currentY += sy;
    }
  }
  return points;
}

function drawBrush(ctx, x, y, size, zoom, color, erase) {
  const half = Math.floor((size - 1) / 2);
  const left = (x - half) * zoom;
  const top = (y - half) * zoom;
  const extent = size * zoom;
  if (erase) {
    ctx.clearRect(left, top, extent, extent);
  } else {
    ctx.fillStyle = color;
    ctx.fillRect(left, top, extent, extent);
  }
}

function drawLineBrush(ctx, p0, p1, size, zoom, color, erase) {
  const points = bresenhamPoints(p0.x, p0.y, p1.x, p1.y);
  for (const point of points) {
    drawBrush(ctx, point.x, point.y, size, zoom, color, erase);
  }
}

function drawRectOutline(ctx, p0, p1, size, zoom, color, erase) {
  const left = Math.min(p0.x, p1.x);
  const right = Math.max(p0.x, p1.x);
  const top = Math.min(p0.y, p1.y);
  const bottom = Math.max(p0.y, p1.y);
  drawLineBrush(ctx, { x: left, y: top }, { x: right, y: top }, size, zoom, color, erase);
  drawLineBrush(ctx, { x: right, y: top }, { x: right, y: bottom }, size, zoom, color, erase);
  drawLineBrush(ctx, { x: right, y: bottom }, { x: left, y: bottom }, size, zoom, color, erase);
  drawLineBrush(ctx, { x: left, y: bottom }, { x: left, y: top }, size, zoom, color, erase);
}

function drawEllipseOutline(ctx, p0, p1, size, zoom, color) {
  const left = Math.min(p0.x, p1.x) * zoom;
  const top = Math.min(p0.y, p1.y) * zoom;
  const width = (Math.abs(p1.x - p0.x) + 1) * zoom;
  const height = (Math.abs(p1.y - p0.y) + 1) * zoom;
  const lineWidth = Math.max(1, size * zoom);
  const radiusX = Math.max(0, width / 2 - lineWidth / 2);
  const radiusY = Math.max(0, height / 2 - lineWidth / 2);
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  ctx.ellipse(left + width / 2, top + height / 2, radiusX, radiusY, 0, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawSliceOverlay(ctx, start, end, data) {
  const left = Math.min(start.x, end.x) * data.zoom;
  const top = Math.min(start.y, end.y) * data.zoom;
  const width = (Math.abs(end.x - start.x) + 1) * data.zoom;
  const height = (Math.abs(end.y - start.y) + 1) * data.zoom;
  ctx.save();
  ctx.strokeStyle = data.slice_color || "#00ffff";
  ctx.lineWidth = Math.max(1, Math.floor(data.zoom / 6));
  ctx.strokeRect(left + 0.5, top + 0.5, Math.max(0, width - 1), Math.max(0, height - 1));
  ctx.restore();
}

function drawHover(ctx, hoverPoint, data) {
  if (!hoverPoint) {
    return;
  }
  const brushLikeTools = ["pencil", "eraser"];
  const size = brushLikeTools.includes(data.tool) ? data.brush_size : 1;
  const half = Math.floor((size - 1) / 2);
  const left = (hoverPoint.x - half) * data.zoom;
  const top = (hoverPoint.y - half) * data.zoom;
  const width = size * data.zoom;
  const height = size * data.zoom;

  ctx.save();
  ctx.strokeStyle = "rgba(0, 0, 0, 0.75)";
  ctx.lineWidth = 1;
  ctx.strokeRect(left + 0.5, top + 0.5, Math.max(0, width - 1), Math.max(0, height - 1));
  if (width > 3 && height > 3) {
    ctx.setLineDash([3, 2]);
    ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
    ctx.strokeRect(left + 1.5, top + 1.5, Math.max(0, width - 3), Math.max(0, height - 3));
  }
  ctx.restore();
}

function renderScene(state) {
  const ctx = state.ctx;
  ctx.clearRect(0, 0, state.canvas.width, state.canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(state.baseCanvas, 0, 0);
  ctx.drawImage(state.activePreviewCanvas, 0, 0);
  ctx.drawImage(state.guidesCanvas, 0, 0);

  if (state.drag && state.drag.tool === "slice") {
    drawSliceOverlay(ctx, state.drag.start, state.drag.end, state.data);
  }
  if (!state.drag) {
    drawHover(ctx, state.hover, state.data);
  }
}

function getSpritePointFromEvent(state, event) {
  const rect = state.canvas.getBoundingClientRect();
  const scaleX = state.canvas.width / rect.width;
  const scaleY = state.canvas.height / rect.height;
  const rawX = clamp((event.clientX - rect.left) * scaleX, 0, state.canvas.width - 1);
  const rawY = clamp((event.clientY - rect.top) * scaleY, 0, state.canvas.height - 1);
  return {
    x: clamp(Math.floor(rawX / state.data.zoom), 0, state.data.sprite_width - 1),
    y: clamp(Math.floor(rawY / state.data.zoom), 0, state.data.sprite_height - 1),
  };
}

function resetActivePreview(state) {
  copyCanvas(state.activeCanvas, state.activePreviewCanvas);
}

function updateDragPreview(state, point) {
  const drag = state.drag;
  if (!drag) {
    return;
  }
  drag.end = { x: point.x, y: point.y };
  drag.dragged = drag.dragged || !samePoint(drag.start, point);

  const tool = drag.tool;
  const previewCtx = state.activePreviewCanvas.getContext("2d");
  previewCtx.imageSmoothingEnabled = false;

  if (tool === "pencil" || tool === "eraser") {
    if (!samePoint(drag.last, point)) {
      drawLineBrush(
        previewCtx,
        drag.last,
        point,
        state.data.brush_size,
        state.data.zoom,
        state.data.fg_css,
        tool === "eraser",
      );
      drag.path.push({ x: point.x, y: point.y });
      drag.last = { x: point.x, y: point.y };
    }
  } else if (tool === "line") {
    resetActivePreview(state);
    drawLineBrush(previewCtx, drag.start, point, state.data.brush_size, state.data.zoom, state.data.fg_css, false);
  } else if (tool === "rect") {
    resetActivePreview(state);
    drawRectOutline(previewCtx, drag.start, point, state.data.brush_size, state.data.zoom, state.data.fg_css, false);
  } else if (tool === "ellipse") {
    resetActivePreview(state);
    drawEllipseOutline(previewCtx, drag.start, point, state.data.brush_size, state.data.zoom, state.data.fg_css);
  } else if (tool === "move") {
    previewCtx.clearRect(0, 0, state.activePreviewCanvas.width, state.activePreviewCanvas.height);
    previewCtx.drawImage(
      state.activeCanvas,
      (point.x - drag.start.x) * state.data.zoom,
      (point.y - drag.start.y) * state.data.zoom,
    );
  } else if (tool === "slice") {
    resetActivePreview(state);
  } else {
    resetActivePreview(state);
  }

  renderScene(state);
}

function startDrag(state, point) {
  state.drag = {
    id: `stroke-${Date.now()}-${++state.eventCounter}`,
    tool: state.data.tool,
    start: { x: point.x, y: point.y },
    end: { x: point.x, y: point.y },
    last: { x: point.x, y: point.y },
    path: [{ x: point.x, y: point.y }],
    dragged: false,
  };

  resetActivePreview(state);
  const previewCtx = state.activePreviewCanvas.getContext("2d");
  previewCtx.imageSmoothingEnabled = false;

  if (state.data.tool === "pencil") {
    drawLineBrush(previewCtx, point, point, state.data.brush_size, state.data.zoom, state.data.fg_css, false);
  } else if (state.data.tool === "eraser") {
    drawLineBrush(previewCtx, point, point, state.data.brush_size, state.data.zoom, state.data.fg_css, true);
  }

  renderScene(state);
}

function finishDrag(state, point) {
  const drag = state.drag;
  if (!drag) {
    return;
  }

  updateDragPreview(state, point);

  const payload = {
    id: drag.id,
    tool: drag.tool,
    start: drag.start,
    end: { x: point.x, y: point.y },
    dragged: drag.dragged || !samePoint(drag.start, point),
  };

  if (drag.tool === "pencil" || drag.tool === "eraser") {
    payload.path = drag.path;
  }

  state.drag = null;
  state.setTriggerValue("event", payload);
}

function cancelDrag(state) {
  state.drag = null;
  resetActivePreview(state);
  renderScene(state);
}

export default function(component) {
  const { data, setTriggerValue, parentElement } = component;

  let state = parentElement.__opsPixelEditorState;
  if (!state) {
    const root = parentElement.querySelector("#ops-pixel-root") || parentElement;
    let canvas = root.querySelector("#ops-pixel-canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      canvas.id = "ops-pixel-canvas";
      root.appendChild(canvas);
    }

    const ctx = canvas.getContext("2d");
    ctx.imageSmoothingEnabled = false;

    state = {
      root,
      canvas,
      ctx,
      baseCanvas: document.createElement("canvas"),
      activeCanvas: document.createElement("canvas"),
      activePreviewCanvas: document.createElement("canvas"),
      guidesCanvas: document.createElement("canvas"),
      renderToken: 0,
      eventCounter: 0,
      drag: null,
      hover: null,
      data: null,
      setTriggerValue,
    };

    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !state.data) {
        return;
      }
      const point = getSpritePointFromEvent(state, event);
      state.hover = point;
      startDrag(state, point);
      if (state.canvas.setPointerCapture) {
        state.canvas.setPointerCapture(event.pointerId);
      }
      event.preventDefault();
    });

    canvas.addEventListener("pointermove", (event) => {
      if (!state.data) {
        return;
      }
      const point = getSpritePointFromEvent(state, event);
      state.hover = point;
      if (!state.drag) {
        renderScene(state);
        return;
      }
      updateDragPreview(state, point);
      event.preventDefault();
    });

    canvas.addEventListener("pointerup", (event) => {
      if (!state.data || !state.drag) {
        return;
      }
      const point = getSpritePointFromEvent(state, event);
      state.hover = point;
      finishDrag(state, point);
      event.preventDefault();
    });

    canvas.addEventListener("pointercancel", () => {
      if (state.drag) {
        cancelDrag(state);
      }
    });

    canvas.addEventListener("pointerleave", () => {
      if (!state.drag) {
        state.hover = null;
        renderScene(state);
      }
    });

    parentElement.__opsPixelEditorState = state;
  }

  state.data = data;
  state.setTriggerValue = setTriggerValue;
  state.canvas.style.cursor = data.cursor || "crosshair";
  state.root.style.width = `${data.width}px`;
  state.root.style.height = `${data.height}px`;
  state.canvas.style.width = `${data.width}px`;
  state.canvas.style.height = `${data.height}px`;

  [state.canvas, state.baseCanvas, state.activeCanvas, state.activePreviewCanvas, state.guidesCanvas].forEach((canvas) => {
    ensureCanvasSize(canvas, data.width, data.height);
  });

  const token = ++state.renderToken;
  Promise.all([
    loadImage(data.base_image),
    loadImage(data.active_image),
    loadImage(data.guides_image),
  ])
    .then(([baseImage, activeImage, guidesImage]) => {
      if (token !== state.renderToken) {
        return;
      }
      drawImageToCanvas(state.baseCanvas, baseImage);
      drawImageToCanvas(state.activeCanvas, activeImage);
      drawImageToCanvas(state.guidesCanvas, guidesImage);
      if (!state.drag) {
        resetActivePreview(state);
      }
      renderScene(state);
    })
    .catch((error) => {
      console.error("Failed to render pixel canvas", error);
    });
}
"""

if _HAS_COMPONENT_V2:
    _pixel_canvas = st.components.v2.component(
        "ops_live_pixel_canvas",
        html=_HTML,
        css=_CSS,
        js=_JS,
        isolate_styles=True,
    )
else:
    _pixel_canvas = None


def has_live_pixel_canvas() -> bool:
    return _pixel_canvas is not None


def pixel_canvas(data: dict[str, Any], key: str):
    if _pixel_canvas is None:
        raise RuntimeError("Streamlit components.v2 is not available.")
    return _pixel_canvas(
        data=data,
        key=key,
        default={"event": None},
        on_event_change=lambda: None,
    )
