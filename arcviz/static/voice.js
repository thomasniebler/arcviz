// Voice: speech input (speech.js) wired to the composer, local voice commands (commands.js) and
// speech output of the agent reply (SpeechSynthesis).
import { plainText } from "./markdown.js";
import { api } from "./api.js";
import { fitView, resetView, zoomCenter } from "./canvas.js";
import { addMessage } from "./chatlog.js";
import { commandHelp, matchCommand } from "./commands.js";
import { onDiagramSwitched, setFocus, showDiagram } from "./diagram.js";
import { $, el } from "./dom.js";
import { nextDiagram, refreshDiagramListLabel } from "./navigator.js";
import { ZOOM_STEP } from "./geometry.js";
import { parentFocus } from "./model.js";
import { createSpeechInput, speechSupported } from "./speech.js";
import { focusQuery, state } from "./state.js";
import { typeConfig } from "./types.js";
import { setStatus } from "./ui.js";
import { t } from "./i18n.js";

let speechEl = null, speakEl = null, synth = null;

// ---------- voice input (Web Speech API) --------------------------------------------------

function appendToInput(text) {
  const current = el.input.value;
  const needsSpace = current && !/\s$/.test(current);
  el.input.value = current + (needsSpace ? " " : "") + text;
  el.input.dispatchEvent(new Event("input", { bubbles: true }));
  el.input.focus();
  el.input.selectionStart = el.input.selectionEnd = el.input.value.length;
}

/** Execute a local voice command (see commands.js). Echoed as a system line, never sent to the LLM. */
async function runCommand(cmd) {
  const echo = (text) => addMessage("system", `🎙 ${text}`);
  if (cmd.error) { echo(cmd.error); setStatus(cmd.error); return; }
  echo(cmd.label);
  switch (cmd.id) {
    case "zoom_in": zoomCenter(ZOOM_STEP); break;
    case "zoom_out": zoomCenter(1 / ZOOM_STEP); break;
    case "zoom_reset": resetView(); break;
    case "fit": fitView(); break;
    case "focus_all": await setFocus(null); break;
    case "focus_up": await setFocus(parentFocus(state.nodes, state.focus)); break;
    case "focus": await setFocus(cmd.args.node.id); break;
    case "undo": if (!$("btn-undo").disabled) $("btn-undo").click(); else echo(t("Nichts rückgängig zu machen.")); break;
    case "redo": if (!$("btn-redo").disabled) $("btn-redo").click(); else echo(t("Nichts zu wiederholen.")); break;
    case "snapshot": {
      if (state.busy) break;
      const detail = await api(`/api/diagrams/${state.diagramId}/versions${focusQuery()}`, { method: "POST", body: JSON.stringify({ label: t("Sprachbefehl: Version sichern") }) });
      showDiagram(detail); await refreshDiagramListLabel(); break;
    }
    case "next_diagram": case "prev_diagram": await nextDiagram(cmd.id === "next_diagram" ? 1 : -1); break;
    case "toggle_source": $("btn-source").click(); break;
    case "send": if (el.input.value.trim()) el.form.requestSubmit(); else echo(t("Das Eingabefeld ist leer.")); break;
    case "clear_input": el.input.value = ""; break;
    case "help": $("voice-help").open = true; break;
    default: break;
  }
}

function fillVoiceHelp() {
  const body = $("voice-help-table").querySelector("tbody");
  body.innerHTML = "";
  for (const c of commandHelp()) {
    const tr = document.createElement("tr");
    const name = document.createElement("td"); name.textContent = c.de;
    const en = document.createElement("td"); en.textContent = c.en; en.className = "ex";
    const ex = document.createElement("td"); ex.textContent = c.examples.length ? `„${c.examples.join("“, „")}“` : "„Fokus auf prod-vnet“, „zeig mir das VNet“"; ex.className = "ex";
    tr.append(name, en, ex);
    body.appendChild(tr);
  }
}

function speechSettings() {
  return { lang: speechEl.lang.value, continuous: speechEl.continuous.checked };
}

