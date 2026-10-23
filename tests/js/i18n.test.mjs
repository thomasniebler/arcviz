// Frontend internationalization (spec: docs/architecture/specs/internationalization.md, ADR-0199): the pure functions,
// and the catalog rules: every German text passed to `t("…")` / `N_("…")` or marked in index.html has an English entry,
// every entry is used, placeholders agree. Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { N_, applyDom, interpolate, resolveLanguage, setLanguage, t } from "../../arcviz/static/i18n.js";

const staticDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "arcviz", "static");
const localeDir = join(staticDir, "locales");

test("resolveLanguage: stored choice, then browser list, then German", () => {
  assert.equal(resolveLanguage("en", ["de-DE"]), "en");
  assert.equal(resolveLanguage(null, ["fr-FR", "en-US", "de"]), "en");
  assert.equal(resolveLanguage("xx", ["de-AT"]), "de");
  assert.equal(resolveLanguage(null, ["fr"]), "de");
  assert.equal(resolveLanguage(null, undefined), "de");
});

test("t returns German without a catalog entry and English with one", () => {
  setLanguage("en", { "Hallo {name}": "Hello {name}" });
  assert.equal(t("Hallo {name}", { name: "Ada" }), "Hello Ada");
  assert.equal(t("Ohne Eintrag {x}", { x: 1 }), "Ohne Eintrag 1");
  assert.equal(t("Unbekannt {y}", {}), "Unbekannt {y}");
  setLanguage("de", { "Hallo {name}": "Hello {name}" });
  assert.equal(t("Hallo {name}", { name: "Ada" }), "Hallo Ada");
  assert.equal(N_("Text"), "Text");
  assert.equal(interpolate("a", undefined), "a");
});

test("applyDom translates marked text and attributes", () => {
  const make = (attrs, text) => {
    const node = { dataset: attrs.i18n === undefined ? {} : { i18n: attrs.i18n }, textContent: text, attrs: { ...attrs.attrs }, getAttribute(n) { return this.attrs[n] ?? null; }, setAttribute(n, v) { this.attrs[n] = v; } };
    return node;
  };
  const text = make({ i18n: "" }, "  Speichern ");
  const title = make({ attrs: { "data-i18n-title": "", title: "Schließen" } }, "");
  const root = { querySelectorAll: (sel) => (sel === "[data-i18n]" ? [text] : sel === "[data-i18n-title]" ? [title] : []) };
  setLanguage("en", { Speichern: "Save", Schließen: "Close" });
  applyDom(root);
  assert.equal(text.textContent, "Save");
  assert.equal(title.attrs.title, "Close");
  setLanguage("de");
});

function catalog() {
  const names = JSON.parse(readFileSync(join(localeDir, "index.json"), "utf8"));
  const files = readdirSync(join(localeDir, "en")).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5)).sort();
  assert.deepEqual([...names].sort(), files, "locales/index.json lists exactly the fragments in locales/en");
  return Object.assign({}, ...names.map((n) => JSON.parse(readFileSync(join(localeDir, "en", `${n}.json`), "utf8"))));
}

function literals() {
  const texts = new Set();
  const problems = [];
  const call = /(?<![\w.$])(?:t|N_)\(\s*("(?:[^"\\\n]|\\.)*"|`[^`]*`|'[^'\n]*'|[^"'`\s)][^,)]*)/g;
  for (const file of readdirSync(staticDir).filter((f) => f.endsWith(".js") && f !== "i18n.js")) {
    const src = readFileSync(join(staticDir, file), "utf8");
    for (const m of src.matchAll(call)) {
      const arg = m[1];
      const line = src.slice(0, m.index).split("\n").length;
      if (arg.startsWith('"')) texts.add(JSON.parse(arg));
      else if (/^[a-z_$][\w$.\[\]]*$/i.test(arg.trim())) continue; // a variable holding an N_ text
      else problems.push(`${file}:${line}: ${arg.slice(0, 40)} (use a double-quoted literal with {placeholders})`);
    }
  }
  const html = readFileSync(join(staticDir, "index.html"), "utf8");
  for (const m of html.matchAll(/<(\w+)([^>]*)>([^<]*)/g)) {
    const [, , attrs, inner] = m;
    if (/\bdata-i18n(?:=""|=")?(?=[\s>"]|$)/.test(attrs) && /\bdata-i18n(?!-)/.test(attrs)) {
      const key = (/\bdata-i18n="([^"]+)"/.exec(attrs) || [])[1] ?? inner.trim();
      if (key) texts.add(key);
    }
    for (const attr of ["title", "placeholder", "aria-label"]) {
      if (new RegExp(`\\bdata-i18n-${attr}\\b`).test(attrs)) {
        const v = (new RegExp(`(?:^|\\s)${attr}="([^"]*)"`).exec(attrs) || [])[1];
        if (v) texts.add(v.replace(/&quot;/g, '"').replace(/&amp;/g, "&"));
      }
    }
  }
  return { texts, problems };
}

test("every literal is extractable", () => {
  assert.deepEqual(literals().problems, []);
});

test("every text has an English entry and every entry is used", () => {
  const entries = catalog();
  const { texts } = literals();
  assert.deepEqual([...texts].filter((x) => !(x in entries)).sort(), [], "texts without an English entry (static/locales/en/*.json)");
  assert.deepEqual(Object.keys(entries).filter((x) => !texts.has(x)).sort(), [], "entries no t() / N_() / data-i18n uses");
});

test("placeholders match between German and English", () => {
  const names = (s) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
  for (const [de, en] of Object.entries(catalog())) {
    assert.deepEqual(names(en), names(de), de);
    assert.ok(en.trim(), de);
  }
});

test("fragments do not translate the same German text differently", () => {
  const names = JSON.parse(readFileSync(join(localeDir, "index.json"), "utf8"));
  const seen = {};
  const conflicts = [];
  for (const n of names) {
    for (const [de, en] of Object.entries(JSON.parse(readFileSync(join(localeDir, "en", `${n}.json`), "utf8")))) {
      if (de in seen && seen[de].en !== en) conflicts.push(`${de}: ${seen[de].file} "${seen[de].en}" vs ${n} "${en}"`);
      seen[de] ??= { en, file: n };
    }
  }
  assert.deepEqual(conflicts, [], "one German text, one English text: reword the German source to tell contexts apart");
});
