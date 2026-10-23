// Release highlights (spec: docs/architecture/specs/whats-new.md): when the banner shows and what it says.
// Pure module: no DOM access.
import { t } from "./i18n.js";

/** Where the full changelog is served. */
export const CHANGELOG_PATH = "/docs/changelog/";

/** The storage key that remembers the dismissal of one release's banner. */
export function dismissKey(entry) { return `arcviz.whatsnew.${entry.version}`; }

/** The highlights the server sends (`config.whats_new`) when they are usable, else null. */
export function highlights(config) {
  const entry = config?.whats_new;
  if (!entry || typeof entry.version !== "string" || !Array.isArray(entry.items) || !entry.items.length) return null;
  const items = entry.items.filter((i) => i && typeof i.text === "string" && i.text.trim());
  return items.length ? { version: entry.version, items: items.map((i) => ({ emoji: String(i.emoji || "•"), text: i.text })) } : null;
}

/** Whether the banner shows on its own: highlights exist and the user has not dismissed this release's. */
export function bannerVisible(entry, dismissed) { return Boolean(entry) && !dismissed; }

/** Heading of the banner; the running version when it differs from the highlighted release. */
export function bannerTitle(entry, version) {
  if (entry.version === version || !version) return t("Neu in Version {version}", { version: entry.version });
  return t("Neu in Version {version} (läuft: {running})", { version: entry.version, running: version });
}
