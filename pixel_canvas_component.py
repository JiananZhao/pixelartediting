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

function createCanvas(width, height) {
  const canvas = document.createElement("canvas");
  ensureCanvasSize(canvas, width, height);
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  return canvas;
}

function copyCanvas(sourceCanvas, targetCanvas) {
  const ctx = targetCanvas.getContext("2d");
  ctx.clearRect(0, 0, targetCanvas.width, targetCanvas.height);
  ctx.imageSmoothingEnabled = false;
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

function getGlobalStore() {
  if (!window.__OPS_PIXEL_CANVAS_STORE__) {
    window.__OPS_PIXEL_CANVAS_STORE__ = {};
  }
  return window.__OPS_PIXEL_CANVAS_STORE__;
}

function pruneStore(store, keepKey) {
  const keys = Object.keys(store);
  if (keys.length <= 12) {
    return;
  }
  keys
    .filter((key) => key !== keepKey)
    .sort((a, b) => Number(store[a]?.touchedAt || 0) - Number(store[b]?.touchedAt || 0))
    .slice(0, Math.max(0, keys.length - 12))
    .forEach((key) => {
      delete store[key];
    });
}

function getContextEntry(contextKey, width, height) {
  const store = getGlobalStore();
  const key = contextKey || "__default__";
  let entry = store[key];
  if (!entry || !entry.docCanvas || entry.docCanvas.width !== width || entry.docCanvas.height !== height) {
    entry = {
      contextKey: key,
      docCanvas: createCanvas(width, height),
      initialized: false,
      localSeq: 0,
      lastAckedSeq: 0,
      touchedAt: Date.now(),
    };
    store[key] = entry;
  }
  entry.touchedAt = Date.now();
  pruneStore(store, key);
  return entry;
}

function getDocCanvas(state) {
  if (state.cacheEntry && state.cacheEntry.docCanvas) {
    return state.cacheEntry.docCanvas;
  }
  return state.fallbackDocCanvas;
}

function drawActiveLayer(ctx, activeCanvas, data) {
  if (data.active_visible === false) {
    return;
  }
  const alpha = clamp(Number(data.active_opacity ?? 255) / 255, 0, 1);
  if (alpha <= 0) {
    return;
  }
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.drawImage(activeCanvas, 0, 0);
  ctx.restore();
}

function renderScene(state) {
  const ctx = state.ctx;
  const activeSource = state.drag ? state.activePreviewCanvas : getDocCanvas(state);
  ctx.clearRect(0, 0, state.canvas.width, state.canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(state.baseCanvas, 0, 0);
  drawActiveLayer(ctx, activeSource, state.data);
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
  copyCanvas(getDocCanvas(state), state.activePreviewCanvas);
}

function isMutatingTool(tool) {
  return ["pencil", "eraser", "fill", "line", "rect", "ellipse", "move"].includes(tool);
}

function isBlockedByLock(tool) {
  return tool !== "eyedropper";
}

function parseHexColor(value) {
  let hex = (value || "#000000").trim();
  if (hex.startsWith("#")) {
    hex = hex.slice(1);
  }
  if (hex.length === 3) {
    hex = hex.split("").map((ch) => ch + ch).join("");
  }
  if (hex.length !== 6) {
    return [0, 0, 0, 255];
  }
  return [
    parseInt(hex.slice(0, 2), 16),
    parseInt(hex.slice(2, 4), 16),
    parseInt(hex.slice(4, 6), 16),
    255,
  ];
}

function sameColor(a, b) {
  return a[0] === b[0] && a[1] === b[1] && a[2] === b[2] && a[3] === b[3];
}

function rgbaToCss(color) {
  return `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${color[3] / 255})`;
}

function getCellColor(ctx, x, y, zoom) {
  const rgba = ctx.getImageData(x * zoom, y * zoom, 1, 1).data;
  return [rgba[0], rgba[1], rgba[2], rgba[3]];
}

function fillCell(ctx, x, y, zoom, color) {
  const left = x * zoom;
  const top = y * zoom;
  ctx.clearRect(left, top, zoom, zoom);
  if (color[3] > 0) {
    ctx.fillStyle = rgbaToCss(color);
    ctx.fillRect(left, top, zoom, zoom);
  }
}

function floodFillCanvas(canvas, point, cssColor, data) {
  const ctx = canvas.getContext("2d");
  const replacement = parseHexColor(cssColor);
  const target = getCellColor(ctx, point.x, point.y, data.zoom);
  if (sameColor(target, replacement)) {
    return;
  }
  const width = data.sprite_width;
  const height = data.sprite_height;
  const visited = new Uint8Array(width * height);
  const stack = [{ x: point.x, y: point.y }];
  while (stack.length) {
    const cell = stack.pop();
    if (!cell || cell.x < 0 || cell.y < 0 || cell.x >= width || cell.y >= height) {
      continue;
    }
    const index = cell.y * width + cell.x;
    if (visited[index]) {
      continue;
    }
    visited[index] = 1;
    if (!sameColor(getCellColor(ctx, cell.x, cell.y, data.zoom), target)) {
      continue;
    }
    fillCell(ctx, cell.x, cell.y, data.zoom, replacement);
    stack.push({ x: cell.x + 1, y: cell.y });
    stack.push({ x: cell.x - 1, y: cell.y });
    stack.push({ x: cell.x, y: cell.y + 1 });
    stack.push({ x: cell.x, y: cell.y - 1 });
  }
}

function translateCanvas(canvas, dxSprites, dySprites, zoom) {
  const tempCanvas = createCanvas(canvas.width, canvas.height);
  const tempCtx = tempCanvas.getContext("2d");
  tempCtx.imageSmoothingEnabled = false;
  tempCtx.drawImage(canvas, 0, 0);

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(tempCanvas, dxSprites * zoom, dySprites * zoom);
}

function applyPayloadToCanvas(canvas, payload, data) {
  if (!payload) {
    return;
  }
  const ctx = canvas.getContext("2d");
  ctx.imageSmoothingEnabled = false;
  const brushSize = Math.max(1, Number(payload.brush_size || data.brush_size || 1));
  const color = payload.color || data.fg_css || "#000000";
  const start = payload.start || payload.end;
  const end = payload.end || payload.start;
  if (!start || !end) {
    return;
  }

  if (payload.tool === "pencil" || payload.tool === "eraser") {
    const erase = payload.tool === "eraser";
    const path = Array.isArray(payload.path) && payload.path.length ? payload.path : [start, end];
    let last = path[0] || start;
    drawLineBrush(ctx, last, last, brushSize, data.zoom, color, erase);
    for (const point of path.slice(1)) {
      drawLineBrush(ctx, last, point, brushSize, data.zoom, color, erase);
      last = point;
    }
    if (!samePoint(last, end)) {
      drawLineBrush(ctx, last, end, brushSize, data.zoom, color, erase);
    }
  } else if (payload.tool === "fill") {
    floodFillCanvas(canvas, end, color, data);
  } else if (payload.tool === "line") {
    drawLineBrush(ctx, start, end, brushSize, data.zoom, color, false);
  } else if (payload.tool === "rect") {
    drawRectOutline(ctx, start, end, brushSize, data.zoom, color, false);
  } else if (payload.tool === "ellipse") {
    drawEllipseOutline(ctx, start, end, brushSize, data.zoom, color);
  } else if (payload.tool === "move") {
    translateCanvas(canvas, end.x - start.x, end.y - start.y, data.zoom);
  }
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
  const docCanvas = getDocCanvas(state);

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
    previewCtx.drawImage(docCanvas, (point.x - drag.start.x) * state.data.zoom, (point.y - drag.start.y) * state.data.zoom);
  } else if (tool === "slice") {
    resetActivePreview(state);
  } else {
    resetActivePreview(state);
  }

  renderScene(state);
}

function startDrag(state, point) {
  const nextSeqBase = Number(state.cacheEntry?.localSeq || 0);
  state.drag = {
    id: `stroke-${Date.now()}-${nextSeqBase + 1}-${++state.eventCounter}`,
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

function commitLocally(state, payload) {
  const docCanvas = getDocCanvas(state);
  if (payload.tool === "fill") {
    applyPayloadToCanvas(docCanvas, payload, state.data);
  } else {
    copyCanvas(state.activePreviewCanvas, docCanvas);
  }
  if (state.cacheEntry) {
    state.cacheEntry.localSeq = Math.max(Number(state.cacheEntry.localSeq || 0), Number(payload.seq || 0));
    state.cacheEntry.touchedAt = Date.now();
  }
  resetActivePreview(state);
}

function finishDrag(state, point) {
  const drag = state.drag;
  if (!drag) {
    return;
  }

  updateDragPreview(state, point);

  const payload = {
    id: drag.id,
    seq: Number(state.cacheEntry?.localSeq || 0) + 1,
    tool: drag.tool,
    start: drag.start,
    end: { x: point.x, y: point.y },
    dragged: drag.dragged || !samePoint(drag.start, point),
    brush_size: state.data.brush_size,
    color: state.data.fg_css,
  };

  if (drag.tool === "pencil" || drag.tool === "eraser") {
    payload.path = drag.path;
  }

  state.drag = null;

  if (isMutatingTool(payload.tool)) {
    commitLocally(state, payload);
  }

  renderScene(state);
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
      baseCanvas: createCanvas(1, 1),
      guidesCanvas: createCanvas(1, 1),
      activePreviewCanvas: createCanvas(1, 1),
      fallbackDocCanvas: createCanvas(1, 1),
      renderToken: 0,
      eventCounter: 0,
      canvasContextKey: null,
      cacheEntry: null,
      drag: null,
      hover: null,
      data: null,
      setTriggerValue,
    };

    canvas.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || !state.data) {
        return;
      }
      if (state.data.layer_locked && isBlockedByLock(state.data.tool)) {
        return;
      }
      const point = getSpritePointFromEvent(state, event);
      state.hover = point;
      startDrag(state, point);
      if (state.canvas.setPointerCapture) {
        try {
          state.canvas.setPointerCapture(event.pointerId);
        } catch (error) {
          console.debug("Pointer capture unavailable", error);
        }
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
  const lockedCursor = data.layer_locked && isBlockedByLock(data.tool);
  state.canvas.style.cursor = lockedCursor ? "not-allowed" : (data.cursor || "crosshair");
  state.root.style.width = `${data.width}px`;
  state.root.style.height = `${data.height}px`;
  state.canvas.style.width = `${data.width}px`;
  state.canvas.style.height = `${data.height}px`;

  [state.canvas, state.baseCanvas, state.guidesCanvas, state.activePreviewCanvas, state.fallbackDocCanvas].forEach((canvas) => {
    ensureCanvasSize(canvas, data.width, data.height);
  });

  const contextKey = data.canvas_context || null;
  const contextChanged = state.canvasContextKey !== contextKey;
  if (contextChanged) {
    state.canvasContextKey = contextKey;
    state.cacheEntry = getContextEntry(contextKey, data.width, data.height);
    state.drag = null;
    state.hover = null;
  } else if (!state.cacheEntry) {
    state.cacheEntry = getContextEntry(contextKey, data.width, data.height);
  }

  const entry = state.cacheEntry;
  entry.lastAckedSeq = Math.max(Number(entry.lastAckedSeq || 0), Number(data.acked_event_seq || 0));
  entry.localSeq = Math.max(Number(entry.localSeq || 0), Number(data.acked_event_seq || 0));
  entry.touchedAt = Date.now();

  const shouldHydrateDoc = !entry.initialized;
  const token = ++state.renderToken;
  Promise.all([
    loadImage(data.base_image),
    shouldHydrateDoc ? loadImage(data.active_raw_image) : Promise.resolve(null),
    loadImage(data.guides_image),
  ])
    .then(([baseImage, activeRawImage, guidesImage]) => {
      if (token !== state.renderToken) {
        return;
      }
      drawImageToCanvas(state.baseCanvas, baseImage);
      if (activeRawImage) {
        drawImageToCanvas(entry.docCanvas, activeRawImage);
        entry.initialized = true;
      }
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