function initSpeechInput() {
  speechEl = { mic: $("btn-mic"), continuous: $("speech-continuous"), lang: $("speech-lang"), state: $("speech-state"), preview: $("speech-preview") };
  fillVoiceHelp();
  document.addEventListener("pointerdown", (e) => { const h = $("voice-help"); if (h.open && !h.contains(e.target)) h.open = false; });

  const speech = speechSupported()
    ? createSpeechInput({
        onInterim: (text) => { speechEl.preview.textContent = text ? `… ${text}` : ""; speechEl.preview.classList.toggle("hidden", !text); },
        onFinal: (text) => {
          const cmd = matchCommand(text, { nodes: state.nodes, containerKinds: typeConfig(state.config, state.type).container_kinds });
          if (cmd) runCommand(cmd); else appendToInput(text);
        },
        onState: (s) => {
          speechEl.mic.classList.toggle("listening", s === "listening");
          speechEl.mic.classList.toggle("starting", s === "starting");
          speechEl.mic.setAttribute("aria-pressed", String(s !== "idle"));
          speechEl.state.textContent = s === "listening" ? (speechEl.continuous.checked ? t("hört zu …") : t("aufnehmen …")) : s === "starting" ? t("startet …") : "";
        },
        onError: (message) => setStatus(message),
      })
    : null;

  if (!speech) {
    // No Web Speech API (Firefox, some WebViews): keep the controls visible but inert, say why once.
    const reason = window.isSecureContext === false
      ? t("Spracheingabe braucht eine sichere Verbindung (https oder localhost).")
      : t("Spracheingabe wird von diesem Browser nicht unterstützt. Chrome, Edge oder Safari verwenden.");
    for (const c of [speechEl.mic, speechEl.continuous, speechEl.lang]) { c.disabled = true; c.title = reason; }
    delete speechEl.mic.dataset.editLocked; // disabled by its feature: read-only mode must never give it back
    speechEl.mic.classList.add("unavailable");
    speechEl.state.textContent = t("nicht verfügbar");
    speechEl.state.title = reason;
    let shown = false;
    try { shown = localStorage.getItem("arcviz.speech.noticeShown") === "1"; } catch {}
    if (!shown) {
      const note = addMessage("system", "🎙 " + t("{reason} Tippen funktioniert wie gewohnt.", { reason }));
      note.id = "speech-unavailable-notice";
      try { localStorage.setItem("arcviz.speech.noticeShown", "1"); } catch {}
    }
    return;
  }
  try {
    const savedLang = localStorage.getItem("arcviz.speech.lang");
    if (savedLang && [...speechEl.lang.options].some((o) => o.value === savedLang)) speechEl.lang.value = savedLang;
    speechEl.continuous.checked = localStorage.getItem("arcviz.speech.continuous") === "1";
  } catch {}
  speech.configure(speechSettings());
  const persist = () => {
    speech.configure(speechSettings());
    try { localStorage.setItem("arcviz.speech.lang", speechEl.lang.value); localStorage.setItem("arcviz.speech.continuous", speechEl.continuous.checked ? "1" : "0"); } catch {}
  };
  speechEl.lang.addEventListener("change", persist);
  speechEl.continuous.addEventListener("change", persist);

  // Push-to-talk: hold the button (pointer or Space/Enter). Continuous: click toggles.
  let holding = false;
  speechEl.mic.addEventListener("pointerdown", (e) => {
    if (speechEl.continuous.checked || e.button !== 0) return;
    e.preventDefault();
    holding = true;
    speechEl.mic.setPointerCapture(e.pointerId);
    speech.begin();
  });
  const release = () => { if (holding) { holding = false; speech.stop(); } };
  speechEl.mic.addEventListener("pointerup", release);
  speechEl.mic.addEventListener("pointercancel", release);
  speechEl.mic.addEventListener("lostpointercapture", release);
  speechEl.mic.addEventListener("click", () => { if (speechEl.continuous.checked) speech.toggle(); });
  speechEl.mic.addEventListener("keydown", (e) => {
    if (e.key !== " " && e.key !== "Enter") return;
    e.preventDefault();
    if (speechEl.continuous.checked) { if (!e.repeat) speech.toggle(); }
    else if (!e.repeat && !holding) { holding = true; speech.begin(); }
  });
  speechEl.mic.addEventListener("keyup", (e) => { if ((e.key === " " || e.key === "Enter") && !speechEl.continuous.checked) release(); });
  // Sending a message or switching diagrams ends the current recording.
  el.form.addEventListener("submit", () => { if (!speechEl.continuous.checked) speech.stop(); });
}

// ---------- speech output (SpeechSynthesis) of the agent reply ---------------------------------

function speakingEnabled() { return Boolean(synth) && speakEl.toggle.checked; }

/** Stop any ongoing speech output (new turn, microphone pressed, toggle off). */
function stopSpeaking() { if (synth) synth.cancel(); }

/** Read the final reply of a turn aloud in the recognition language, if enabled. */
export function speakReply(text) {
  if (!speakingEnabled() || !text || !text.trim()) return;
  stopSpeaking();
  const utterance = new SpeechSynthesisUtterance(plainText(text).trim().slice(0, 1000)); // no Markdown marks read aloud
  utterance.lang = speechEl.lang.value || "de-DE";
  const voice = synth.getVoices().find((v) => v.lang === utterance.lang) || synth.getVoices().find((v) => v.lang.startsWith(utterance.lang.slice(0, 2)));
  if (voice) utterance.voice = voice;
  synth.speak(utterance);
}

function initSpeechOutput() {
  speakEl = { label: $("speak-label"), toggle: $("speak-replies") };
  synth = "speechSynthesis" in window && typeof window.SpeechSynthesisUtterance === "function" ? window.speechSynthesis : null;
  if (!synth) return;
  speakEl.label.classList.remove("hidden");
  try { speakEl.toggle.checked = localStorage.getItem("arcviz.speech.speak") === "1"; } catch {}
  speakEl.toggle.addEventListener("change", () => {
    try { localStorage.setItem("arcviz.speech.speak", speakEl.toggle.checked ? "1" : "0"); } catch {}
    if (!speakEl.toggle.checked) stopSpeaking();
  });
  // Pressing the microphone or sending a new instruction interrupts the current speech output.
  speechEl.mic.addEventListener("pointerdown", stopSpeaking);
  speechEl.mic.addEventListener("keydown", (e) => { if (e.key === " " || e.key === "Enter") stopSpeaking(); });
  el.form.addEventListener("submit", stopSpeaking);
  onDiagramSwitched(stopSpeaking);
}

export function initVoice() {
  initSpeechInput();
  initSpeechOutput();
}
