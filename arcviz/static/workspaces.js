// The current user and sharing (spec: docs/architecture/specs/personal-workspaces.md): the user's name in the header
// and the sharing dialog of the open project. Only the project's owner shares (viewer or editor) and revokes; anybody
// else sees the owner and their own role.
import { addedText, personLabel, roleLabel, SHARE_ROLES, shareRows, suggestionLabel, wantsSuggestions } from "./access.js";
import { api } from "./api.js";
import { $, option } from "./dom.js";
import { currentProject, currentProjectInfo, guarded, loadDiagramList, onProjectShown } from "./navigator.js";
import { t } from "./i18n.js";
import { state } from "./state.js";

function say(id, text) { $(id).textContent = text || ""; }

function accessPath(projectId, userId) {
  const base = `/api/projects/${encodeURIComponent(projectId)}/access`;
  return userId === undefined ? base : `${base}/${encodeURIComponent(userId)}`;
}

async function openSharing() {
  const projectId = currentProject();
  if (!projectId) return;
  say("share-status", "");
  renderSharing(await api(accessPath(projectId)));
  $("share-dialog").showModal();
}

function renderSharing(access) {
  const owner = access.role === "owner";
  $("share-heading").textContent = t("„{title}“ freigeben", { title: currentProjectInfo()?.title ?? access.project });
  $("share-hint").textContent = owner
    ? t("Nur Personen, für die Sie das Projekt freigeben, sehen es. Bearbeiten erlaubt Änderungen; freigeben und löschen können nur Besitzer.")
    : t("Besitzer: {owner}. Ihre Rolle: {role}. Nur der Besitzer ändert Freigaben.", { owner: personLabel(access.owner), role: roleLabel(access.role) });
  $("share-add").classList.toggle("hidden", !owner);
  $("share-add-note").classList.toggle("hidden", !owner);
  const list = $("share-list");
  list.innerHTML = "";
  for (const row of shareRows(access)) {
    // Built with DOM APIs: names and e-mail addresses come from the identity provider.
    const li = document.createElement("li");
    li.className = "share-row";
    li.dataset.userId = row.id;
    const label = document.createElement("span");
    label.className = "share-name";
    label.textContent = row.id === state.me?.id ? t("{label} (Sie)", { label: row.label }) : row.label;
    const select = document.createElement("select");
    select.className = "share-role";
    select.setAttribute("aria-label", t("Rolle von {label}", { label: row.label }));
    for (const role of row.roles) select.appendChild(option(role, roleLabel(role), role === row.role));
    select.disabled = !row.editable;
    select.addEventListener("change", () => guarded(async () => {
      renderSharing(await api(accessPath(access.project), { method: "POST", body: JSON.stringify({ user: row.id, role: select.value }) }));
      say("share-status", `${row.label}: ${roleLabel(select.value)}`);
    }));
    li.append(label, select);
    if (row.editable) {
      const remove = document.createElement("button");
      remove.type = "button"; remove.className = "small danger share-remove"; remove.textContent = t("Entfernen");
      remove.addEventListener("click", () => guarded(async () => {
        if (!confirm(t("Freigabe für {label} entfernen? Die Person sieht das Projekt danach nicht mehr.", { label: row.label }))) return;
        renderSharing(await api(accessPath(access.project, row.id), { method: "DELETE" }));
        say("share-status", t("Freigabe für {label} entfernt.", { label: row.label }));
      }));
      li.appendChild(remove);
    }
    if (row.note) {
      const note = document.createElement("span");
      note.className = "muted share-note";
      note.textContent = row.note;
      li.appendChild(note);
    }
    list.appendChild(li);
  }
}

function updateProjectActions(project) {
  $("btn-project-share").disabled = !project;
}

function showMe() {
  const me = state.me;
  const detail = me ? [me.email, me.id !== me.name ? me.id : ""].filter(Boolean).join(" · ") : "";
  $("me").textContent = me?.name || t("Konto");
  $("me").title = detail;
  $("settings-user").textContent = me ? (detail ? t("Angemeldet als {name} ({detail})", { name: me.name, detail }) : t("Angemeldet als {name}", { name: me.name })) : "";
}

let suggested = []; // the users the share dialog suggests right now (GET /api/users)
let asked = 0; // the newest suggestion request: an older answer arriving later is dropped

/** Suggest signed-in users while the owner types (ADR-0165); choosing one fills in the user id. */
async function suggest() {
  const text = $("share-add-input").value;
  const list = $("share-add-suggestions");
  if (!wantsSuggestions(text, suggested)) {
    if (text.trim().length < 2) { suggested = []; list.innerHTML = ""; }
    return;
  }
  const mine = ++asked;
  let users = [];
  try {
    users = (await api(`/api/users?q=${encodeURIComponent(text.trim())}`)).users || [];
  } catch {
    return; // suggestions are a convenience: typing an id or e-mail still works
  }
  if (mine !== asked) return;
  suggested = users;
  list.innerHTML = "";
  for (const user of users) {
    const opt = document.createElement("option");
    opt.value = user.id;
    opt.label = suggestionLabel(user);
    opt.textContent = suggestionLabel(user);
    list.appendChild(opt);
  }
}

export function initWorkspaces() {
  let typing = null;
  $("share-add-input").addEventListener("input", () => {
    clearTimeout(typing);
    typing = setTimeout(() => guarded(suggest), 200);
  });
  const roleSelect = $("share-add-role");
  for (const role of SHARE_ROLES) roleSelect.appendChild(option(role, roleLabel(role), role === "viewer"));
  const closeProjectMenu = () => $("project-menu").removeAttribute("open");
  $("btn-project-share").addEventListener("click", () => { closeProjectMenu(); guarded(openSharing); });
  $("share-close").addEventListener("click", async () => {
    $("share-dialog").close();
    await loadDiagramList(null, currentProject()); // labels of the project list stay current
  });
  $("share-add").addEventListener("submit", (e) => {
    e.preventDefault();
    guarded(async () => {
      const user = $("share-add-input").value.trim(), role = $("share-add-role").value, projectId = currentProject();
      if (!user || !projectId) return;
      const access = await api(accessPath(projectId), { method: "POST", body: JSON.stringify({ user, role }) });
      renderSharing(access);
      $("share-add-input").value = "";
      suggested = []; $("share-add-suggestions").innerHTML = "";
      say("share-status", addedText(access.added, user, role)); // exactly who it was shared with, not just what was typed
    });
  });
  onProjectShown(updateProjectActions);
  showMe();
}

/** The caller from GET /api/me, shown in the header. */
export function setMe(me) {
  state.me = me;
  showMe();
}
