// No unsafe HTML sinks in the frontend (#119, spec: docs/architecture/specs/hardening.md): server- or user-derived
// text reaches the DOM only through textContent / DOM APIs. Assigning markup is allowed for
//   - clearing (`.innerHTML = ""`),
//   - a constant string literal without interpolation (static markup written in the module),
//   - a line marked `// trusted-html: <reason>`, and only in the files listed in TRUSTED below (none since #197),
//   - parsing into an inert `<template>` (`template.innerHTML = …` marked `// inert-html: <reason>`), only in the
//     files listed in INERT below, and only when `sanitizeSvg(` follows before the function returns (Mermaid's output,
//     #197: nothing of it reaches the page before the allow-list ran).
// insertAdjacentHTML, outerHTML, document.write, `+=` on innerHTML and DOMParser "text/html" are never allowed.
// Run with: node --test tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const staticDir = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "arcviz", "static");
const modules = readdirSync(staticDir).filter((f) => f.endsWith(".js")).sort();
// File -> number of reviewed `trusted-html` sinks. Adding one is a reviewed change to this list.
const TRUSTED = {};
// File -> number of reviewed inert parses (sanitized before use). Adding one is a reviewed change to this list.
const INERT = { "canvas.js": 1 };

/** The problems of one module's source (exported for the self-test below). */
export function sinkProblems(src) {
  const problems = [];
  const trusted = [];
  const inert = [];
  const lines = src.split("\n");
  lines.forEach((line, i) => {
    const code = line.replace(/\/\/.*$/, "");
    const at = `line ${i + 1}`;
    if (/insertAdjacentHTML|outerHTML\s*=|document\.write|\.innerHTML\s*\+=/.test(code)) problems.push(`${at}: forbidden HTML sink`);
    if (/parseFromString\([^)]*["']text\/html["']/.test(code)) problems.push(`${at}: DOMParser text/html`);
    // Every assignment on the line. Constant only when the *whole* right-hand side is one literal, ended by `;` or
    // the end of the line: `"" + userText`, `"a" + b` or a template with `${}` are not.
    for (const m of code.matchAll(/\.innerHTML\s*=(?!=)\s*/g)) {
      const rest = code.slice(m.index + m[0].length);
      const constant = /^("[^"\\]*"|'[^'\\]*'|`[^`$\\]*`)\s*(;|$)/.test(rest);
      if (constant) continue;
      if (/\/\/\s*inert-html:\s*\S/.test(line)) {
        const rest = lines.slice(i + 1);
        const end = rest.findIndex((l) => /^\s*(return\b|\}$)/.test(l));
        const sanitized = rest.slice(0, end === -1 ? rest.length : end + 1).some((l) => l.includes("sanitizeSvg("));
        if (/\btemplate\.innerHTML\s*=/.test(code) && sanitized) { inert.push(at); continue; }
        problems.push(`${at}: an inert parse must assign template.innerHTML and call sanitizeSvg( before returning`);
        continue;
      }
      if (/\/\/\s*trusted-html:\s*\S/.test(line)) { trusted.push(at); continue; }
      problems.push(`${at}: innerHTML assigned a non-constant value (use textContent or DOM APIs)`);
    }
  });
  return { problems, trusted, inert };
}

test("no module assigns server- or user-derived strings as markup", () => {
  for (const file of modules) {
    const { problems, trusted, inert } = sinkProblems(readFileSync(join(staticDir, file), "utf8"));
    assert.deepEqual(problems, [], `${file}: ${problems.join("; ")}`);
    assert.equal(trusted.length, TRUSTED[file] || 0, `${file}: trusted-html sinks changed (${trusted.join(", ")}); review and update TRUSTED`);
    assert.equal(inert.length, INERT[file] || 0, `${file}: inert parses changed (${inert.join(", ")}); review and update INERT`);
  }
});

test("the rule catches the patterns of #119", () => {
  const bad = [
    "el.diagram.innerHTML = `<div>${err.message}</div>`;",
    "x.innerHTML = message;",
    "x.innerHTML += '<b>';",
    "x.insertAdjacentHTML('beforeend', s);",
    "x.outerHTML = s;",
    "document.write(s);",
    "new DOMParser().parseFromString(s, \"text/html\");",
    "x.innerHTML = svg; // trusted-html:",
    'a.innerHTML = ""; b.innerHTML = text;',
    "x.innerHTML = `a ${b}`;",
    'x.innerHTML = "" + userText;',
    "x.innerHTML = '<b>' + name + '</b>';",
    "x.innerHTML = `<td>` + cell;",
    'a.innerHTML = ""; b.innerHTML = "" + text;',
  ];
  for (const line of bad) assert.notDeepEqual(sinkProblems(line).problems, [], line);
  const inertBad = [
    "x.innerHTML = svg; // inert-html: not a template",
    "template.innerHTML = svg; // inert-html: never sanitized\n  return template.content.firstElementChild;",
  ];
  for (const src of inertBad) assert.notDeepEqual(sinkProblems(src).problems, [], src);
  const inertOk = "template.innerHTML = svg; // inert-html: reviewed\n  const root = template.content.firstElementChild;\n  sanitizeSvg(root);\n  return root;";
  assert.deepEqual(sinkProblems(inertOk), { problems: [], trusted: [], inert: ["line 1"] });
  const ok = ['x.innerHTML = "";', 'x.innerHTML = ""', 'a.innerHTML = ""; b.innerHTML = "";', "if (x.innerHTML === y) f();", "x.innerHTML = `<td><input></td>`;", "x.innerHTML = svg; // trusted-html: reviewed"];
  for (const line of ok) assert.deepEqual(sinkProblems(line).problems, [], line);
});
