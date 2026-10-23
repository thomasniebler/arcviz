// Splitter between chat and canvas: drag, keyboard, double-click reset, ARIA, persisted width.
import { $ } from "./dom.js";
import { CHAT_MIN, SPLITTER_WIDTH, chatWidthMax, clampChatWidth } from "./geometry.js";

let layout = null, splitter = null;

function chatWidth() { return document.querySelector(".chat").getBoundingClientRect().width; }

/** Keep the separator's ARIA value inside a declared pixel range (min/max follow the layout width). */
function updateSplitterAria() {
  const max = chatWidthMax(layout.clientWidth);
  splitter.setAttribute("aria-valuemin", String(CHAT_MIN));
  splitter.setAttribute("aria-valuemax", String(max));
  splitter.setAttribute("aria-valuenow", String(Math.round(Math.min(Math.max(chatWidth(), CHAT_MIN), max))));
}

function setChatWidth(px, persist = true) {
  const width = clampChatWidth(px, layout.clientWidth);
  layout.style.setProperty("--chat-width", `${width}px`);
  updateSplitterAria();
  if (persist) { try { localStorage.setItem("arcviz.chatWidth", String(width)); } catch {} }
  return width;
}

export function initSplitter() {
  layout = document.querySelector(".layout");
  splitter = $("splitter");
  splitter.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    splitter.setPointerCapture(e.pointerId);
    splitter.classList.add("dragging");
  });
  splitter.addEventListener("pointermove", (e) => {
    if (!splitter.hasPointerCapture(e.pointerId)) return;
    setChatWidth(e.clientX - layout.getBoundingClientRect().left - SPLITTER_WIDTH / 2);
  });
  splitter.addEventListener("pointerup", (e) => { splitter.releasePointerCapture(e.pointerId); splitter.classList.remove("dragging"); });
  splitter.addEventListener("lostpointercapture", () => splitter.classList.remove("dragging"));
  splitter.addEventListener("dblclick", () => { layout.style.removeProperty("--chat-width"); try { localStorage.removeItem("arcviz.chatWidth"); } catch {} updateSplitterAria(); });
  splitter.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 96 : 24;
    if (e.key === "ArrowLeft") { e.preventDefault(); setChatWidth(chatWidth() - step); }
    else if (e.key === "ArrowRight") { e.preventDefault(); setChatWidth(chatWidth() + step); }
    else if (e.key === "Home") { e.preventDefault(); setChatWidth(CHAT_MIN); }
    else if (e.key === "End") { e.preventDefault(); setChatWidth(Infinity); }
  });
  window.addEventListener("resize", () => { if (layout.style.getPropertyValue("--chat-width")) setChatWidth(chatWidth(), false); else updateSplitterAria(); });
  try {
    const saved = Number(localStorage.getItem("arcviz.chatWidth"));
    if (saved > 0) setChatWidth(saved, false);
  } catch {}
  updateSplitterAria();
}
