// Requests to the server's /api/*. Errors carry the server's `detail`; a 403 (the caller's role does not allow the
// change, spec: workspaces-and-sharing.md) carries an explanation.
import { forbiddenText } from "./access.js";
import { getLanguage, t } from "./i18n.js";

/** Where a 401 sends the browser: the login page, coming back to the current page afterwards. */
export function loginUrl(location) {
  return `/login?next=${encodeURIComponent(location.pathname + location.search)}`;
}

/** `fetch` for the API: a 401 (authentication on, no or an expired session) goes to the login page. */
export async function apiFetch(path, options = {}) {
  const res = await fetch(path, { ...options, headers: { "Accept-Language": getLanguage(), ...(options.headers || {}) } });
  if (res.status === 401) {
    window.location.assign(loginUrl(window.location));
    throw new Error(t("Anmeldung erforderlich"));
  }
  return res;
}

/** JSON request to the API; a failure throws an Error with the server's text, its `status` and response `body`
 * (a 412 of a stale `If-Match` carries the current revision there, spec: collaboration.md). */
export async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const res = await apiFetch(path, { ...options, headers });
  if (res.status === 204) return null;
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(errorText(res.status, body.detail || res.statusText)), { status: res.status, body });
  return body;
}

/** The `If-Match` header for a revision (none without one). */
export function ifMatch(revision) { return revision ? { "If-Match": `"${revision}"` } : {}; }

/** The message of a failed request: the server's detail, or for 403 a German explanation. */
export function errorText(status, detail) {
  return status === 403 ? `${forbiddenText()} (${detail})` : detail;
}
