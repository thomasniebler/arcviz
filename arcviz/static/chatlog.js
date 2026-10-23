// Chat transcript rendering: message bubbles, summaries, tool lines.
import { el } from "./dom.js";
import { authorName, chatEntryMeta, describeCall } from "./format.js";
import { parseBlocks } from "./markdown.js";
import { changedDiagramsLabel, describeProjectCall } from "./projectchat.js";
import { state } from "./state.js";
import { N_, t } from "./i18n.js";

const EMPTY_TEXT = {
  diagram: N_("Beschreibe, was am Diagramm geändert werden soll. Ich wende die Änderungen auf das Modell an und rendere das Ergebnis sofort rechts."),
  requirements: N_("Anforderungs-Chat: Sag mir, welche Anforderungen aufgenommen werden sollen, oder frag nach dem Stand. Ich nehme sie so auf, wie du sie sagst, schlage normalisierte Formulierungen vor und bereite Durchgänge vor. Übernehmen und Löschen machst du in der Tabelle. Lange Listen importierst du besser über „Importieren…“."),
  project: N_("Projekt-Chat: Beschreibe, was im Projekt entstehen oder sich ändern soll. Ich lege Diagramme an, öffne sie und bearbeite sie nacheinander; das bearbeitete Diagramm erscheint rechts."),
};

// ---- Markdown of assistant replies (spec: chat-markdown.md): the tree of markdown.js, built with DOM APIs only ----

const INLINE_TAGS = { strong: "strong", em: "em", del: "del" };

function inlineNodes(nodes) {
  return nodes.map((n) => {
    if (n.type === "text") return document.createTextNode(n.text);
    if (n.type === "br") return document.createElement("br");
    if (n.type === "code") { const c = document.createElement("code"); c.textContent = n.text; return c; }
    if (n.type === "link") {
      const a = document.createElement("a");
      a.href = n.href; // markdown.safeHref: http(s) and mailto only
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      a.append(...inlineNodes(n.children));
      return a;
    }
    const tag = document.createElement(INLINE_TAGS[n.type] || "span");
    tag.append(...inlineNodes(n.children || []));
    return tag;
  });
}

function blockNodes(blocks) {
  return blocks.map((b) => {
    let node;
    if (b.type === "paragraph") { node = document.createElement("p"); node.append(...inlineNodes(b.children)); }
    else if (b.type === "heading") { node = document.createElement(`h${Math.min(6, b.level + 2)}`); node.append(...inlineNodes(b.children)); } // # is h3: a bubble is no page
    else if (b.type === "rule") node = document.createElement("hr");
    else if (b.type === "quote") { node = document.createElement("blockquote"); node.append(...blockNodes(b.children)); }
    else if (b.type === "code") {
      node = document.createElement("pre");
      const code = document.createElement("code");
      code.textContent = b.text;
      if (b.lang) code.dataset.lang = b.lang;
      node.appendChild(code);
    } else if (b.type === "list") {
      node = document.createElement(b.ordered ? "ol" : "ul");
      if (b.ordered && b.start !== 1) node.start = b.start;
      for (const item of b.items) {
        const li = document.createElement("li");
        // a tight item (one paragraph, maybe a nested list) shows its text without a paragraph margin
        if (item[0]?.type === "paragraph") { li.append(...inlineNodes(item[0].children), ...blockNodes(item.slice(1))); }
        else li.append(...blockNodes(item));
        node.appendChild(li);
      }
    } else if (b.type === "table") {
      node = document.createElement("div");
      node.className = "md-table";
      const table = document.createElement("table");
      const row = (cells, tag) => {
        const tr = document.createElement("tr");
        cells.forEach((cell, k) => {
          const td = document.createElement(tag);
          if (b.align[k]) td.style.textAlign = b.align[k];
          td.append(...inlineNodes(cell));
          tr.appendChild(td);
        });
        return tr;
      };
      const head = document.createElement("thead");
      head.appendChild(row(b.header, "th"));
      const body = document.createElement("tbody");
      for (const r of b.rows) body.appendChild(row(r, "td"));
      table.append(head, body);
      node.appendChild(table);
    } else node = document.createTextNode("");
    return node;
  });
}

/** Show `text` as Markdown in a message bubble (assistant replies and summaries); replaces its content. */
export function setRichText(div, text) {
  const body = document.createElement("div");
  body.className = "md";
  body.append(...blockNodes(parseBlocks(text)));
  div.replaceChildren(body);
}

export function addMessage(role, text, meta) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  if (role === "assistant") setRichText(div, text);
  else div.textContent = text;
  if (meta) {
    const m = document.createElement("span");
    m.className = "meta";
    m.textContent = meta;
    div.appendChild(m);
  }
  el.messages.appendChild(div);
  el.messages.scrollTop = el.messages.scrollHeight;
  return div;
}

export function addSummary(entry) {
  const details = document.createElement("details");
  details.className = "msg summary";
  const summary = document.createElement("summary");
  const turns = entry.covers?.turns;
  summary.textContent = turns ? `Earlier conversation (${turns} turns summarised)` : "Earlier conversation (summarised)";
  const body = document.createElement("div");
  setRichText(body, entry.content);
  details.append(summary, body);
  el.messages.appendChild(details);
  return details;
}

/** Show a transcript; `scope` picks the hint shown for an empty one. Project entries also name their diagrams. */
export function showChat(chat, { scope = "diagram" } = {}) {
  const notice = document.getElementById("speech-unavailable-notice");
  el.messages.innerHTML = "";
  if (notice) el.messages.appendChild(notice); // keep the one-time voice notice above the transcript
  if (!chat.length) {
    const hint = EMPTY_TEXT[scope] || EMPTY_TEXT.diagram;
    addMessage("assistant", t(hint));
    return;
  }
  for (const entry of chat) {
    if (entry.role === "summary") addSummary(entry);
    else if (entry.role === "user") addMessage("user", entry.content, entry.author && entry.author !== state.me?.id ? authorName(entry) : ""); // someone else wrote it (M20)
    else addMessage(entry.error ? "error" : "assistant", entry.content, [changedDiagramsLabel(entry.diagrams), chatEntryMeta(entry)].filter(Boolean).join(" · "));
  }
}

export function makeToolLine(ev) {
  const div = document.createElement("div");
  div.className = "msg tool";
  div.dataset.call = ev.id;
  div.textContent = describeProjectCall(ev.name, ev.arguments || {}) ?? describeCall(ev.name, ev.arguments);
  return div;
}
