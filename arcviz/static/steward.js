// The steward's status line (spec: docs/architecture/specs/project-steward.md): pure helpers of the Architektur page.
import { N_, t } from "./i18n.js";

const OUTCOMES = {
  proposed: N_("Vorschlag erstellt"),
  clean: N_("nichts zu melden"),
  unchanged: N_("unverändert seit der letzten Prüfung"),
  blocked: N_("übersprungen: zu viele offene Vorschläge"),
  disabled: N_("ausgeschaltet"),
  capped: N_("Tageslimit erreicht"),
};

/** The words for a run's outcome; unknown outcomes are shown as they are. */
export function outcomeText(outcome) {
  return OUTCOMES[outcome] ? t(OUTCOMES[outcome]) : String(outcome);
}

/** The status line of the project's steward: switched off, or the last run and the day's count. */
export function statusLine(status) {
  if (!status?.available) return "";
  if (!status.enabled) return t("Steward: aus für dieses Projekt.");
  const verifier = status.verifier === "configured" ? "" : ` ${t("Ohne zweites Modell nur Prüfregeln.")}`;
  const last = status.last_run ? t("Letzte Prüfung: {outcome}.", { outcome: outcomeText(status.last_run.outcome) }) : t("Noch nicht geprüft.");
  return `${t("Steward: an.")} ${last} ${t("Heute {n} von {max} Prüfungen.", { n: status.runs_today, max: status.max_runs_per_day })}${verifier}`;
}

/** Whether the person may switch the steward (owner) or run it (editor or owner). */
export function stewardRights(role) {
  return { switch: role === "owner", run: role === "owner" || role === "editor" };
}
