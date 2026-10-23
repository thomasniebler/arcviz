// Voice input on top of the Web Speech API (SpeechRecognition).
//
// Two modes: push-to-talk (recognise while the button is held) and continuous listening (toggle;
// the recogniser is restarted whenever the browser ends a session). Interim results are reported
// separately from final ones so the UI can preview them without committing text.

import { t } from "./i18n.js";

export function speechSupported() {
  // Chrome/Edge expose webkitSpeechRecognition on insecure origins too, where starting it always fails.
  return Boolean(window.SpeechRecognition || window.webkitSpeechRecognition) && window.isSecureContext !== false;
}

/** Errors after which restarting makes no sense: listening is turned off and `onend` reports idle. */
const TERMINAL_ERRORS = new Set(["not-allowed", "service-not-allowed", "audio-capture", "network", "language-not-supported"]);

/**
 * @param {object} opts
 * @param {(text: string) => void} opts.onInterim   current interim transcript (may be "")
 * @param {(text: string) => void} opts.onFinal     a final transcript segment
 * @param {(state: "idle"|"listening"|"starting") => void} opts.onState
 * @param {(message: string, code: string) => void} opts.onError
 */
export function createSpeechInput(opts) {
  if (!speechSupported()) return null;
  const Ctor = window.SpeechRecognition || window.webkitSpeechRecognition;

  let recognition = null;
  let wanted = false;          // user wants recognition to run (continuous: until toggled off)
  let continuous = false;
  let lang = "de-DE";
  let state = "idle";

  const setState = (s) => { if (s !== state) { state = s; opts.onState?.(s); } };

  function build() {
    const r = new Ctor();
    r.lang = lang;
    r.continuous = continuous;
    r.interimResults = true;
    r.maxAlternatives = 1;
    r.onstart = () => setState("listening");
    r.onresult = (event) => {
      let interim = "";
      for (let i = event.resultIndex; i < event.results.length; i++) {
        const res = event.results[i];
        const text = (res[0]?.transcript ?? "").trim();
        if (!text) continue;
        if (res.isFinal) opts.onFinal?.(text);
        else interim += (interim ? " " : "") + text;
      }
      opts.onInterim?.(interim);
    };
    r.onerror = (event) => {
      const code = event.error || "unknown";
      if (code === "no-speech" && continuous && wanted) return; // normal while waiting
      if (code === "aborted") return;
      const messages = {
        "not-allowed": t("Mikrofonzugriff verweigert. Bitte im Browser erlauben."),
        "service-not-allowed": t("Spracherkennung ist in diesem Browser nicht erlaubt."),
        "audio-capture": t("Kein Mikrofon gefunden."),
        "network": t("Spracherkennung braucht eine Netzwerkverbindung."),
        "no-speech": t("Keine Sprache erkannt."),
        "language-not-supported": t("Sprache wird nicht unterstützt."),
      };
      if (TERMINAL_ERRORS.has(code)) wanted = false; // otherwise continuous mode would restart in a tight loop
      opts.onError?.(messages[code] || t("Spracherkennung: {code}", { code }), code);
    };
    r.onend = () => {
      opts.onInterim?.("");
      recognition = null;
      if (wanted && continuous) { start(); return; } // browser sessions time out; keep listening
      wanted = false;
      setState("idle");
    };
    return r;
  }

  function start() {
    if (recognition) return;
    try {
      recognition = build();
      setState("starting");
      recognition.start();
    } catch (err) {
      recognition = null;
      wanted = false;
      setState("idle");
      opts.onError?.(t("Spracherkennung konnte nicht starten: {error}", { error: err.message || err }), "start-failed");
    }
  }

  return {
    get state() { return state; },
    get active() { return wanted; },
    configure({ lang: l, continuous: c } = {}) {
      if (l) lang = l;
      if (typeof c === "boolean") continuous = c;
      if (recognition) { wanted = false; recognition.stop(); } // apply on next start
    },
    /** Push-to-talk: begin listening until stop() is called. */
    begin() { wanted = true; start(); },
    /** Stop after the current utterance is delivered (push-to-talk release, continuous toggle off). */
    stop() { wanted = false; if (recognition) recognition.stop(); else setState("idle"); },
    /** Continuous mode toggle. Returns the new active state. */
    toggle() { if (wanted) { this.stop(); return false; } this.begin(); return true; },
    /** Abort immediately, discarding pending results. */
    abort() { wanted = false; if (recognition) recognition.abort(); setState("idle"); },
  };
}
