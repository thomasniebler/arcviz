// First steps (spec: docs/architecture/specs/onboarding.md): the starter cards of the empty project and the guided tour.
// The tour never opens by itself (it would cover the page for scripted clients and surprise returning users); the empty
// project offers it, and Hilfe -> Rundgang starts it.
import { api } from "./api.js";
import { $, el } from "./dom.js";
import { currentProject, guarded, loadDiagramList, onProjectShown } from "./navigator.js";
import { availableStarters, moveStep, nextVisible, starterBody, TOUR_STEPS } from "./onboarding.js";
import { t } from "./i18n.js";
import { state } from "./state.js";

// ---- starters -----------------------------------------------------------------------------------------------------

async function startFrom(starter, example) {
  const created = await api("/api/diagrams", { method: "POST", body: JSON.stringify(starterBody(starter, currentProject(), example)) });
  await loadDiagramList(created.diagram.id);
  if (!example) { // a first message that fits the type, ready to send or to change
    el.input.value = t(starter.prompt);
    el.input.focus();
  }
}

function card(starter) {
  // Built with DOM APIs, like every other list in the UI.
  const box = document.createElement("div");
  box.className = "starter-card";
  const heading = document.createElement("strong");
  heading.textContent = t(starter.title);
  const blurb = document.createElement("span");
  blurb.className = "muted";
  blurb.textContent = t(starter.blurb);
  const row = document.createElement("div");
  row.className = "starter-actions";
  for (const [label, example, title] of [
    [t("Mit Chat starten"), false, t("Legt ein leeres Diagramm an und trägt eine passende erste Nachricht in den Chat ein")],
    [t("Beispiel ansehen"), true, t("Legt ein fertiges Beispiel an, das Sie per Chat weiter ändern können")],
  ]) {
    const button = document.createElement("button");
    button.type = "button"; button.textContent = label; button.title = title;
    button.dataset.starter = `${starter.type}:${example ? "example" : "new"}`;
    button.addEventListener("click", () => guarded(() => startFrom(starter, example)));
    row.appendChild(button);
  }
  box.append(heading, blurb, row);
  return box;
}

function fillStarters() {
  $("starter-cards").replaceChildren(...availableStarters(state.config).map(card));
}

// ---- tour ---------------------------------------------------------------------------------------------------------

let current = null; // index of the shown step, null while the tour is closed
let marked = null;  // the highlighted element

function visible(step) {
  const target = document.querySelector(step.target);
  return target && target.getClientRects().length ? target : null; // a collapsed or hidden control has no box
}

function unmark() { marked?.classList.remove("tour-target"); marked = null; }

function showStep(from, by) {
  const index = nextVisible(from, by, (i) => visible(TOUR_STEPS[i]));
  if (index === null) { if (by > 0 || current === null) closeTour(); return; } // back past the first shown step: stay
  current = index;
  const step = TOUR_STEPS[index], target = visible(step);
  unmark();
  marked = target;
  target.classList.add("tour-target");
  $("tour-title").textContent = t(step.title);
  $("tour-text").textContent = t(step.text);
  $("tour-count").textContent = `${index + 1} / ${TOUR_STEPS.length}`;
  $("tour-prev").disabled = nextVisible(index - 1, -1, (i) => visible(TOUR_STEPS[i])) === null;
  $("tour-next").textContent = index === TOUR_STEPS.length - 1 ? t("Fertig") : t("Weiter");
  $("tour").classList.remove("hidden");
  $("tour-next").focus();
}

function closeTour() {
  current = null;
  unmark();
  $("tour").classList.add("hidden");
}

function startTour() {
  $("help-menu").open = false;
  showStep(0, 1);
}

export function initWelcome() {
  $("btn-tour").addEventListener("click", startTour);
  $("btn-tour-start").addEventListener("click", startTour);
  $("tour-close").addEventListener("click", closeTour);
  $("tour-prev").addEventListener("click", () => showStep(current - 1, -1));
  $("tour-next").addEventListener("click", () => {
    const next = moveStep(current, 1);
    if (next === null) closeTour(); else showStep(next, 1);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && current !== null) closeTour(); });
  onProjectShown(fillStarters); // config is loaded before the first project is shown
}
