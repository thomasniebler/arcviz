// Pan/zoom and splitter maths (spec: docs/architecture/specs/frontend-modules.md).
// Pure module: no DOM access, unit-tested with node --test.

export const ZOOM_MIN = 0.1, ZOOM_MAX = 4, ZOOM_STEP = 1.2;
export const FIT_PADDING = 24;

/** Zoom `view` by `factor` keeping the diagram point under (cx, cy) (container coordinates) fixed. */
export function zoomAt(view, factor, cx, cy) {
  const scale = Math.min(Math.max(view.scale * factor, ZOOM_MIN), ZOOM_MAX);
  const k = scale / view.scale;
  return { scale, x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k };
}

/** Transform that fits a diagram of `size` into a container `box` with padding, centred; null if impossible.
 * The fit may go below the interactive zoom minimum: the whole diagram must become visible. */
export function fitTransform(size, box, pad = FIT_PADDING) {
  if (!size) return null;
  const cw = box.width - 2 * pad, ch = box.height - 2 * pad;
  if (cw <= 0 || ch <= 0) return null;
  const scale = Math.min(Math.min(cw / size.width, ch / size.height), ZOOM_MAX);
  return { scale, x: pad + (cw - size.width * scale) / 2, y: pad + (ch - size.height * scale) / 2 };
}

/** Zoom factor of a wheel event. deltaMode 0 = pixels, 1 = lines, 2 = pages. Trackpad pinch arrives
 * as ctrl+wheel with small pixel deltas; mouse wheels send ±100 px or ±3 lines per notch. */
export function wheelFactor({ deltaY, deltaMode, ctrlKey }, pageHeight) {
  const unit = deltaMode === 1 ? 16 : deltaMode === 2 ? pageHeight : 1;
  return Math.exp(-deltaY * unit * (ctrlKey ? 0.01 : 0.0015));
}

export function viewTransform(view) { return `translate(${view.x}px, ${view.y}px) scale(${view.scale})`; }

export function zoomLabel(scale) { return `${Math.round(scale * 100)} %`; }

export const CHAT_MIN = 280, CANVAS_MIN = 320, SPLITTER_WIDTH = 6;

/** Largest chat width that leaves the canvas its minimum (never below the chat minimum). */
export function chatWidthMax(layoutWidth) { return Math.max(layoutWidth - CANVAS_MIN - SPLITTER_WIDTH, CHAT_MIN); }

export function clampChatWidth(px, layoutWidth) {
  return Math.round(Math.min(Math.max(px, CHAT_MIN), chatWidthMax(layoutWidth)));
}
