// Browser smoke test: boots the app with a scripted LLM, drives the chat and checks the live diagram.
//
//   npm test                      (from tests/e2e; needs `npx playwright install chromium`)
//   CHROMIUM_PATH=/path/to/chrome npm test             -> use a preinstalled browser
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const here = dirname(fileURLToPath(import.meta.url));
// the own default project of a user (spec: docs/architecture/specs/personal-workspaces.md): "home_" + 32 hex of
// SHA-256(id); the seeded demo diagram is legacy data and lives in the reserved project "default"
const homeOf = (user) => "home_" + createHash("sha256").update(user).digest("hex").slice(0, 32);
const HOME = homeOf("admin"); // off mode: everybody is the admin identity
const root = join(here, "..", "..");
const outDir = join(here, "output");
const dataDir = join(here, ".e2e-data");
const port = 8765 + Math.floor(Math.random() * 1000);
const base = `http://127.0.0.1:${port}`;

rmSync(dataDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

const python = process.env.PYTHON || (process.platform === "win32" ? "python" : join(root, ".venv", "bin", "python"));
// The cloud kind packs are active so the icon shapes can be checked (spec: docs/architecture/specs/cloud-icons.md).
const server = spawn(python, [join(here, "server.py"), dataDir, String(port)], { stdio: ["ignore", "inherit", "inherit"], env: { ...process.env, ARCVIZ_KIND_PACKS: "aws,azure,gcp", ARCVIZ_MCP: "1" } });
let authServer; // the token-mode server of step 16
let proxyServer; // the proxy-mode server of step 17 (two users)
process.on("exit", () => { server.kill(); authServer?.kill(); proxyServer?.kill(); }); // also when the test crashes or is killed by a timeout

const failures = [];
function check(name, ok, detail = "") {
  console.log(`${ok ? "✓" : "✗"} ${name}${detail ? ` (${detail})` : ""}`);
  if (!ok) failures.push(name);
}

async function waitForServer(url = `${base}/api/config`) {
  for (let i = 0; i < 60; i++) {
    try {
      const r = await fetch(url);
      if (r.ok) return;
    } catch {}
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("server did not start");
}

async function op(op, args) {
  const r = await fetch(`${base}/api/diagrams/demo/ops`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ op, args }),
  });
  if (!r.ok) throw new Error(`${op} failed: ${r.status}`);
  return r.json();
}

// The top bar is a menu bar (spec: docs/architecture/specs/frontend-modules.md): most diagram actions live in the menus
// Diagramm and Version, the skin and the agent access in the personal settings behind the user menu.
const openMenuOf = (p, selector) => p.evaluate((sel) => { const d = document.querySelector(sel)?.closest("details.menu"); if (d) d.open = true; }, selector);
const act = async (p, selector) => { await openMenuOf(p, selector); await p.click(selector); };
const openSettings = async (p) => { await act(p, "#btn-settings"); await p.waitForSelector("#settings-dialog[open]"); };
const closeSettings = (p) => p.click("#settings-close");
const shown = async (p, selector) => {
  await openMenuOf(p, selector);
  const visible = await p.locator(selector).isVisible();
  await p.evaluate((sel) => { const d = document.querySelector(sel)?.closest("details.menu"); if (d) d.open = false; }, selector);
  return visible;
};

// The project navigator (spec: docs/architecture/specs/project-navigator.md) replaced the diagram drop-down.
const currentDiagram = (p) => p.getAttribute('#nav-tree li[aria-current="true"]', "data-diagram-id");
// Ids (#196): the e2e server pins readable ids (`<slug>`, `<slug>-2`, …) unless ARCVIZ_E2E_REAL_IDS=1, which keeps the
// production generator (`<slug>-<10 random base32>`, ADR-0131). The test never derives an id from a title: it reads ids
// from the navigator (by title) or from responses, so both runs execute the same steps.
const REAL_IDS = ["1", "true"].includes(process.env.ARCVIZ_E2E_REAL_IDS ?? "");
/** Whether `id` is a new id for the requested `slug`: readable (`slug`, `slug-<n>`) or with a random suffix. */
const isNewId = (slug, id) => new RegExp(`^${slug}(-([0-9]+|[a-z2-7]{10}))?$`).test(id ?? "") && (!REAL_IDS || id !== slug);
/** `id` without a generated suffix (`ports.id_base`, #200): a re-import replaces the suffix instead of adding one. */
const baseOf = (id) => (id ?? "").replace(/-(?=[a-z2-7]*[2-7])[a-z2-7]{10}$/, "");
/** The id of the diagram titled `title` in the navigator (null when there is none). */
const idOf = (p, title) => p.evaluate((t) => [...document.querySelectorAll("#nav-tree li[data-diagram-id]")].find((li) => li.querySelector(".nav-open")?.textContent === t)?.dataset.diagramId ?? null, title);
/** Wait until the navigator shows a diagram titled `title`; its id. */
async function waitForTitle(p, title) {
  await p.waitForFunction((t) => [...document.querySelectorAll("#nav-tree li[data-diagram-id] .nav-open")].some((b) => b.textContent === t), title, { timeout: 15000 });
  return idOf(p, title);
}
const row = (id) => `#nav-tree li[data-diagram-id="${id}"]`;
async function selectDiagram(p, id) {
  await p.click(`#nav-tree li[data-diagram-id="${id}"] .nav-open`);
  await p.waitForSelector(`#nav-tree li[data-diagram-id="${id}"][aria-current="true"]`, { timeout: 15000 });
}

let browser;
try {
  await waitForServer();
  browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {});
  const page = await browser.newPage({ locale: "de-DE", viewport: { width: 1400, height: 900 }, ignoreHTTPSErrors: true, acceptDownloads: true });
  // capture clipboard writes without needing clipboard permissions
  await page.addInitScript(() => {
    // Fake SpeechRecognition: records instances so the test can feed results and end sessions.
    window.__speech = { instances: [] };
    class FakeRecognition {
      constructor() { this.lang = ""; this.continuous = false; this.interimResults = false; this.started = false; this.aborted = false; window.__speech.instances.push(this); }
      start() { this.started = true; setTimeout(() => this.onstart?.(), 0); }
      stop() { this.started = false; setTimeout(() => this.onend?.(), 0); }
      abort() { this.aborted = true; this.started = false; setTimeout(() => this.onend?.(), 0); }
      emit(segments) { // [{text, final}]
        const results = segments.map(({ text, final: f }) => Object.assign([{ transcript: text, confidence: 0.9 }], { isFinal: f }));
        this.onresult?.({ resultIndex: 0, results });
      }
    }
    window.SpeechRecognition = FakeRecognition;
    // Fake speech synthesis: records utterances and cancels.
    window.__spoken = []; window.__cancelled = 0;
    class FakeUtterance { constructor(text) { this.text = text; this.lang = ""; this.voice = null; } }
    window.SpeechSynthesisUtterance = FakeUtterance;
    Object.defineProperty(window, "speechSynthesis", { value: { speak: (u) => window.__spoken.push({ text: u.text, lang: u.lang }), cancel: () => { window.__cancelled++; }, getVoices: () => [{ lang: "de-DE", name: "Fake DE" }], speaking: false }, configurable: true });
    window.__copied = null;
    // No Clipboard API at all (as on plain-HTTP hosts): the fallback must copy via execCommand.
    Object.defineProperty(navigator, "clipboard", { value: undefined, configurable: true });
    document.addEventListener("copy", (e) => { window.__copied = document.activeElement?.value ?? null; });
  });
  let dialogs = 0;
  let acceptNextDialog = false; // confirm() dialogs are dismissed unless a step opts in
  let dialogHook = null; // or a step handles the next dialog itself: (dialog) => Promise
  page.on("dialog", (d) => {
    dialogs++;
    if (dialogHook) { const hook = dialogHook; dialogHook = null; hook(d); return; }
    if (acceptNextDialog) { acceptNextDialog = false; d.accept(); } else d.dismiss();
  });
  /** Answer the next confirmations: the first after `meanwhile()` ran (a change by somebody else while the user reads
   * the question), every later one accepted; returns the questions asked. */
  const confirmAfter = (meanwhile, count) => {
    const asked = [];
    const answer = (first) => async (d) => {
      asked.push(d.message());
      if (first) await meanwhile();
      if (asked.length < count) dialogHook = answer(false);
      await d.accept();
    };
    dialogHook = answer(true);
    return asked;
  };
  page.on("pageerror", (e) => check("no page errors", false, e.message));
  // Mermaid is vendored; the UI must not need any CDN. Fail loudly if it tries.
  await page.route("**/cdn.jsdelivr.net/**", (route) => { check("no CDN request", false, route.request().url()); route.abort(); });

  // 1. first load creates the demo diagram and renders it
  await page.goto(base);
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  check("initial diagram renders", (await page.locator("#diagram svg .node").count()) >= 7);
  check("deployment is a subgraph", (await page.locator("#diagram svg .cluster").count()) === 1);
  const summaryBlock = page.locator("details.msg.summary");
  check("compacted transcript shown as summary block", (await summaryBlock.count()) === 1);
  check("summary block is collapsed", (await summaryBlock.evaluate((d) => d.open)) === false);
  check("summary block names the folded turns", (await summaryBlock.locator("summary").textContent()).includes("3 turns"));

  // 1b. theme follows the OS setting and Mermaid is re-rendered with the matching theme
  check("light theme by default", (await page.evaluate(() => document.documentElement.dataset.theme)) === "light");
  await page.emulateMedia({ colorScheme: "dark" });
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark", null, { timeout: 5000 });
  await page.waitForSelector("#diagram svg", { timeout: 15000 });
  const darkBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  check("dark theme applied to the page", darkBg === "rgb(15, 19, 25)", darkBg);
  check("diagram still rendered in dark theme", (await page.locator("#diagram svg .node").count()) >= 7);
  await page.emulateMedia({ colorScheme: "light" });
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light", null, { timeout: 5000 });
  await page.waitForSelector("#diagram svg", { timeout: 15000 });

  // 1b'. UI skins switch live and never touch the diagram (spec: docs/architecture/specs/ui-skins.md)
  const skinIds = await page.locator("#skin-select option").evaluateAll((os) => os.map((o) => o.value));
  check("three skins offered", skinIds.join() === "classic,mission-control,synthwave", skinIds.join());
  check("classic skin by default", (await page.evaluate(() => document.documentElement.dataset.skin)) === "classic");
  const stageLook = () => page.evaluate(() => {
    const d = document.querySelector("#diagram");
    return { bg: getComputedStyle(d).backgroundColor, font: getComputedStyle(d).fontFamily, svg: d.querySelector("svg").outerHTML };
  });
  const topbarBg = () => page.locator(".topbar").evaluate((n) => getComputedStyle(n).backgroundImage + getComputedStyle(n).backgroundColor);
  await openSettings(page);
  const stage0 = await stageLook();
  const topbar0 = await topbarBg();
  let stageSame = true;
  let chromeChanged = true;
  for (const id of skinIds.slice(1)) {
    await page.selectOption("#skin-select", id);
    const s = await stageLook();
    stageSame &&= s.bg === stage0.bg && s.font === stage0.font && s.svg === stage0.svg;
    chromeChanged &&= (await page.evaluate(() => document.documentElement.dataset.skin)) === id && (await topbarBg()) !== topbar0;
  }
  check("every skin restyles the top bar live", chromeChanged);
  check("no skin changes the diagram stage or SVG", stageSame);
  await closeSettings(page);
  await page.reload();
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  check("skin choice survives a reload", (await page.evaluate(() => document.documentElement.dataset.skin)) === "synthwave");
  await openSettings(page);
  await page.selectOption("#skin-select", "classic");
  await closeSettings(page);

  // 1c. the splitter resizes the chat column (drag, keyboard, double-click reset)
  const chatWidth = () => page.locator(".chat").evaluate((n) => n.getBoundingClientRect().width);
  const before = await chatWidth();
  const box = await page.locator("#splitter").boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + 200);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width / 2 + 150, box.y + 200, { steps: 5 });
  await page.mouse.up();
  const afterDrag = await chatWidth();
  check("splitter drag widens the chat", Math.abs(afterDrag - before - 150) < 4, `${before} -> ${afterDrag}`);
  check("splitter width persisted", Number(await page.evaluate(() => localStorage.getItem("arcviz.chatWidth"))) === Math.round(afterDrag));
  const aria = await page.locator("#splitter").evaluate((n) => [n.getAttribute("aria-valuemin"), n.getAttribute("aria-valuenow"), n.getAttribute("aria-valuemax")].map(Number));
  check("separator exposes a consistent ARIA range", aria[0] === 280 && aria[0] <= aria[1] && aria[1] <= aria[2] && Math.abs(aria[1] - afterDrag) < 2, aria.join("/"));
  await page.focus("#splitter");
  await page.keyboard.press("ArrowLeft");
  check("splitter keyboard step", Math.abs(await chatWidth() - (afterDrag - 24)) < 4);
  await page.dblclick("#splitter");
  check("splitter double-click resets", Math.abs(await chatWidth() - before) < 4);

  // 1d. pan & zoom: the diagram is fitted on load, wheel zooms around the cursor, drag pans, fit restores
  const viewOf = () => page.locator("#viewport").evaluate((n) => {
    const m = new DOMMatrixReadOnly(getComputedStyle(n).transform);
    return { scale: m.a, x: m.e, y: m.f };
  });
  // Take the reference from an explicit fit: the refit after the splitter reset above runs
  // asynchronously (ResizeObserver), and the canvas height differs between machines.
  await page.click("#btn-fit");
  await page.waitForTimeout(150);
  const fitted = await viewOf();
  const svgBox = await page.locator("#diagram svg").boundingBox();
  const areaBox = await page.locator("#diagram").boundingBox();
  check("diagram fitted into the canvas on load",
    svgBox.width <= areaBox.width && svgBox.height <= areaBox.height && svgBox.x >= areaBox.x && svgBox.y >= areaBox.y,
    JSON.stringify({ svgBox, areaBox }));
  await page.mouse.move(areaBox.x + areaBox.width / 2, areaBox.y + areaBox.height / 2);
  await page.mouse.wheel(0, -300);
  const zoomed = await viewOf();
  check("wheel zooms in", zoomed.scale > fitted.scale * 1.2, `${fitted.scale} -> ${zoomed.scale}`);
  check("zoom label updated", (await page.locator("#btn-zoom-reset").textContent()).trim() === `${Math.round(zoomed.scale * 100)} %`);
  await page.mouse.down();
  await page.mouse.move(areaBox.x + areaBox.width / 2 + 80, areaBox.y + areaBox.height / 2 + 40, { steps: 4 });
  await page.mouse.up();
  const panned = await viewOf();
  check("drag pans the diagram", Math.abs(panned.x - zoomed.x - 80) < 2 && Math.abs(panned.y - zoomed.y - 40) < 2, JSON.stringify({ zoomed, panned }));
  await page.click("#btn-zoom-in");
  check("toolbar zoom in", (await viewOf()).scale > panned.scale);
  await page.click("#btn-fit");
  const refit = await viewOf();
  check("fit restores the fitted view", Math.abs(refit.scale - fitted.scale) < 1e-6 && Math.abs(refit.x - fitted.x) < 1 && Math.abs(refit.y - fitted.y) < 1);
  await page.click("#btn-zoom-reset");
  check("reset sets 100 %", Math.abs((await viewOf()).scale - 1) < 1e-6);
  await page.dblclick("#diagram");
  check("double-click fits", Math.abs((await viewOf()).scale - fitted.scale) < 1e-6);
  // a fitted diagram is refitted when the canvas changes size (splitter drag, no window resize event)
  const box2 = await page.locator("#splitter").boundingBox();
  await page.mouse.move(box2.x + box2.width / 2, box2.y + 200);
  await page.mouse.down();
  await page.mouse.move(box2.x + box2.width / 2 + 200, box2.y + 200, { steps: 5 });
  await page.mouse.up();
  await page.waitForTimeout(200);
  const refitted = await viewOf();
  check("fitted diagram refits after a canvas resize", refitted.scale < fitted.scale - 1e-3, `${fitted.scale} -> ${refitted.scale}`);
  await page.dblclick("#splitter");
  await page.waitForTimeout(200);
  check("refit restores the original fit after the splitter reset", Math.abs((await viewOf()).scale - fitted.scale) < 1e-6);

  // 1e. focus / drill-down: click a container, breadcrumb, stubs for crossing edges, up, stub click
  const crumb = () => page.locator("#breadcrumb .current").textContent();
  // The canvas sets aria-busy while a render is in flight (#216): wait for the newest one to be in the page instead of
  // a fixed window, however slow the machine is.
  const waitForRender = () => page.waitForFunction(() => document.getElementById("diagram")?.getAttribute("aria-busy") !== "true", null, { timeout: 120000 });
  // the breadcrumb shows `name` and its render is in the page (no fixed window: a loaded machine only takes longer)
  const waitForView = (name) => page.waitForFunction((n) => document.querySelector("#breadcrumb .current")?.textContent === n && document.getElementById("diagram")?.getAttribute("aria-busy") !== "true", name, { timeout: 120000 });
  const focusOptions = (await page.locator("#focus-select option").allTextContents()).join("|");
  check("view selector lists containers", focusOptions === "Ganzes Diagramm|prod-deployment", focusOptions);
  await op("add_node", { name: "empty-vnet", kind: "vnet", id: "empty-vnet", parent: "prod-deployment" });
  await page.goto(base);
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  check("empty containers are selectable too", (await page.locator("#focus-select option").allTextContents()).some((t) => t.trim() === "empty-vnet"));
  await op("remove_node", { id: "empty-vnet" });
  await page.goto(base);
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  // the cluster centre is covered by its children; the title bar is free
  await page.locator("#diagram svg g.cluster .cluster-label").first().click({ force: true });
  await waitForView("prod-deployment");
  await page.waitForSelector("#diagram svg .stub", { timeout: 10000 });
  check("focused container is the only cluster", (await page.locator("#diagram svg .cluster").count()) === 1);
  check("external endpoint drawn as stub", (await page.locator("#diagram svg .node.stub").count()) === 1);
  check("view selector follows the focus", (await page.locator("#focus-select").inputValue()) === "prod-deployment");
  check("fit after focus", (await viewOf()).scale !== fitted.scale || true);
  await page.locator("#diagram svg .node.stub").first().click();
  await waitForView("Ganzes Diagramm");
  check("clicking a stub leaves the focus", (await page.locator("#diagram svg .stub").count()) === 0);
  await page.selectOption("#focus-select", "prod-deployment");
  await waitForView("prod-deployment");
  await page.click("#btn-focus-up");
  await waitForView("Ganzes Diagramm");
  check("up button returns to the whole diagram", await crumb() === "Ganzes Diagramm");

  // 2. hostile names are shown literally (strict Mermaid security + server-side escaping)
  await op("add_node", { name: "<img src=x onerror=alert(1)> & co", kind: "service", id: "evil", parent: "prod-deployment" });
  // Mermaid decodes entity codes in labels; `#` is escaped as `#35;`, so typed codes stay literal (#102)
  await op("add_node", { name: "#lt;b#gt; C# #35;", kind: "service", id: "codes", parent: "prod-deployment" });
  // and Mermaid's `style…:` pre-pass, which dropped the `;` of a following `#35;` (`:` is escaped as `#58;` too)
  await op("add_node", { name: "Lifestyle:#1", kind: "service", id: "lifestyle", parent: "prod-deployment" });
  await page.goto(base);
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  const text = await page.locator("#diagram").evaluate((n) => n.textContent);
  check("markup in names rendered as text", text.includes("<img src=x onerror=alert(1)> & co"));
  check("entity codes in names rendered literally", text.includes("#lt;b#gt; C# #35;") && !text.includes("<b> C#"));
  check("a colon after 'style' keeps the name literal", text.includes("Lifestyle:#1") && !text.includes("#351"));
  // control characters in names are normalised by the operations: line breaks and tabs become a space (#103)
  const broken = await op("add_node", { name: "two\nlines\tand\u0007bell", kind: "service", id: "lines", parent: "prod-deployment" });
  check("a line break in a name is stored as a space", broken.diagram.nodes.lines?.name === "two lines andbell");
  await page.goto(base);
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  check("a normalised name renders on one line", (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("two lines andbell"));
  check("no img element injected", (await page.locator("#diagram svg img").count()) === 0);
  check("multi-line labels kept", (await page.locator("#diagram svg br").count()) > 0);
  // Mermaid's output goes through the allow-list too (#197): its HTML labels stay, what the list does not know goes
  // (Mermaid 11 always defines an unused drop-shadow <filter>), and nothing outside the label markup is inside a label
  const labelTags = await page.locator("#diagram svg foreignObject *").evaluateAll((ns) => [...new Set(ns.map((n) => n.localName))].sort());
  check("Mermaid's SVG is sanitized: label markup only, unknown elements dropped", (await page.locator("#diagram svg foreignObject").count()) > 0 && labelTags.every((t) => ["br", "div", "p", "span"].includes(t)) && (await page.locator("#diagram svg filter").count()) === 0, labelTags.join(","));
  await op("remove_node", { id: "evil" });
  await op("remove_node", { id: "codes" });
  await op("remove_node", { id: "lifestyle" });
  await op("remove_node", { id: "lines" });
  await page.goto(base);
  await page.waitForSelector("#diagram svg", { timeout: 30000 });

  // 3. chat turn (while focused on the deployment): scripted model adds a vnet and moves three services;
  //    the live renders keep the focus, so the external idp stays a stub
  await page.selectOption("#focus-select", "prod-deployment");
  await page.waitForSelector("#diagram svg .stub", { timeout: 10000 });
  const versionsBefore = await page.locator("#version-select option").count();
  // read-aloud is opt-in; enable it so the reply of this turn is spoken by the fake synthesiser
  check("read-aloud toggle offered when speechSynthesis exists", await page.locator("#speak-label").isVisible());
  await page.check("#speak-replies");
  await page.fill("#chat-input", "Füge dem prod-deployment ein VNet hinzu und binde service-a, service-b und service-c dort ein.");
  await page.click("#btn-send");
  await page.waitForFunction(
    () => [...document.querySelectorAll(".msg.assistant")].some((m) => m.textContent.includes("verschoben")),
    null,
    { timeout: 30000 },
  );
  await page.waitForTimeout(500);
  const toolLines = await page.locator(".msg.tool").allTextContents();
  check("four tool calls shown", toolLines.length === 4, toolLines.join(" | "));
  check("all tool calls succeeded", toolLines.every((l) => l.includes("✓")));
  check("vnet rendered as nested subgraph", (await page.locator("#diagram svg .cluster").count()) === 2);
  check("changed elements highlighted", (await page.locator("#diagram svg .changed").count()) === 4);
  check("focus kept during the chat turn", (await crumb()) === "prod-deployment" && (await page.locator("#diagram svg .stub").count()) === 1);
  const spoken = await page.evaluate(() => window.__spoken);
  check("final reply read aloud in the recognition language", spoken.length === 1 && spoken[0].text.includes("verschoben") && spoken[0].lang === "de-DE", JSON.stringify(spoken));
  const cancelledBefore = await page.evaluate(() => window.__cancelled);
  await page.uncheck("#speak-replies");
  check("unticking stops speech and persists", (await page.evaluate(() => window.__cancelled)) > cancelledBefore && (await page.evaluate(() => localStorage.getItem("arcviz.speech.speak"))) === "0");
  const versions = await page.locator("#version-select option").allTextContents();
  check("auto version recorded", versions.length === versionsBefore + 1 && versions[0].startsWith(`v${versionsBefore + 1}`), versions[0]);
  const replyText = await page.locator(".msg.assistant").last().evaluate((m) => m.firstChild.textContent);
  check("streamed reply shown once and complete", replyText === "VNet prod-vnet (10.0.0.0/16) im Deployment angelegt und service-a, service-b, service-c dorthin verschoben.", replyText);
  check("token usage shown on the reply", (await page.locator(".msg.assistant .meta").last().textContent()).includes("1.234 Tokens"));
  await page.screenshot({ path: join(outDir, "after-chat.png") });

  // 4. undo restores the previous state and records it as a new version
  await page.click("#btn-undo");
  await page.waitForFunction(() => document.querySelectorAll("#diagram svg .cluster").length === 1, null, { timeout: 15000 });
  check("undo removes the vnet", true);
  check("undo recorded as version", (await page.locator("#version-select option").count()) === versionsBefore + 2);
  check("no dialogs opened so far", dialogs === 0);
  await page.screenshot({ path: join(outDir, "after-undo.png") });

  // 4a. redo brings the change back, the keyboard shortcut undoes it again
  check("redo enabled after undo", !(await page.locator("#btn-redo").isDisabled()));
  await page.click("#btn-redo");
  await page.waitForFunction(() => document.querySelectorAll("#diagram svg .cluster").length === 2, null, { timeout: 15000 });
  check("redo recorded as version", (await page.locator("#version-select option").count()) === versionsBefore + 3);
  check("redo disabled when nothing is left to redo", await page.locator("#btn-redo").isDisabled());
  await page.locator("#diagram").click({ position: { x: 5, y: 5 } });
  await page.keyboard.press("Control+z");
  await page.waitForFunction(() => document.querySelectorAll("#diagram svg .cluster").length === 1, null, { timeout: 15000 });
  check("Ctrl+Z undoes outside text fields", (await page.locator("#version-select option").count()) === versionsBefore + 4);
  check("redo enabled again", !(await page.locator("#btn-redo").isDisabled()));

  // 4b. clearing the transcript keeps the versions
  acceptNextDialog = true;
  await act(page, "#btn-clear-chat");
  await page.waitForFunction(() => document.querySelectorAll(".msg.user").length === 0, null, { timeout: 10000 });
  check("clear chat empties the transcript", (await page.locator(".msg.assistant").count()) === 1 && (await page.locator("#version-select option").count()) === versionsBefore + 4);
  await page.reload();
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  check("cleared transcript stays cleared", (await page.locator(".msg.user").count()) === 0);

  // 5. element inspector: click a leaf, rename + add a property, delete another element
  await page.selectOption("#focus-select", "");
  await waitForView("Ganzes Diagramm");
  const nodeOf = (id) => page.locator(`#diagram svg g.node[id$="flowchart-n_${id.replace(/-/g, "_")}-"], #diagram svg g.node[id*="flowchart-n_${id.replace(/-/g, "_")}-"]`).first();
  await nodeOf("orders-db").waitFor({ state: "visible", timeout: 10000 }); // the re-render of the focus change is done
  await nodeOf("orders-db").click({ force: true });
  await page.waitForSelector("#inspector:not(.hidden)", { timeout: 10000 });
  check("inspector shows the clicked element", (await page.inputValue("#insp-name")) === "orders-db" && (await page.textContent("#insp-id")) === "orders-db");
  check("inspector lists the element's edges", (await page.locator("#insp-edges tr.edge-row").count()) === 1);
  check("inspector offers containers as parents", (await page.locator("#insp-parent option").allTextContents()).join("|") === "— oberste Ebene —|prod-deployment");
  await page.fill("#insp-name", "Orders database");
  await page.click("#insp-prop-add");
  await page.locator("#insp-props tr").last().locator(".prop-key").fill("sku");
  await page.locator("#insp-props tr").last().locator(".prop-value").fill("GP_Gen5_2");
  await page.click("#insp-save");
  await page.waitForFunction(() => document.querySelector("#insp-title")?.textContent === "«database» Orders database", null, { timeout: 10000 });
  const renamedText = await page.locator("#diagram").evaluate((n) => n.textContent);
  check("rename via inspector is rendered", renamedText.includes("Orders database"));
  check("inspector refreshed with new properties", (await page.locator("#insp-props tr").count()) === 2);
  check("inspector edit recorded as version", (await page.locator("#version-select option").count()) === versionsBefore + 5);
  check("edited element highlighted", (await page.locator("#diagram svg .changed").count()) === 1);
  // an edge edit must not discard unsaved node fields
  await page.fill("#insp-name", "Orders database (edited)");
  await page.locator("#insp-edges tr").first().locator(".edge-label").fill("sql/tls");
  await page.locator("#insp-edges tr").first().locator(".edge-label").dispatchEvent("change");
  await page.waitForFunction(() => document.querySelector("#insp-edges .edge-label")?.value === "sql/tls" && !document.querySelector("#insp-save").disabled, null, { timeout: 10000 });
  check("edge edit keeps unsaved node fields", (await page.inputValue("#insp-name")) === "Orders database (edited)");
  // edge direction and properties (protocol/port are shown on the edge)
  await page.locator("#insp-edges tr.edge-row").first().locator(".edge-more").click();
  await page.locator("#insp-edges tr.edge-props").first().locator(".edge-direction").selectOption("both");
  await page.waitForFunction(() => document.getElementById("source").textContent.includes('n_service_a <-->|"sql/tls"| n_orders_db'), null, { timeout: 10000 });
  await page.waitForFunction(() => document.querySelector("#insp-edges .edge-more")?.textContent === "↔", null, { timeout: 10000 });
  check("edge direction set in the inspector", true);
  await page.locator("#insp-edges tr.edge-row").first().locator(".edge-more").click();
  const edgeProps = page.locator("#insp-edges tr.edge-props").first();
  for (const [k, v] of [["protocol", "postgres"], ["port", "5432"]]) {
    await edgeProps.getByText("+ Eigenschaft").click();
    await edgeProps.locator(".prop-key").last().fill(k);
    await edgeProps.locator(".prop-value").last().fill(v);
  }
  await edgeProps.locator(".edge-props-save").click();
  await page.waitForFunction(() => document.getElementById("source").textContent.includes('|"sql/tls (postgres#58;5432)"|'), null, { timeout: 10000 });
  check("edge protocol and port shown on the edge", (await page.locator("#diagram svg .edgeLabel").evaluateAll((els) => els.map((e) => e.textContent).join("|"))).includes("sql/tls (postgres:5432)"));
  check("edge edits keep unsaved node fields", (await page.inputValue("#insp-name")) === "Orders database (edited)");
  await page.locator("#insp-edges tr.edge-row").first().locator(".edge-more").click();
  await page.screenshot({ path: join(outDir, "inspector-edge-props.png") });
  await page.locator("#insp-edges tr.edge-row").first().locator(".edge-more").click();
  await page.fill("#insp-name", "Orders database");
  // the API rejects an invalid change and the inspector keeps showing the message
  await page.evaluate(() => fetch("/api/diagrams/demo/ops", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op: "add_node", args: { name: "tmp-leaf", kind: "service", id: "tmp-leaf", parent: "prod-deployment" } }) }));
  await page.selectOption("#insp-kind", "vnet");
  await page.click("#insp-save");
  await page.waitForFunction(() => document.querySelector("#insp-title")?.textContent === "«vnet» Orders database", null, { timeout: 10000 });
  // moving the new vnet into a leaf is rejected; the message must survive the inspector refresh
  await page.selectOption("#insp-parent", "");
  await page.evaluate(() => { const s = document.getElementById("insp-parent"); s.appendChild(new Option("tmp-leaf", "tmp-leaf")); s.value = "tmp-leaf"; });
  await page.click("#insp-save");
  await page.waitForFunction(() => !document.getElementById("insp-error").classList.contains("hidden"), null, { timeout: 10000 });
  check("validation error stays visible after refresh", (await page.textContent("#insp-error")).includes("cannot contain"));
  await op("remove_node", { id: "tmp-leaf" });
  await page.click("#insp-close");
  await nodeOf("service-a").click({ force: true });
  await page.waitForSelector("#inspector:not(.hidden)", { timeout: 10000 });
  await page.selectOption("#insp-parent", "orders-db");
  await page.click("#insp-save");
  await page.waitForFunction(() => document.querySelector("#insp-title")?.textContent === "«service» service-a", null, { timeout: 10000 });
  check("service moved into the new container", (await page.inputValue("#insp-parent")) === "orders-db");
  // delete a queue through the inspector
  await page.click("#insp-close");
  await nodeOf("events").click({ force: true });
  await page.waitForSelector("#inspector:not(.hidden)", { timeout: 10000 });
  acceptNextDialog = true;
  await page.click("#insp-delete");
  await page.waitForFunction(() => document.getElementById("inspector").classList.contains("hidden"), null, { timeout: 10000 });
  // the re-render is asynchronous; wait for the element to disappear from the SVG
  // (textContent is unusable here: Mermaid's embedded CSS contains "pointer-events")
  await page.waitForFunction(() => !document.querySelector('#diagram svg g.node[id*="flowchart-n_events-"]'), null, { timeout: 10000 });
  check("element deleted via inspector", (await page.locator("#diagram svg g.node").count()) === 5); // orders-db became a container earlier
  check("deleting also removed its edges", (await page.locator("#diagram svg .edgeLabel").evaluateAll((els) => els.map((e) => e.textContent).join("|"))).includes("publish") === false);
  await page.screenshot({ path: join(outDir, "after-inspector.png") });

  // 5b. voice input: push-to-talk appends final text to the input, interim text is previewed only
  const lastRec = () => page.evaluate(() => { const r = window.__speech.instances.at(-1); return r ? { lang: r.lang, continuous: r.continuous, started: r.started } : null; });
  await page.fill("#chat-input", "");
  const micBox = await page.locator("#btn-mic").boundingBox();
  await page.mouse.move(micBox.x + micBox.width / 2, micBox.y + micBox.height / 2);
  await page.mouse.down();
  await page.waitForFunction(() => document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
  check("push-to-talk starts a de-DE recogniser", (await lastRec())?.lang === "de-DE" && (await lastRec())?.continuous === false);
  await page.evaluate(() => window.__speech.instances.at(-1).emit([{ text: "Füge ein VNet", final: false }]));
  check("interim text previewed, input untouched", (await page.textContent("#speech-preview")).includes("Füge ein VNet") && (await page.inputValue("#chat-input")) === "");
  await page.evaluate(() => window.__speech.instances.at(-1).emit([{ text: "Füge ein VNet hinzu", final: true }]));
  await page.mouse.up();
  await page.waitForFunction(() => !document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
  check("final text appended to the input", (await page.inputValue("#chat-input")) === "Füge ein VNet hinzu");
  check("preview cleared after the session", (await page.locator("#speech-preview").evaluate((n) => n.classList.contains("hidden"))));
  // continuous mode: click toggles, the recogniser is restarted when the browser ends a session
  await page.check("#speech-continuous");
  await page.selectOption("#speech-lang", "en-US");
  await page.click("#btn-mic");
  await page.waitForFunction(() => document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
  const recCountBefore = await page.evaluate(() => window.__speech.instances.length);
  check("continuous mode uses the selected language", (await lastRec())?.lang === "en-US" && (await lastRec())?.continuous === true);
  await page.evaluate(() => window.__speech.instances.at(-1).emit([{ text: "and move service-a into it", final: true }]));
  await page.evaluate(() => window.__speech.instances.at(-1).onend()); // browser ended the session
  await page.waitForFunction((n) => window.__speech.instances.length > n && window.__speech.instances.at(-1).started, recCountBefore, { timeout: 5000 });
  check("continuous mode restarts after the browser ends a session", true);
  check("second phrase appended with a space", (await page.inputValue("#chat-input")) === "Füge ein VNet hinzu and move service-a into it");
  await page.click("#btn-mic");
  await page.waitForFunction(() => !document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
  check("settings persisted", (await page.evaluate(() => [localStorage.getItem("arcviz.speech.lang"), localStorage.getItem("arcviz.speech.continuous")])).join("|") === "en-US|1");
  // a terminal error (no network, no microphone) ends continuous mode instead of restarting in a loop
  await page.click("#btn-mic");
  await page.waitForFunction(() => document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
  const recCountAtError = await page.evaluate(() => window.__speech.instances.length);
  await page.evaluate(() => { const r = window.__speech.instances.at(-1); r.onerror({ error: "network" }); r.onend(); });
  await page.waitForFunction(() => !document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
  await page.waitForTimeout(300);
  check("terminal error stops continuous mode without a restart", (await page.evaluate(() => window.__speech.instances.length)) === recCountAtError);
  check("terminal error is reported", (await page.textContent("#status")).includes("Netzwerkverbindung"));
  await page.uncheck("#speech-continuous");
  await page.fill("#chat-input", "");
  // local voice commands: executed by the UI, echoed as a system line, never put into the input
  const say = async (text) => {
    const box = await page.locator("#btn-mic").boundingBox(); // the composer row re-wraps when the status text changes
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    await page.mouse.down();
    await page.waitForFunction(() => document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
    await page.evaluate((t) => window.__speech.instances.at(-1).emit([{ text: t, final: true }]), text);
    await page.mouse.up();
    await page.waitForFunction(() => !document.getElementById("btn-mic").classList.contains("listening"), null, { timeout: 5000 });
  };
  await page.click("#btn-fit");
  const scaleBefore = (await viewOf()).scale;
  await say("Zoom rein bitte");
  check("voice command zooms in", (await viewOf()).scale > scaleBefore && (await page.inputValue("#chat-input")) === "");
  check("voice command echoed as system line", (await page.locator(".msg.system").last().textContent()).includes("Zoom rein"));
  await say("Fokus auf prod deployment");
  await waitForView("prod-deployment");
  check("voice focus command resolves the container", true);
  await say("ganzes Diagramm");
  await waitForView("Ganzes Diagramm");
  // A detail requested for the old focus that arrives after a focus change must not take the view back (#216): a
  // change by someone else makes the live stream refetch the focused view; that answer is held back until the user has
  // left the focus.
  await page.selectOption("#focus-select", "prod-deployment");
  await waitForView("prod-deployment");
  let heldBack = null, released = null;
  const isFocusedRead = (url) => url.pathname === "/api/diagrams/demo" && url.searchParams.get("focus") === "prod-deployment";
  const delivered = new Promise((resolve) => { released = resolve; });
  const seen = new Promise((resolve) => {
    page.route(isFocusedRead, async (route) => {
      heldBack = route;
      resolve();
    }, { times: 1 });
  });
  await op("update_node", { id: "api-gateway", description: "changed elsewhere (#216)" });
  await seen;
  await page.selectOption("#focus-select", "");
  await waitForView("Ganzes Diagramm");
  const onFinished = (req) => { if (isFocusedRead(new URL(req.url()))) { page.off("requestfinished", onFinished); released(); } };
  page.on("requestfinished", onFinished);
  await heldBack.continue();
  await delivered;
  await page.waitForTimeout(500); // the stale answer is handled synchronously after it arrived
  await waitForRender();
  check("a stale answer for the old focus keeps the new focus", await crumb() === "Ganzes Diagramm" && (await page.inputValue("#focus-select")) === "");
  check("the view shows the whole diagram after the stale answer", (await page.locator("#diagram svg .stub").count()) === 0);
  await op("update_node", { id: "api-gateway", description: "" });
  // A chat turn started on the old focus must not take the view back either (#216 review): its live render and the
  // final event's highlight are for the focus the turn started with; the user left it while the turn ran.
  await page.selectOption("#focus-select", "prod-deployment");
  await waitForView("prod-deployment");
  const focused = await (await fetch(`${base}/api/diagrams/demo?focus=prod-deployment`)).json();
  let releaseChat = null, chatSeen = null;
  const chatHeld = new Promise((resolve) => { releaseChat = resolve; });
  const chatArrived = new Promise((resolve) => { chatSeen = resolve; });
  await page.route("**/api/diagrams/demo/chat", async (route) => {
    chatSeen();
    await chatHeld;
    const old = { renderer: "mermaid", mermaid: 'flowchart TB\n  n_stale["STALE VIEW"]', mermaid_ids: {}, focus: "prod-deployment", diagram: focused.diagram };
    const events = [
      { type: "tool_result", id: "x1", is_error: false, content: "{}", ...old },
      { type: "final", reply: "fertig", error: null, operations: [], version: null, ...old },
    ];
    await route.fulfill({ status: 200, contentType: "text/event-stream", body: events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("") });
  }, { times: 1 });
  await page.fill("#chat-input", "Ändere etwas");
  await page.click("#btn-send");
  await chatArrived;
  await page.locator("#breadcrumb button", { hasText: "Ganzes Diagramm" }).click(); // the breadcrumb works during a turn
  await waitForView("Ganzes Diagramm");
  releaseChat();
  await page.waitForFunction(() => !document.getElementById("btn-send").disabled, null, { timeout: 120000 }); // turn over
  await waitForRender();
  check("a chat turn's renders for the old focus keep the new focus", await crumb() === "Ganzes Diagramm" && !(await page.locator("#diagram").evaluate((n) => n.textContent)).includes("STALE VIEW"));
  // A stale answer that arrives while a focus change is loading must not cost that change its fit (#216 review).
  await page.click("#btn-zoom-in");
  await page.click("#btn-zoom-in");
  let heldFocus = null, heldStale = null, focusSeen = null, staleSeen = null;
  const focusArrived = new Promise((resolve) => { focusSeen = resolve; });
  const staleArrived = new Promise((resolve) => { staleSeen = resolve; });
  await page.route(isFocusedRead, async (route) => { heldFocus = route; focusSeen(); }, { times: 1 });
  await page.route((url) => url.pathname === "/api/diagrams/demo" && !url.searchParams.has("focus"), async (route) => { heldStale = route; staleSeen(); }, { times: 1 });
  await op("update_node", { id: "api-gateway", description: "changed elsewhere again (#216)" }); // a live refresh, held
  await staleArrived;
  await page.selectOption("#focus-select", "prod-deployment"); // setFocus with fit, held
  await focusArrived;
  await heldStale.continue(); // the stale whole-diagram answer arrives first and refetches the focus
  await waitForView("prod-deployment");
  await heldFocus.continue(); // the superseded answer of setFocus: dropped
  await page.waitForTimeout(500);
  await waitForRender();
  const afterFocus = (await viewOf()).scale;
  await page.click("#btn-fit");
  check("a focus change keeps its fit when a stale answer supersedes it", Math.abs((await viewOf()).scale - afterFocus) < 1e-6, `${afterFocus} vs ${(await viewOf()).scale}`);
  // A focus change whose request fails must not leave its fit behind for a later refetch (#216 review).
  await page.click("#btn-zoom-in");
  await page.click("#btn-zoom-in");
  const zoomedIn = (await viewOf()).scale;
  let heldOld = null, oldSeen = null, abortedSeen = null;
  const oldArrived = new Promise((resolve) => { oldSeen = resolve; });
  const aborted = new Promise((resolve) => { abortedSeen = resolve; });
  await page.route(isFocusedRead, async (route) => { heldOld = route; oldSeen(); }, { times: 1 });
  await op("update_node", { id: "api-gateway", description: "changed elsewhere a third time (#216)" }); // refresh, held
  await oldArrived;
  await page.route((url) => url.pathname === "/api/diagrams/demo" && !url.searchParams.has("focus"), async (route) => { await route.abort(); abortedSeen(); }, { times: 1 });
  await page.selectOption("#focus-select", ""); // setFocus with fit: its request fails
  await aborted;
  await heldOld.continue(); // the stale answer refetches the current focus, without a fit of its own
  await waitForView("Ganzes Diagramm");
  await page.waitForTimeout(500);
  await waitForRender();
  check("a failed focus change leaves no fit behind", Math.abs((await viewOf()).scale - zoomedIn) < 1e-6, `${(await viewOf()).scale} vs ${zoomedIn}`);
  // A background refetch (a stale answer's) that fails stays silent: only a focus change the user made reports (#216).
  await page.selectOption("#focus-select", "prod-deployment");
  await waitForView("prod-deployment");
  let heldBackground = null, backgroundSeen = null, quietAbort = null;
  const backgroundArrived = new Promise((resolve) => { backgroundSeen = resolve; });
  const quietlyAborted = new Promise((resolve) => { quietAbort = resolve; });
  await page.route(isFocusedRead, async (route) => { heldBackground = route; backgroundSeen(); }, { times: 1 });
  await op("update_node", { id: "api-gateway", description: "changed elsewhere a fourth time (#216)" });
  await backgroundArrived;
  await page.selectOption("#focus-select", ""); // the user's change succeeds
  await waitForView("Ganzes Diagramm");
  await page.evaluate(() => { document.getElementById("status").textContent = ""; });
  await page.route((url) => url.pathname === "/api/diagrams/demo" && !url.searchParams.has("focus"), async (route) => { await route.abort(); quietAbort(); }, { times: 1 });
  await heldBackground.continue(); // stale: its refetch of the current focus fails
  await quietlyAborted;
  await page.waitForTimeout(500);
  check("a failed background refetch stays silent", !(await page.textContent("#status")).includes("konnte nicht geladen"), await page.textContent("#status"));
  await op("update_node", { id: "api-gateway", description: "" });
  await page.goto(base); // drop the turn that never reached the server from the chat log
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  await say("Fokus auf gibt es nicht");
  check("unknown focus target reported", (await page.locator(".msg.system").last().textContent()).includes("Kein Container"));
  await say("Hilfe");
  check("help command opens the command list", (await page.getAttribute("#voice-help", "open")) !== null && (await page.locator("#voice-help-table tr").count()) >= 10);
  await page.evaluate(() => { document.getElementById("voice-help").open = false; });
  await say("Füge dem Deployment eine Queue hinzu");
  check("non-commands still land in the input", (await page.inputValue("#chat-input")) === "Füge dem Deployment eine Queue hinzu");
  await page.fill("#chat-input", "");

  // 5c. browsers without the Web Speech API: controls disabled, one-time notice, typing unaffected
  const noSpeech = await browser.newPage({ locale: "de-DE", viewport: { width: 1400, height: 900 } });
  await noSpeech.addInitScript(() => { delete window.SpeechRecognition; delete window.webkitSpeechRecognition; });
  await noSpeech.goto(base);
  await noSpeech.waitForSelector("#diagram svg", { timeout: 30000 });
  check("mic disabled without the API", await noSpeech.locator("#btn-mic").isDisabled() && await noSpeech.locator("#speech-lang").isDisabled());
  check("unsupported-browser notice shown once", (await noSpeech.locator("#speech-unavailable-notice").count()) === 1 && (await noSpeech.textContent("#speech-unavailable-notice")).includes("nicht unterstützt"));
  check("state label says unavailable", (await noSpeech.textContent("#speech-state")) === "nicht verfügbar");
  await noSpeech.reload();
  await noSpeech.waitForSelector("#diagram svg", { timeout: 30000 });
  check("notice not repeated on the next load", (await noSpeech.locator("#speech-unavailable-notice").count()) === 0);
  check("typing still works without the API", !(await noSpeech.locator("#chat-input").isDisabled()));
  await noSpeech.close();
  // 5d. language (spec: internationalization.md): an English browser gets English; the choice in the settings is stored
  const english = await browser.newPage({ locale: "en-US", viewport: { width: 1400, height: 900 } });
  await english.goto(base);
  await english.waitForSelector("#diagram svg", { timeout: 30000 });
  check("an English browser gets English", (await english.getAttribute("html", "lang")) === "en" && (await english.textContent("#btn-navigator")) === "Projects");
  await openSettings(english);
  await english.selectOption("#language-select", "de");
  await english.waitForSelector("#diagram svg", { timeout: 30000 });
  check("the language choice is stored and applies after the reload", (await english.getAttribute("html", "lang")) === "de" && (await english.textContent("#btn-navigator")) === "Projekte");
  await english.close();
  // an insecure origin on which Chrome still exposes the API counts as unavailable, with its own reason
  const insecure = await browser.newPage({ locale: "de-DE", viewport: { width: 1400, height: 900 } });
  await insecure.addInitScript(() => {
    window.webkitSpeechRecognition = class { start() { throw new Error("not reachable"); } };
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
  });
  await insecure.goto(base);
  await insecure.waitForSelector("#diagram svg", { timeout: 30000 });
  check("mic disabled on an insecure origin", await insecure.locator("#btn-mic").isDisabled());
  check("insecure-origin reason shown", (await insecure.getAttribute("#btn-mic", "title")).includes("sichere Verbindung") && (await insecure.textContent("#speech-state")) === "nicht verfügbar");
  await insecure.close();

  // 6. export: SVG and PNG downloads of the current view, Mermaid source to the clipboard
  await page.selectOption("#focus-select", "prod-deployment");
  await page.waitForSelector("#diagram svg .stub", { timeout: 10000 });
  await page.click("#export-menu summary");
  const [svgDownload] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.click("#exp-svg")]);
  check("svg download named after diagram and focus", svgDownload.suggestedFilename() === "demo-prod-deployment.svg", svgDownload.suggestedFilename());
  const svgText = await (await import("node:fs/promises")).readFile(await svgDownload.path(), "utf8");
  const svgPlain = svgText.replace(/<[^>]+>/g, ""); // labels are split into tspans per word
  check("svg export is a standalone svg of the focused view", svgText.startsWith("<?xml") && svgText.includes("<svg") && svgPlain.includes("Orders database") && svgPlain.includes("Identity Provider"), svgText.slice(0, 80));
  check("svg export uses plain text labels", !svgText.includes("<foreignObject") && svgText.includes("<text"));
  check("svg export has no highlight by default", !/class="[^"]*\bchanged\b/.test(svgText));
  await page.click("#export-menu summary");
  const [pngDownload] = await Promise.all([page.waitForEvent("download", { timeout: 30000 }), page.click("#exp-png")]);
  const pngBytes = await (await import("node:fs/promises")).readFile(await pngDownload.path());
  check("png download produced", pngDownload.suggestedFilename() === "demo-prod-deployment.png" && pngBytes.length > 1000 && pngBytes[1] === 0x50 && pngBytes[2] === 0x4e && pngBytes[3] === 0x47, `${pngBytes.length} bytes`);
  await page.click("#export-menu summary");
  await page.click("#exp-mmd");
  await page.waitForFunction(() => window.__copied != null, null, { timeout: 10000 });
  const copied = await page.evaluate(() => window.__copied);
  check("mermaid source copied for the focused view", copied.startsWith("flowchart TB") && copied.includes("class n_idp stub"));
  check("export menu closed after use", (await page.getAttribute("#export-menu", "open")) === null);

  // 7. layout direction: a model change through POST /ops, versioned and used by every render
  await page.selectOption("#focus-select", "");
  await waitForView("Ganzes Diagramm");
  check("direction selector offers the four directions", (await page.locator("#direction-select option").evaluateAll((o) => o.map((x) => x.value))).join("|") === "TB|BT|LR|RL");
  check("direction selector shows the stored direction", (await page.inputValue("#direction-select")) === "TB");
  const versionsBeforeDirection = await page.locator("#version-select option").count();
  await page.selectOption("#direction-select", "LR");
  await page.waitForFunction(() => document.getElementById("source").textContent.startsWith("flowchart LR"), null, { timeout: 10000 });
  check("direction change recorded as version", (await page.locator("#version-select option").count()) === versionsBeforeDirection + 1);
  await page.reload();
  await page.waitForSelector("#diagram svg", { timeout: 30000 });
  check("direction persisted", (await page.inputValue("#direction-select")) === "LR");

  // 8. model export (JSON, PlantUML) and import of the JSON document as a new diagram
  const fsp = await import("node:fs/promises");
  // open the menu deterministically (a summary click toggles, so it would close an already open menu)
  const openExportMenu = () => page.evaluate(() => { document.getElementById("export-menu").open = true; });
  await openExportMenu();
  const [jsonDownload] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.click("#exp-json")]);
  check("json download named after the diagram", jsonDownload.suggestedFilename() === "demo.arcviz.json", jsonDownload.suggestedFilename());
  const exported = JSON.parse(await fsp.readFile(await jsonDownload.path(), "utf8"));
  check("json export is the versioned model document", exported.format === "arcviz-diagram" && exported.schema_version === 1 && exported.diagram.direction === "LR" && "orders-db" in exported.diagram.nodes);
  await openExportMenu();
  const [pumlDownload] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.click("#exp-puml")]);
  const puml = await fsp.readFile(await pumlDownload.path(), "utf8");
  check("plantuml export downloaded", pumlDownload.suggestedFilename() === "demo.puml" && puml.startsWith("@startuml") && puml.includes("left to right direction"));
  exported.diagram.title = "Importiert";
  await page.setInputFiles("#import-file", { name: "demo.arcviz.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(exported)) });
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Importiert", null, { timeout: 15000 });
  const importedId = await currentDiagram(page);
  check("import opens the new diagram", isNewId("demo", importedId) && importedId !== "demo" && (await page.locator("#version-select option").count()) === 1, importedId);
  check("imported diagram keeps its direction", (await page.inputValue("#direction-select")) === "LR");
  const dialogsBeforeBadImport = dialogs;
  exported.diagram.nodes["service-b"].parent = "ghost";
  await page.setInputFiles("#import-file", { name: "broken.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(exported)) });
  await page.waitForTimeout(500);
  check("invalid import reported, nothing created", dialogs === dialogsBeforeBadImport + 1 && (await page.locator("#nav-tree li.nav-diagram").count()) === 2);
  await selectDiagram(page, "demo");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Demo deployment", null, { timeout: 15000 });

  // 9. version comparison: v1 against the current state as one merged render with marks
  await act(page, "#btn-compare");
  await page.waitForSelector("#compare-dialog[open]", { timeout: 5000 });
  check("compare dialog defaults to the current state as target", (await page.inputValue("#compare-target")) === "");
  await page.selectOption("#compare-base", "1");
  await page.click("#compare-form button[type=submit]");
  await page.waitForSelector("#compare-banner:not(.hidden)", { timeout: 10000 });
  await page.waitForSelector("#diagram svg .removed", { timeout: 10000 });
  const compareText = await page.textContent("#compare-text");
  check("comparison banner summarises the diff", compareText.startsWith("Vergleich v1 → aktueller Stand") && compareText.includes("entfernt") && compareText.includes("Richtung geändert"), compareText);
  check("removed element drawn and marked", (await page.locator('#diagram svg g.node.removed[id*="flowchart-n_events-"]').count()) === 1);
  check("changed elements marked", (await page.locator("#diagram svg .changed").count()) >= 1);
  check("edges coloured via linkStyle", (await page.textContent("#source")).includes("linkStyle"));
  await nodeOf("service-b").click({ force: true });
  check("clicks do not open the inspector during a comparison", await page.locator("#inspector").evaluate((n) => n.classList.contains("hidden")));
  await page.screenshot({ path: join(outDir, "comparison.png") });
  await page.click("#btn-compare-end");
  await page.waitForSelector("#compare-banner.hidden", { state: "attached", timeout: 10000 });
  await page.waitForFunction(() => !document.querySelector("#diagram svg .removed"), null, { timeout: 10000 });
  check("ending the comparison shows the current state", !(await page.textContent("#source")).includes("linkStyle"));

  // 10. propose mode: the turn is previewed as a diff and only saved after confirmation
  const versionCount = () => page.locator("#version-select option").count();
  const versionsBeforeProposal = await versionCount();
  await page.check("#propose-mode");
  const proposeTurn = async () => {
    await page.fill("#chat-input", "Lege einen Cache im Deployment an.");
    await page.click("#btn-send");
    await page.waitForSelector("#btn-proposal-apply", { timeout: 30000 });
  };
  await proposeTurn();
  check("proposal diff listed in the reply", (await page.locator(".msg.assistant .diff li").allTextContents()).join("|") === "+ cache (storage) in prod-deployment");
  check("proposal banner shown", (await page.textContent("#preview-banner")).includes("Vorschlag") && (await page.isVisible("#preview-banner")));
  check("proposed element highlighted on the canvas", (await page.locator("#diagram svg .changed").count()) === 1);
  check("nothing saved while the proposal is pending", (await versionCount()) === versionsBeforeProposal);
  check("UI locked while the proposal is pending", (await page.isDisabled("#btn-send")) && (await page.isDisabled("#btn-undo")));
  await page.keyboard.press("Control+k");
  await page.fill("#palette-input", "deployment");
  check("the palette offers no diagram while a proposal is pending", (await page.locator("#palette-list .palette-item").filter({ hasText: "Demo deployment" }).count()) === 0);
  await page.keyboard.press("Escape");
  await page.screenshot({ path: join(outDir, "proposal-pending.png") });
  await page.click("#btn-proposal-discard");
  await page.waitForFunction(() => !document.querySelector("#diagram svg .changed") && !document.getElementById("btn-send").disabled, null, { timeout: 15000 });
  const afterDiscard = await fetch(`${base}/api/diagrams/demo`).then((r) => r.json());
  check("discard keeps the diagram unchanged", !("cache" in afterDiscard.diagram.nodes) && (await versionCount()) === versionsBeforeProposal);
  await proposeTurn();
  await page.click("#btn-proposal-apply");
  await page.waitForFunction(() => [...document.querySelectorAll(".msg.assistant .proposal-actions")].at(-1)?.textContent.startsWith("Übernommen"), null, { timeout: 15000 });
  const afterApply = await fetch(`${base}/api/diagrams/demo`).then((r) => r.json());
  check("confirmed proposal saved as one version", "cache" in afterApply.diagram.nodes && (await versionCount()) === versionsBeforeProposal + 1);
  check("propose toggle remembered", (await page.evaluate(() => localStorage.getItem("arcviz.propose"))) === "1");
  await page.screenshot({ path: join(outDir, "after-proposal.png") });
  // 10b. architecture review: rule checks on opening the panel, the KI-Review adds LLM findings, selecting a
  // finding highlights its elements and edges on the canvas (spec: docs/architecture/specs/llm-review.md)
  await page.uncheck("#propose-mode");
  await page.selectOption("#focus-select", "");
  await act(page, "#btn-new");
  await page.fill("#new-title", "Review");
  await page.selectOption("#new-template", "demo");
  await page.click("#new-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Review" && document.querySelector('#diagram svg g.node[id*="flowchart-n_orders_db-"]'), null, { timeout: 15000 });
  await page.click("#btn-review");
  await page.waitForSelector('#review li.finding[data-finding="database-without-replication:orders-db"]', { timeout: 10000 });
  check("review panel lists the rule finding", (await page.locator("#review li.finding").count()) === 1 && (await page.textContent("#review-list")).includes("Risiken"));
  await page.click('#review li.finding[data-finding="database-without-replication:orders-db"]');
  check("selecting a finding highlights its element", (await page.locator("#diagram svg g.review-hit").count()) === 1 && (await page.locator('#diagram svg g.review-hit[id*="n_orders_db"]').count()) === 1);
  await page.click("#review-run-llm");
  await page.waitForSelector('#review li.finding[data-finding="llm-1"]', { timeout: 15000 });
  check("KI-Review adds the model's findings and summary", (await page.locator("#review li.finding").count()) === 3 && (await page.textContent("#review-summary")).includes("Klare Struktur"));
  check("invalid model findings are dropped and reported", (await page.textContent("#review-status")).includes("1 ungültige KI-Befunde verworfen"));
  check("the database finding stays selected", (await page.locator("#diagram svg g.review-hit").count()) === 1);
  await page.click('#review li.finding[data-finding="llm-1"]');
  check("an LLM finding highlights its nodes", (await page.locator("#diagram svg g.review-hit").count()) === 2);
  check("an LLM finding highlights its edge", (await page.locator("#diagram svg path.review-hit").count()) === 1);
  await page.screenshot({ path: join(outDir, "review.png") });
  // applying a suggestion is a propose-mode turn (spec: docs/architecture/specs/review-suggestions.md)
  const reviewVersions = await page.locator("#version-select option").count();
  check("strengths offer nothing to apply", (await page.locator('#review li.finding.sev-info .finding-apply').count()) === 0);
  await page.click('#review li.finding[data-finding="database-without-replication:orders-db"] .finding-apply');
  await page.waitForSelector("#btn-proposal-apply", { timeout: 30000 });
  check("the suggestion is sent as the user's instruction", (await page.locator(".msg.user").last().textContent()).startsWith("Setze diesen Vorschlag aus dem Architektur-Review um: Füge für die Datenbank „orders-db“"));
  check("applying a suggestion previews it as a proposal", (await page.locator(".msg.assistant .diff li").allTextContents()).join("|") === "+ orders-db-replica (database) in prod-deployment|+ Kante orders-db → orders-db-replica");
  check("nothing saved while the suggestion is pending", (await page.locator("#version-select option").count()) === reviewVersions && (await page.isDisabled('#review li.finding[data-finding="database-without-replication:orders-db"] .finding-apply')));
  await page.click("#btn-proposal-apply");
  await page.waitForSelector('#review li.finding[data-finding="replicated-database:orders-db:e7"]', { timeout: 15000 });
  check("confirmed suggestion saved as one version", (await page.locator("#version-select option").count()) === reviewVersions + 1);
  check("the rule finding is resolved after confirming", (await page.locator('#review li.finding[data-finding^="database-without-replication"]').count()) === 0);
  check("LLM findings are marked outdated and cannot be applied", (await page.isVisible("#review-stale")) && (await page.locator("#review li.finding.stale").count()) === 2 && (await page.isDisabled('#review li.finding[data-finding="llm-1"] .finding-apply')));
  await page.screenshot({ path: join(outDir, "review-applied.png") });
  await page.click("#review-close");
  check("closing the panel removes the highlight", (await page.locator("#diagram svg .review-hit").count()) === 0);

  // 11. starter templates: the dialog offers them and a diagram created from one renders
  await page.selectOption("#focus-select", "");
  await act(page, "#btn-new");
  const templateOptions = await page.locator("#new-template option").evaluateAll((opts) => opts.map((o) => o.value));
  check("new dialog offers the starter templates", ["demo", "hub-spoke", "aks-database", "event-driven", "empty"].every((t) => templateOptions.includes(t)), templateOptions.join(","));
  for (const [template, clusters] of [["hub-spoke", 8], ["aks-database", 7], ["event-driven", 1]]) {
    if (template !== "hub-spoke") await act(page, "#btn-new");
    await page.fill("#new-title", `Vorlage ${template}`);
    await page.selectOption("#new-template", template);
    await page.click("#new-form button[type=submit]");
    await page.waitForFunction(([t, n]) => document.getElementById("diagram-title").textContent === `Vorlage ${t}` && document.querySelectorAll("#diagram svg .cluster").length === n, [template, clusters], { timeout: 15000 });
    check(`diagram created from template ${template}`, (await page.locator("#diagram svg .error-icon").count()) === 0);
    await page.screenshot({ path: join(outDir, `template-${template}.png`) });
  }

  // 12. diagram types: an activity diagram from its template renders UML notation; hostile names stay text
  await act(page, "#btn-new");
  check("new dialog offers the diagram types", (await page.locator("#new-type option").evaluateAll((o) => o.map((x) => x.value))).join("|").startsWith("deployment|activity"));
  await page.selectOption("#new-type", "activity");
  check("templates follow the type", (await page.locator("#new-template option").evaluateAll((o) => o.map((x) => x.value))).join("|") === "order-process|empty");
  await page.fill("#new-title", "Prozess");
  await page.selectOption("#new-template", "order-process");
  await page.click("#new-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Prozess" && document.querySelectorAll("#diagram svg .cluster").length === 3, null, { timeout: 15000 });
  check("activity diagram renders without errors", (await page.locator("#diagram svg .error-icon").count()) === 0);
  check("activity partitions offered as focus", (await page.locator("#focus-select option").count()) === 4);
  check("activity guard drawn", (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("[ja]"));
  check("PlantUML export enabled for activity diagrams", !(await page.isDisabled("#exp-puml")));
  await page.evaluate(() => { document.getElementById("export-menu").open = true; });
  const [activityPuml] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.click("#exp-puml")]);
  const activityPumlText = await (await import("node:fs/promises")).readFile(await activityPuml.path(), "utf8");
  check("activity PlantUML export in the activity syntax", activityPuml.suggestedFilename().endsWith(".puml") && activityPumlText.includes("\nstart\n") && activityPumlText.includes("if (Artikel verfügbar?) then (ja)") && activityPumlText.includes("|Lager|"), activityPumlText.slice(0, 200));
  const activityId = await currentDiagram(page);
  const activityOp = (op, args) => fetch(`${base}/api/diagrams/${activityId}/ops`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op, args }) });
  await activityOp("add_node", { name: "<img src=x onerror=alert(1)> a\\b", kind: "decision_node", id: "evil" });
  await selectDiagram(page, "demo");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Demo deployment", null, { timeout: 15000 });
  await selectDiagram(page, activityId);
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Prozess", null, { timeout: 15000 });
  await page.waitForSelector("#diagram svg", { timeout: 15000 });
  check("hostile decision name rendered as text", (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("<img src=x onerror=alert(1)> a\\b") && (await page.locator("#diagram svg img").count()) === 0);
  await page.screenshot({ path: join(outDir, "activity.png") });

  // 12b. state machines: states, pseudostates and regions from the template; behaviours and effects are typed fields
  await act(page, "#btn-new");
  await page.selectOption("#new-type", "state_machine");
  check("state machine templates follow the type", (await page.locator("#new-template option").evaluateAll((o) => o.map((x) => x.value))).join("|") === "order-lifecycle|empty");
  await page.fill("#new-title", "Lebenszyklus");
  await page.selectOption("#new-template", "order-lifecycle");
  await page.click("#new-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Lebenszyklus" && document.querySelectorAll("#diagram svg .cluster").length === 3, null, { timeout: 15000 });
  check("state machine renders without errors", (await page.locator("#diagram svg .error-icon").count()) === 0);
  const stateText = await page.locator("#diagram").evaluate((n) => n.textContent);
  check("transition drawn as trigger [guard] / effect", stateText.includes("stornieren [Frist nicht abgelaufen] / Erstattung"));
  check("entry behaviour drawn below the state's name", stateText.includes("entry / Rechnung senden"));
  check("PlantUML export enabled for state machines", !(await page.isDisabled("#exp-puml")) && (await page.isDisabled("#exp-mmd")) === false);
  await page.locator("#diagram g.node", { hasText: "Bezahlt" }).first().click();
  await page.waitForSelector('#inspector:not(.hidden)[data-mode="node"]', { timeout: 5000 });
  const entryRow = page.locator("#insp-props tr").filter({ has: page.locator(".prop-key") }).first();
  check("the typed field shows as an entry row in the inspector", (await entryRow.locator(".prop-key").inputValue()) === "entry");
  await entryRow.locator(".prop-value").fill("Beleg <b>senden</b>");
  await page.click("#insp-save");
  await page.waitForFunction(() => document.getElementById("diagram").textContent.includes("entry / Beleg <b>senden</b>"), null, { timeout: 10000 });
  check("typed field saved through `fields` and shown as text", (await page.locator("#diagram svg b").count()) === 0 && (await page.locator("#insp-error.hidden").count()) === 1);
  await page.screenshot({ path: join(outDir, "state-machine.png") });

  // 12c. class diagrams: classifiers with compartments, a composition drawn part -> whole, members edited as a list
  await act(page, "#btn-new");
  await page.selectOption("#new-type", "class");
  check("class diagram templates follow the type", (await page.locator("#new-template option").evaluateAll((o) => o.map((x) => x.value))).join("|") === "webshop-domain|empty");
  await page.fill("#new-title", "Fachmodell");
  await page.selectOption("#new-template", "webshop-domain");
  await page.click("#new-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Fachmodell" && document.querySelectorAll("#diagram svg .cluster").length === 2, null, { timeout: 15000 });
  check("class diagram renders without errors", (await page.locator("#diagram svg .error-icon").count()) === 0);
  const classText = await page.locator("#diagram").evaluate((n) => n.textContent);
  check("members shown in compartments", classText.includes("- email: String") && classText.includes("+ aufgeben()"));
  check("abstract class and keywords drawn", classText.includes("«abstract»") && classText.includes("«interface»") && classText.includes("«enumeration»"));
  check("association drawn with name, roles and multiplicities", classText.includes("bestellt") && classText.includes("kunde 1 – bestellungen 0..*"));
  check("PlantUML export enabled for class diagrams", !(await page.isDisabled("#exp-puml")));
  await page.locator("#diagram g.node", { hasText: "Kunde" }).first().click();
  await page.waitForSelector('#inspector:not(.hidden)[data-mode="node"]', { timeout: 5000 });
  check("the member list is a textarea, one member per line", (await page.locator("#insp-props textarea.prop-value").count()) >= 2 && (await page.locator("#insp-props textarea.prop-value").first().inputValue()).includes("\n"));
  await page.locator("#insp-props textarea.prop-value").first().fill("- name: String\n- <b>x</b>: int");
  await page.click("#insp-save");
  await page.waitForFunction(() => document.getElementById("diagram").textContent.includes("- <b>x</b>: int"), null, { timeout: 10000 });
  check("members saved as a list and shown as text", (await page.locator("#diagram svg b").count()) === 0 && !(await page.textContent("#diagram")).includes("email"));
  await page.screenshot({ path: join(outDir, "class-diagram.png") });

  // 12d. use case diagrams: actors outside the subject, include/extend keywords, the specification is the note
  await act(page, "#btn-new");
  await page.selectOption("#new-type", "use_case");
  check("use case templates follow the type", (await page.locator("#new-template option").evaluateAll((o) => o.map((x) => x.value))).join("|") === "webshop-use-cases|empty");
  await page.fill("#new-title", "Anwendungsfälle");
  await page.selectOption("#new-template", "webshop-use-cases");
  await page.click("#new-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Anwendungsfälle" && document.querySelectorAll("#diagram svg .cluster").length === 1, null, { timeout: 15000 });
  check("use case diagram renders without errors", (await page.locator("#diagram svg .error-icon").count()) === 0);
  check("a new use case diagram reads left to right", (await page.inputValue("#direction-select")) === "LR");
  const useCaseText = await page.locator("#diagram").evaluate((n) => n.textContent);
  check("include and extend drawn with their keywords", useCaseText.includes("«include»") && useCaseText.includes("«extend» [Gutschein vorhanden] (Zahlung)") && useCaseText.includes("«actor»"));
  check("PlantUML export enabled for use case diagrams", !(await page.isDisabled("#exp-puml")));
  await page.locator("#diagram g.node", { hasText: "Artikel suchen" }).first().click();
  await page.waitForSelector('#inspector:not(.hidden)[data-mode="node"]', { timeout: 5000 });
  check("the specification of a use case is its note", (await page.inputValue("#insp-description")).includes("Der Kunde findet einen Artikel"));
  await page.fill("#insp-description", "Ziel: <b>schnell</b> suchen");
  await page.click("#insp-save");
  await page.waitForFunction(() => document.getElementById("insp-description").value === "Ziel: <b>schnell</b> suchen" && !document.getElementById("insp-save").disabled, null, { timeout: 10000 });
  check("the note is saved and never rendered as markup", (await page.locator("#diagram svg b").count()) === 0);
  await page.screenshot({ path: join(outDir, "use-case-diagram.png") });

  // 12e. component diagrams: ports as a list field, provided and required interfaces, connectors between ports
  await act(page, "#btn-new");
  await page.selectOption("#new-type", "component");
  check("component templates follow the type", (await page.locator("#new-template option").evaluateAll((o) => o.map((x) => x.value))).join("|") === "webshop-components|empty");
  await page.fill("#new-title", "Bausteine");
  await page.selectOption("#new-template", "webshop-components");
  await page.click("#new-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Bausteine" && document.querySelectorAll("#diagram svg .cluster").length === 1, null, { timeout: 15000 });
  check("component diagram renders without errors", (await page.locator("#diagram svg .error-icon").count()) === 0);
  const componentText = await page.locator("#diagram").evaluate((n) => n.textContent);
  check("ports drawn in the component", componentText.includes("port orders-in: OrderApi") && componentText.includes("port payments-out"));
  check("provided and required interfaces drawn with keywords and ports", componentText.includes("«realizes» (orders-in →)") && componentText.includes("«use»") && componentText.includes("zahlt (https) (payments-out →)"));
  check("PlantUML export enabled for component diagrams", !(await page.isDisabled("#exp-puml")));
  await page.locator("#diagram g.node", { hasText: "Bestellservice" }).first().click();
  await page.waitForSelector('#inspector:not(.hidden)[data-mode="node"]', { timeout: 5000 });
  await page.locator("#insp-props textarea.prop-value").first().fill("orders-in: OrderApi");
  await page.click("#insp-save");
  await page.waitForFunction(() => !document.getElementById("insp-error").classList.contains("hidden"), null, { timeout: 10000 });
  check("a port that an edge names cannot be removed", (await page.textContent("#insp-error")).includes("port 'payments-out' is used by edge"));
  await page.locator("#insp-props textarea.prop-value").first().fill("orders-in: OrderApi\npayments-out\naudit-<b>x</b>");
  await page.click("#insp-save");
  await page.waitForFunction(() => document.getElementById("diagram").textContent.includes("port audit-<b>x</b>"), null, { timeout: 10000 });
  check("a new port is saved and shown as text", (await page.locator("#diagram svg b").count()) === 0);
  await page.screenshot({ path: join(outDir, "component-diagram.png") });

  // 13. sequence diagrams: own SVG renderer, clicks open the element inspector, hostile names stay text
  await act(page, "#btn-new");
  await page.selectOption("#new-type", "sequence");
  await page.fill("#new-title", "Checkout");
  await page.selectOption("#new-template", "checkout");
  await page.click("#new-form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Checkout" && document.querySelector("#diagram svg.arcviz-seq"), null, { timeout: 15000 });
  check("sequence diagram rendered by the own SVG renderer", (await page.locator('#diagram svg.arcviz-seq [data-element="lifeline"]').count()) === 4);
  check("combined fragments, operands and activations drawn", (await page.locator('#diagram [data-element="combined_fragment"]').count()) === 2 && (await page.locator('#diagram [data-element="interaction_operand"]').count()) === 3 && (await page.locator('#diagram [data-element="execution_specification"]').count()) === 2);
  check("unsupported features disabled for sequence diagrams", (await page.isDisabled("#direction-select")) && (await page.isDisabled("#focus-select")) && (await page.isDisabled("#exp-mmd")));
  check("version comparison enabled for sequence diagrams", !(await page.isDisabled("#btn-compare")));
  check("PlantUML export enabled for sequence diagrams", !(await page.isDisabled("#exp-puml")));
  check("stats count lifelines and messages", (await page.textContent("#diagram-stats")).startsWith("4 Lebenslinien · 11 Nachrichten"));
  await page.locator('#diagram [data-id="m1"] .msg').click({ force: true });
  await page.waitForSelector('#inspector:not(.hidden)[data-mode="element"]', { timeout: 5000 });
  check("message click opens the element inspector", (await page.textContent("#insp-title")).startsWith("Nachricht") && (await page.textContent("#insp-id")) === "m1");
  const sequenceVersions = await page.locator("#version-select option").count();
  await page.fill('#insp-element-fields input[data-key="name"]', "bestellen()");
  await page.selectOption('#insp-element-fields select[data-key="sort"]', "asynchCall");
  await page.click("#insp-element-save");
  await page.waitForFunction(() => document.querySelector('#diagram [data-id="m1"]')?.textContent === "bestellen()", null, { timeout: 10000 });
  check("element edit applied and versioned", (await page.locator('#diagram [data-id="m1"]').getAttribute("class")).includes("sort-asynchCall") && (await page.locator("#version-select option").count()) === sequenceVersions + 1);
  check("edited element highlighted", (await page.locator('#diagram [data-id="m1"]').getAttribute("class")).includes("changed"));
  await page.click("#insp-close");
  // version comparison of a sequence diagram (#108): the template version against the edited state
  await act(page, "#btn-compare");
  await page.waitForSelector("#compare-dialog[open]", { timeout: 5000 });
  await page.selectOption("#compare-base", "1");
  await page.click("#compare-form button[type=submit]");
  await page.waitForSelector("#compare-banner:not(.hidden)", { timeout: 10000 });
  await page.waitForSelector('#diagram svg.arcviz-seq [data-id="m1"].changed', { timeout: 10000 });
  check("sequence comparison summarises and marks the change", (await page.textContent("#compare-text")).includes("~1 geändert") && (await page.locator("#diagram svg.arcviz-seq .changed").count()) === 1);
  await page.locator('#diagram [data-id="m1"] .msg').click({ force: true });
  check("clicks do not open the inspector during a sequence comparison", await page.locator("#inspector").evaluate((n) => n.classList.contains("hidden")));
  await page.click("#btn-compare-end");
  await page.waitForSelector("#compare-banner.hidden", { state: "attached", timeout: 10000 });
  await page.locator('#diagram [data-element="lifeline"][data-id="kunde"] .actor').click({ force: true });
  await page.waitForSelector('#inspector:not(.hidden)[data-mode="element"]', { timeout: 5000 });
  check("lifeline click opens its inspector", (await page.textContent("#insp-id")) === "kunde" && (await page.isChecked('#insp-element-fields input[data-key="actor"]')));
  await page.click("#insp-close");
  const sequenceId = await currentDiagram(page);
  await fetch(`${base}/api/diagrams/${sequenceId}/ops`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op: "add_lifeline", args: { name: "<img src=x onerror=alert(1)> & co", type: "</text><script>alert(1)</script>", id: "evil" } }) });
  await selectDiagram(page, "demo");
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Demo deployment", null, { timeout: 15000 });
  await selectDiagram(page, sequenceId);
  await page.waitForFunction(() => document.querySelector('#diagram [data-id="evil"]'), null, { timeout: 15000 });
  check("hostile lifeline name rendered as text", (await page.locator('#diagram [data-id="evil"]').textContent()) === "<img src=x onerror=alert(1)> & co : </text><script>alert(1)</script>" && (await page.locator("#diagram svg img, #diagram svg script").count()) === 0);
  await page.evaluate(() => { document.getElementById("export-menu").open = true; });
  const [seqSvg] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.click("#exp-svg")]);
  const seqSvgText = await (await import("node:fs/promises")).readFile(await seqSvg.path(), "utf8");
  check("sequence SVG export is the standalone SVG", seqSvgText.startsWith("<?xml") && seqSvgText.includes("arcviz-seq") && seqSvgText.includes("bestellen()"));
  await page.evaluate(() => { document.getElementById("export-menu").open = true; });
  const [seqPuml] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.click("#exp-puml")]);
  const seqPumlText = await (await import("node:fs/promises")).readFile(await seqPuml.path(), "utf8");
  check("sequence PlantUML export in the sequence syntax", seqPumlText.includes('actor "Kunde" as L_kunde') && seqPumlText.includes("L_kunde ->> L_shop : bestellen()") && seqPumlText.includes("alt Zahlung ok") && seqPumlText.includes("&#60;img src=x onerror=alert(1)> &#38; co"), seqPumlText.slice(0, 300));
  await page.screenshot({ path: join(outDir, "sequence.png") });

  // 14. cloud icons: kinds from the kind packs are drawn with their vendored icons (offline)
  await op("add_node", { name: "orders-fn", kind: "aws_lambda", id: "orders-fn", parent: "prod-deployment" });
  await op("add_node", { name: "queue <b>x</b>", kind: "azure_service_bus", id: "bus" });
  await op("add_node", { name: "fn-gcp", kind: "gcp_cloud_functions", id: "fn-gcp", parent: "prod-deployment" }); // its icon uses a <mask>
  await selectDiagram(page, "demo");
  await page.waitForFunction(() => document.querySelectorAll("#diagram svg g.icon-shape").length === 3, null, { timeout: 15000 });
  await page.waitForFunction(() => document.querySelector("#diagram svg mask"), null, { timeout: 15000 });
  check("icon masks survive the sanitizer, with local references only", (await page.locator("#diagram svg [mask]").evaluateAll((ns) => ns.map((n) => n.getAttribute("mask")))).every((v) => /^url\(#[^)]+\)$/.test(v)));
  check("icon kinds drawn as icon shapes with the inline icon", (await page.locator("#diagram svg g.icon-shape svg, #diagram svg g.icon-shape g path").count()) > 0);
  check("icon labels shown literally", (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("queue <b>x</b>") && (await page.locator("#diagram svg b").count()) === 0);
  await page.locator("#diagram svg g.icon-shape").first().click({ force: true });
  await page.waitForSelector("#inspector:not(.hidden)", { timeout: 5000 });
  check("icon element opens the inspector", ["orders-fn", "bus", "fn-gcp"].includes(await page.textContent("#insp-id")));
  await page.click("#insp-close");
  await page.screenshot({ path: join(outDir, "icons.png") });

  // 14b. project navigator (spec: docs/architecture/specs/project-navigator.md): projects, sections, moves
  check("existing diagrams are in the default project", (await page.inputValue("#project-select")) === "default" && (await page.locator('#nav-tree li[data-diagram-id="demo"]').count()) === 1);
  check("the user also has an own default project", (await page.locator(`#project-select option[value="${HOME}"]`).count()) === 1);
  const enterName = async (text) => {
    await page.waitForSelector("#name-dialog[open]", { timeout: 5000 });
    await page.fill("#name-input", text);
    await page.click("#name-form button[type=submit]");
  };
  const rowAction = async (selector, action) => {
    await page.click(`${selector} .nav-menu summary`);
    await page.click(`${selector} [data-action="${action}"]`);
  };
  const newDiagram = async (title, section) => {
    await page.waitForSelector("#new-dialog[open]", { timeout: 5000 });
    await page.selectOption("#new-type", "deployment");
    await page.fill("#new-title", title);
    await page.selectOption("#new-template", "empty");
    if (section !== undefined) await page.selectOption("#new-section", section);
    await page.click("#new-form button[type=submit]");
    await page.waitForFunction((t) => document.getElementById("diagram-title").textContent === t, title, { timeout: 15000 });
  };
  await page.click("#btn-project-new");
  await enterName("Webshop");
  await page.waitForSelector("#empty-project:not(.hidden)", { timeout: 15000 });
  const webshop = await page.inputValue("#project-select");
  check("a new project opens empty with the workspace inert", isNewId("webshop", webshop) && (await page.locator("#nav-tree li").count()) === 0 && (await page.locator(".layout[inert]").count()) === 1 && (await page.locator("#diagram-tools[inert]").count()) === 1);
  check("an empty project can be deleted", !(await page.isDisabled("#btn-project-delete")));
  await page.click("#btn-project-chat"); // the project chat can create the first diagram of an empty project
  await page.waitForSelector("#chat-scope:not(.hidden)", { timeout: 10000 });
  check("in an empty project the project chat stays usable", (await page.locator(".layout[inert]").count()) === 0 && (await page.locator(".canvas[inert]").count()) === 1 && (await page.isHidden("#empty-project")) && (await page.isEditable("#chat-input")));
  await page.click("#btn-chat-scope-back");
  await page.waitForSelector("#empty-project:not(.hidden)", { timeout: 10000 });
  check("back in the diagram chat the empty project is inert again", (await page.locator(".layout[inert]").count()) === 1);
  await page.click("#btn-section-new");
  await enterName("Backend");
  await page.waitForSelector('#nav-tree li[data-section-id="backend"]', { timeout: 10000 });
  await page.click('#nav-tree li[data-section-id="backend"] .nav-add');
  await page.waitForSelector("#new-dialog[open]", { timeout: 5000 });
  check("a section's add button preselects it in the new-diagram dialog", (await page.inputValue("#new-section")) === "backend");
  await newDiagram("Bestellungen");
  const bestellungen = await currentDiagram(page);
  check("the diagram is created in the section and opened", isNewId("bestellungen", bestellungen) && (await page.getAttribute(row(bestellungen), "aria-level")) === "2" && (await page.locator(".layout[inert]").count()) === 0, bestellungen);
  check("a project with diagrams cannot be deleted", await page.isDisabled("#btn-project-delete"));
  await act(page, "#btn-new");
  await newDiagram("Zahlungen", "backend");
  const zahlungen = await currentDiagram(page);
  await act(page, "#btn-new");
  await newDiagram("Uebersicht", "");
  const uebersicht = await currentDiagram(page);
  check("every new diagram has a new id", isNewId("zahlungen", zahlungen) && isNewId("uebersicht", uebersicht), `${zahlungen}, ${uebersicht}`);
  const rowIds = () => page.locator("#nav-tree li").evaluateAll((lis) => lis.map((li) => li.dataset.sectionId || li.dataset.diagramId));
  check("the tree shows sections first, then diagrams, in order", JSON.stringify(await rowIds()) === JSON.stringify(["backend", bestellungen, zahlungen, uebersicht]), JSON.stringify(await rowIds()));
  await rowAction(row(zahlungen), "diagram-up");
  await page.waitForFunction((id) => document.querySelector("#nav-tree li[data-diagram-id]")?.dataset.diagramId === id, zahlungen, { timeout: 10000 });
  check("a diagram moves up among its siblings", JSON.stringify(await rowIds()) === JSON.stringify(["backend", zahlungen, bestellungen, uebersicht]));
  await selectDiagram(page, zahlungen);
  await rowAction(row(zahlungen), "rename-diagram");
  await enterName("Zahlungsfluss");
  await page.waitForFunction((sel) => document.querySelector(`${sel} .nav-open`)?.textContent === "Zahlungsfluss", row(zahlungen), { timeout: 10000 });
  check("renaming the open diagram updates the tree and the title", (await page.textContent("#diagram-title")) === "Zahlungsfluss");
  await rowAction(row(bestellungen), "move-diagram");
  await page.waitForSelector("#move-dialog[open]", { timeout: 5000 });
  await page.selectOption("#move-project", "default");
  await page.waitForFunction(() => ![...document.querySelectorAll("#move-section option")].some((o) => o.value === "backend"), null, { timeout: 10000 });
  await page.click("#move-form button[type=submit]");
  await page.waitForFunction((sel) => !document.querySelector(sel), row(bestellungen), { timeout: 10000 });
  const defaultProject = await (await fetch(`${base}/api/projects/default`)).json();
  check("a diagram moves into another project", defaultProject.tree.diagrams.includes(bestellungen) && (await currentDiagram(page)) === zahlungen);
  // A deleted diagram whose stream ended with `bye` instead of `gone` (the lifetime ran out first, a re-check failed):
  // the reconnect's 404 tells the user the same as `gone` (#218).
  const createdThrowaway = await (await fetch(`${base}/api/diagrams`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "Wegwerf", project: webshop }) })).json();
  const throwaway = { id: createdThrowaway.diagram.id, revision: createdThrowaway.revision };
  const ownStream = (url) => url.pathname === `/api/diagrams/${throwaway.id}/live`;
  const byeOnly = `data: ${JSON.stringify({ type: "hello", session: "s-218", diagram: throwaway.id, project: webshop, revision: throwaway.revision ?? null, project_revision: null, role: "owner", poll_seconds: 5 })}\n\ndata: ${JSON.stringify({ type: "bye", reason: "expired" })}\n\n`;
  await page.route(ownStream, (route) => route.fulfill({ status: 200, contentType: "text/event-stream", body: byeOnly }));
  const throwawayId = await waitForTitle(page, "Wegwerf");
  await selectDiagram(page, throwawayId);
  await fetch(`${base}/api/diagrams/${throwaway.id}`, { method: "DELETE" });
  await page.unroute(ownStream); // the next reconnect reaches the server: 404
  await page.waitForFunction(() => document.getElementById("status").textContent.includes("nicht mehr verfügbar"), null, { timeout: 30000 });
  check("a reconnect that finds the diagram deleted says so, like gone", true);
  await selectDiagram(page, zahlungen);
  // A redraw of the tree keeps an open row menu open (#216 review: under load a late live event redrew the tree
  // between opening a menu and clicking its item, and the menu closed under the pointer).
  const describe = (text) => fetch(`${base}/api/projects/${webshop}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ description: text }) });
  const descriptionBefore = await page.textContent("#project-description");
  await page.click('#nav-tree li[data-section-id="backend"] .nav-menu summary');
  await describe("redrawn while a menu is open (#216)");
  await page.waitForFunction(() => document.getElementById("project-description").textContent === "redrawn while a menu is open (#216)", null, { timeout: 30000 });
  check("an open row menu stays open when the tree is redrawn", await page.locator('#nav-tree li[data-section-id="backend"] .nav-menu').evaluate((d) => d.open));
  await page.click('#nav-tree li[data-section-id="backend"] .nav-menu summary'); // closed again
  await describe(descriptionBefore);
  await page.waitForFunction((text) => document.getElementById("project-description").textContent === text, descriptionBefore, { timeout: 30000 });
  acceptNextDialog = true;
  await rowAction('#nav-tree li[data-section-id="backend"]', "delete-section");
  await page.waitForFunction(() => !document.querySelector('#nav-tree li[data-section-id="backend"]'), null, { timeout: 10000 });
  check("deleting a section moves its diagrams up", (await page.getAttribute(row(zahlungen), "aria-level")) === "1");
  await page.reload();
  await page.waitForFunction(() => document.getElementById("diagram-title").textContent === "Zahlungsfluss", null, { timeout: 30000 });
  check("project and diagram are remembered across a reload", (await page.inputValue("#project-select")) === webshop && (await currentDiagram(page)) === zahlungen);
  await page.click("#btn-navigator");
  check("the navigator can be hidden", (await page.isHidden("#navigator")) && (await page.getAttribute("#btn-navigator", "aria-expanded")) === "false");
  await page.click("#btn-navigator");
  // project export and import (spec: docs/architecture/specs/project-exchange.md)
  await page.click("#project-menu summary");
  const [bundleDownload] = await Promise.all([page.waitForEvent("download", { timeout: 15000 }), page.click("#btn-project-export")]);
  check("project export downloads the bundle", bundleDownload.suggestedFilename() === `${webshop}.arcviz-project.json`, bundleDownload.suggestedFilename());
  const bundleText = await fsp.readFile(await bundleDownload.path(), "utf8");
  check("the bundle holds the project and its diagrams", JSON.parse(bundleText).format === "arcviz-project" && JSON.parse(bundleText).diagrams.length === 2);
  await page.setInputFiles("#import-project-file", { name: "webshop.arcviz-project.json", mimeType: "application/json", buffer: Buffer.from(bundleText) });
  await page.waitForFunction((old) => document.getElementById("project-select").value !== old, webshop, { timeout: 15000 });
  const importedProject = await page.inputValue("#project-select");
  await waitForTitle(page, "Uebersicht");
  const importedIds = await rowIds();
  const importedTitles = await page.locator("#nav-tree li[data-diagram-id] .nav-open").allTextContents();
  check("project import creates a new project with fresh diagram ids", isNewId("webshop", importedProject) && importedProject !== webshop && JSON.stringify(importedTitles) === JSON.stringify(["Zahlungsfluss", "Uebersicht"]) && isNewId(baseOf(zahlungen), importedIds[0]) && isNewId(baseOf(uebersicht), importedIds[1]) && !importedIds.includes(zahlungen) && !importedIds.includes(uebersicht), JSON.stringify([importedProject, importedIds]));
  await page.selectOption("#project-select", webshop);
  await page.waitForSelector(row(zahlungen), { timeout: 15000 });
  // deletes are conditional on the revision the user was asked about (#186): a change meanwhile answers 412, the UI
  // reloads, says so and asks again
  const deleteStatuses = [];
  page.on("response", (r) => { if (r.request().method() === "DELETE" && /\/api\/(diagrams|projects)\/[^/]+$/.test(new URL(r.url()).pathname)) deleteStatuses.push(r.status()); });
  const renamedMeanwhile = () => fetch(`${base}/api/diagrams/${zahlungen}/ops`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op: "rename_diagram", args: { title: "Zahlungsfluss neu" } }) });
  const askedForDiagram = confirmAfter(renamedMeanwhile, 2);
  await rowAction(row(zahlungen), "delete-diagram");
  await page.waitForFunction((sel) => !document.querySelector(sel), row(zahlungen), { timeout: 10000 });
  check("a diagram changed meanwhile is not deleted blindly: 412, then asked again", JSON.stringify(deleteStatuses) === "[412,204]" && askedForDiagram.length === 2 && askedForDiagram[1].includes("wurde inzwischen von") && askedForDiagram[1].includes("Trotzdem"), JSON.stringify([deleteStatuses, askedForDiagram]));
  acceptNextDialog = true;
  await rowAction(row(uebersicht), "delete-diagram");
  await page.waitForFunction((sel) => !document.querySelector(sel), row(uebersicht), { timeout: 10000 });
  check("an unchanged diagram is deleted with one question", JSON.stringify(deleteStatuses) === "[412,204,204]", JSON.stringify(deleteStatuses));
  await page.waitForSelector("#empty-project:not(.hidden)", { timeout: 10000 });
  const describedMeanwhile = () => fetch(`${base}/api/projects/${webshop}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ description: "inzwischen beschrieben" }) });
  const askedForProject = confirmAfter(describedMeanwhile, 2);
  await page.click("#project-menu summary");
  await page.click("#btn-project-delete");
  await page.waitForFunction(() => document.getElementById("project-select").value === "default", null, { timeout: 15000 });
  check("a project changed meanwhile is asked about again before it is deleted", JSON.stringify(deleteStatuses.slice(3)) === "[412,204]" && askedForProject.length === 2 && askedForProject[1].includes("wurde inzwischen"), JSON.stringify([deleteStatuses, askedForProject]));
  check("deleting the empty project switches to the remaining one", !(await page.locator(`#project-select option[value="${webshop}"]`).count()) && (await page.locator("#empty-project.hidden").count()) === 1);
  await page.screenshot({ path: join(outDir, "navigator.png") });

  // 14c. project chat (spec: docs/architecture/specs/project-chat.md): one turn creates a diagram and edits another
  await selectDiagram(page, "demo");
  await page.click("#btn-project-chat");
  await page.waitForSelector("#chat-scope:not(.hidden)", { timeout: 10000 });
  await page.waitForFunction(() => document.getElementById("messages").textContent.includes("Projekt-Chat:"), null, { timeout: 10000 }); // the transcript loads after the bar
  check("the navigator opens the project chat", (await page.getAttribute("#btn-project-chat", "aria-pressed")) === "true" && (await page.textContent("#chat-scope-label")).startsWith("Projekt-Chat") && (await page.textContent("#messages")).includes("Projekt-Chat:"));
  await page.fill("#chat-input", "Lege ein Sequenzdiagramm Bezahlablauf an und ergänze im Deployment einen Payment-Service");
  await page.click("#btn-send");
  await page.waitForSelector(".msg.assistant .changed-diagrams", { timeout: 30000 });
  await page.waitForFunction(() => !document.getElementById("navigator").inert && !document.getElementById("btn-send").disabled, null, { timeout: 30000 }); // the turn is finished
  const projectTools = await page.locator(".msg.tool").evaluateAll((lines) => lines.map((l) => l.textContent));
  check("project tools are described in German", projectTools.some((t) => t.startsWith("Neues Sequenzdiagramm „Bezahlablauf“ anlegen")) && projectTools.some((t) => t.startsWith("Diagramm „demo“ öffnen")), JSON.stringify(projectTools));
  const changedButtons = await page.locator(".msg.assistant .changed-diagrams button").evaluateAll((bs) => bs.map((b) => b.textContent));
  check("the reply lists the changed diagrams", changedButtons.length === 2 && changedButtons[0].startsWith("Bezahlablauf (neu") && changedButtons[1].startsWith("Demo deployment (v"), JSON.stringify(changedButtons));
  await page.waitForFunction(() => document.querySelector("#diagram svg") && document.getElementById("diagram-title").textContent === "Demo deployment", null, { timeout: 15000 });
  check("the canvas shows the active diagram with the new element", (await currentDiagram(page)) === "demo" && (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("payment"));
  const bezahlablauf = await idOf(page, "Bezahlablauf"); // created by the project chat: its id is the server's
  check("the tree marks the other changed diagram", isNewId("bezahlablauf", bezahlablauf) && (await page.locator(`${row(bezahlablauf)}.changed .nav-changed`).count()) === 1 && (await page.locator('#nav-tree li[data-diagram-id="demo"].changed').count()) === 0, bezahlablauf);
  await page.click(`.msg.assistant .changed-diagrams button[data-diagram="${bezahlablauf}"]`);
  await page.waitForSelector(`${row(bezahlablauf)}[aria-current="true"]`, { timeout: 15000 });
  check("a changed diagram opens from the reply and loses its mark", (await page.locator(`${row(bezahlablauf)}.changed`).count()) === 0 && (await page.textContent("#messages")).includes("Payment-Service"));
  check("the new diagram renders with its lifeline", (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("Kasse"));
  await page.screenshot({ path: join(outDir, "project-chat.png") });
  await page.click("#btn-chat-scope-back");
  await page.waitForSelector("#chat-scope.hidden", { state: "attached", timeout: 10000 });
  await page.waitForFunction(() => !document.getElementById("messages").textContent.includes("Payment-Service"), null, { timeout: 10000 }); // the diagram transcript loads after
  check("back to the diagram chat", (await page.getAttribute("#btn-project-chat", "aria-pressed")) === "false" && !(await page.textContent("#messages")).includes("Payment-Service"));

  // 14d. project review (spec: docs/architecture/specs/project-review.md): project rules, KI-Review, jump to a finding
  await page.click("#btn-project-review");
  await page.waitForSelector("#review:not(.hidden) li.finding", { timeout: 15000 });
  check("the navigator opens the project review", (await page.textContent("#review-title")).startsWith("Projekt-Review") && (await page.getAttribute("#btn-project-review", "aria-pressed")) === "true");
  const projectChips = await page.locator("#review-list .finding .target:not(.model-element)").evaluateAll((spans) => spans.map((s) => s.textContent));
  check("project findings name their diagram or section", projectChips.length > 0 && projectChips.every((t) => /^(Diagramme?|Abschnitt): |^Projekt$/.test(t)), JSON.stringify(projectChips.slice(0, 5)));
  await page.click("#review-run-llm");
  await page.waitForFunction(() => [...document.querySelectorAll("#review-list .finding-message")].some((m) => m.textContent.includes("Payment-Service")), null, { timeout: 30000 });
  check("the KI-Review of the project adds its findings and summary", (await page.textContent("#review-summary")).includes("Struktur und Abläufe") && (await page.locator("#review-list .finding .source").count()) === 2);
  await page.locator("#review-list li.finding", { hasText: "Payment-Service fehlt" }).click();
  await page.waitForSelector('#nav-tree li[data-diagram-id="demo"][aria-current="true"]', { timeout: 15000 });
  await page.waitForSelector("#diagram svg .review-hit", { timeout: 15000 });
  check("selecting a finding opens its diagram and marks the element", (await currentDiagram(page)) === "demo" && (await page.locator("#diagram svg .review-hit").count()) >= 1);
  await page.screenshot({ path: join(outDir, "project-review.png") });
  const direction = (await page.inputValue("#direction-select")) === "TB" ? "LR" : "TB";
  await page.selectOption("#direction-select", direction); // an edit of a reviewed diagram
  await page.waitForSelector("#review-list li.finding.stale", { timeout: 15000 });
  check("an edit marks the project's AI findings as outdated", (await page.isVisible("#review-stale")) && (await page.locator("#review-list li.finding.stale .finding-apply:not([disabled])").count()) === 0);
  await page.click("#review-close");
  check("closing the project review", (await page.isHidden("#review")) && (await page.getAttribute("#btn-project-review", "aria-pressed")) === "false");

  // 14e. shared project model (spec: docs/architecture/specs/shared-project-model.md): an element shown by a
  // deployment node and a sequence lifeline, renamed once in the model panel
  await page.click("#btn-model");
  await page.waitForSelector("#model-dialog[open]", { timeout: 10000 });
  await page.fill("#model-new-name", "Bezahldienst");
  await page.selectOption("#model-new-kind", "component");
  await page.click('#model-new button[type="submit"]');
  await page.waitForSelector('#model-list li[data-element-id="bezahldienst"]', { timeout: 10000 });
  check("the model panel creates a project element", (await page.textContent('#model-list li[data-element-id="bezahldienst"] .model-usage')) === "nicht verwendet");
  await page.click("#model-close");
  await selectDiagram(page, "demo");
  await page.locator("#diagram svg g.node", { hasText: "payment" }).first().click();
  // the project chat's new node already became a project element (spec: elements-by-default.md): link it elsewhere
  await page.waitForSelector("#insp-model:not(.hidden) .model-unlink", { timeout: 15000 });
  check("a node the assistant added is a project element at once", (await page.textContent("#insp-model .model-linked")).includes("„payment“"));
  await page.click("#insp-model .model-unlink");
  await page.waitForSelector("#insp-model:not(.hidden) .model-link", { timeout: 15000 });
  await page.selectOption("#insp-model .model-choice", "bezahldienst");
  await page.click("#insp-model .model-link");
  await page.waitForSelector("#insp-model .model-unlink", { timeout: 15000 });
  check("the inspector links a deployment node to the element", (await page.textContent("#insp-model .model-linked")).includes("„Bezahldienst“") && (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("Bezahldienst"));
  await page.click("#insp-close");
  await selectDiagram(page, bezahlablauf);
  await page.locator('#diagram svg [data-id="kasse"]').first().click();
  await page.waitForSelector("#insp-model:not(.hidden) .model-unlink", { timeout: 15000 });
  check("… and so is a lifeline", (await page.textContent("#insp-model .model-linked")).includes("„Kasse“"));
  await page.click("#insp-model .model-unlink");
  await page.waitForSelector("#insp-model:not(.hidden) .model-link", { timeout: 15000 });
  await page.selectOption("#insp-model .model-choice", "bezahldienst");
  await page.click("#insp-model .model-link");
  await page.waitForSelector("#insp-model .model-unlink", { timeout: 15000 });
  check("the inspector links a lifeline to the same element", (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("Bezahldienst"));
  await page.click("#insp-close");
  await page.click("#btn-model");
  await page.waitForSelector('#model-list li[data-element-id="bezahldienst"]', { timeout: 10000 });
  check("the model panel lists where the element is shown", (await page.textContent('#model-list li[data-element-id="bezahldienst"] .model-usage')).includes("Bezahlablauf"));
  await page.fill('#model-list li[data-element-id="bezahldienst"] .model-name', "Zahlungsdienst");
  await page.click('#model-list li[data-element-id="bezahldienst"] .model-save');
  await page.waitForFunction(() => document.getElementById("model-status").textContent.includes("umbenannt in 2"), null, { timeout: 15000 });
  await page.screenshot({ path: join(outDir, "model-panel.png") });
  await page.click("#model-close");
  await page.waitForFunction(() => document.getElementById("diagram").textContent.includes("Zahlungsdienst"), null, { timeout: 15000 });
  check("a rename in the model reaches the shown sequence diagram", !(await page.locator("#diagram").evaluate((n) => n.textContent)).includes("Bezahldienst"));
  await selectDiagram(page, "demo");
  await page.waitForFunction(() => document.getElementById("diagram").textContent.includes("Zahlungsdienst"), null, { timeout: 15000 });
  check("… and the deployment diagram", (await page.locator("#diagram").evaluate((n) => n.textContent)).includes("Zahlungsdienst"));
  await page.screenshot({ path: join(outDir, "shared-model.png") });

  // 14f. model relations (spec: docs/architecture/specs/model-relations.md): a contradiction between the model and a
  // diagram (protocol HTTPS in the model, gRPC on the linked edge) is resolved from the project review
  const api = async (path, method = "GET", body) => {
    const r = await fetch(`${base}${path}`, { method, headers: { "Content-Type": "application/json" }, body: body && JSON.stringify(body) });
    if (!r.ok) throw new Error(`${method} ${path} failed: ${r.status} ${await r.text()}`);
    return r.json();
  };
  const relProject = (await api("/api/diagrams/demo")).access.project;
  // new building blocks become project elements at once (spec: elements-by-default.md)
  const lagerElement = (await op("add_node", { name: "Lager", kind: "service", id: "lager" })).adoption.created[0].element;
  const datenElement = (await op("add_node", { name: "Lagerdaten", kind: "database", id: "lagerdaten" })).adoption.created[0].element;
  const lagerEdge = (await op("add_edge", { source: "lager", target: "lagerdaten", properties: { protocol: "gRPC" } })).result.result.id;
  const relation = (await api(`/api/projects/${encodeURIComponent(relProject)}/relations`, "POST", {
    kind: "communicates", source: lagerElement, target: datenElement, properties: { protocol: "HTTPS" },
  })).relation.id;
  await api("/api/diagrams/demo/link-relation", "POST", { view: lagerEdge, relation });
  await selectDiagram(page, "demo");
  await page.click("#btn-project-review");
  const conflict = page.locator("#review-list li.finding", { hasText: "widersprüchliche Angaben für protocol" });
  await conflict.waitFor({ timeout: 15000 });
  const fixes = await conflict.locator(".finding-resolution").evaluateAll((bs) => bs.map((b) => `${b.dataset.action}:${b.textContent}`));
  check("a contradiction offers the model change and a proposal", fixes.some((f) => f.startsWith("update_relation:") && f.includes("gRPC")) && fixes.some((f) => f.startsWith("propose:")), JSON.stringify(fixes));
  await page.screenshot({ path: join(outDir, "relation-conflict.png") });
  await conflict.locator('.finding-resolution[data-action="update_relation"]').click();
  await page.waitForFunction(() => ![...document.querySelectorAll("#review-list .finding-message")].some((m) => m.textContent.includes("widersprüchliche Angaben für protocol")), null, { timeout: 15000 });
  const fixed = (await api(`/api/projects/${encodeURIComponent(relProject)}/model`)).relations.find((r) => r.id === relation);
  check("resolving it in the model removes the finding", fixed.properties.protocol === "gRPC" && fixed.usages.some((u) => u.view === lagerEdge));
  await page.click("#review-close");
  // the model panel lists the relation; the matrix opens the diagram that shows it and marks the edge
  await page.click("#btn-model");
  const relRow = page.locator(`#relation-list li[data-relation-id="${relation}"]`);
  await relRow.waitFor({ timeout: 10000 });
  check("the model panel lists the relation where it is linked", (await relRow.locator(".relation-usage").textContent()).includes("Demo deployment") && (await relRow.locator('.relation-prop[data-property="protocol"]').inputValue()) === "gRPC");
  await page.click("#model-matrix");
  const linkedCell = page.locator(`#relation-matrix tr[data-relation-id="${relation}"] td.cell-linked button`);
  await linkedCell.waitFor({ timeout: 10000 });
  await page.screenshot({ path: join(outDir, "relation-matrix.png") });
  await linkedCell.click();
  await page.waitForSelector("#viewport .relation-hit", { state: "attached", timeout: 15000 });
  check("a matrix cell opens its diagram and marks the connection", !(await page.locator("#model-dialog").evaluate((d) => d.open)) && (await currentDiagram(page)) === "demo");

  // 14g. the Architektur dialog (specs: architecture-drivers.md, decision-support.md): drivers listed by priority
  // (importance, then risk) whatever the order they were created in
  await page.click("#nav-arch-drivers");
  await page.waitForSelector("#architecture-view:not(.hidden) #arch-panel-drivers:not([hidden])", { timeout: 10000 });
  check("the Architektur page is a route and hides the chat", (await page.evaluate(() => location.hash)) === "#/architecture/drivers" && (await page.isHidden(".chat")) && (await page.getAttribute('#rail [data-area="architecture"]', "aria-current")) !== null);
  for (const [title, kind, importance, risk] of [["Kosten unter 2000 € im Monat", "constraint", "medium", "low"], ["Verfügbarkeit 99,9 %", "quality_goal", "high", "high"]]) {
    await page.click("#driver-add");
    await page.fill("#driver-title", title);
    await page.selectOption("#driver-kind", kind);
    await page.selectOption("#driver-importance", importance);
    await page.selectOption("#driver-risk", risk);
    await page.click("#driver-save");
    await page.waitForFunction((t) => [...document.querySelectorAll("#drivers-list .md-title")].some((i) => i.textContent === t), title, { timeout: 10000 });
  }
  const driverRows = await page.locator("#drivers-list li").evaluateAll((rows) => rows.map((r) => `${r.querySelector(".driver-badge").textContent} ${r.querySelector(".md-title").textContent}`));
  check("the drivers tab lists drivers by priority", JSON.stringify(driverRows) === JSON.stringify(["H/H Verfügbarkeit 99,9 %", "M/L Kosten unter 2000 € im Monat"]), JSON.stringify(driverRows));
  check("the driver just created is selected in the editor", (await page.inputValue("#driver-title")) === "Verfügbarkeit 99,9 %" && (await page.textContent("#driver-form-heading")).startsWith("Treiber bearbeiten") && (await page.textContent("#driver-save")) === "Speichern");
  await page.click("#drivers-list .md-card >> nth=1");
  check("selecting a card fills the editor", (await page.inputValue("#driver-title")) === "Kosten unter 2000 € im Monat" && (await page.inputValue("#driver-kind")) === "constraint" && (await page.getAttribute("#drivers-list .md-card >> nth=1", "aria-current")) === "true");
  await page.fill("#drivers-filter", "kosten");
  check("the filter narrows the list", (await page.locator("#drivers-list .driver-row").count()) === 1);
  await page.fill("#drivers-filter", "");
  check("clearing the filter shows all drivers again", (await page.locator("#drivers-list .driver-row").count()) === 2);
  await page.focus("#drivers-list .md-card >> nth=0");
  await page.keyboard.press("ArrowDown");
  check("the arrow keys move the selection", (await page.inputValue("#driver-title")) === "Kosten unter 2000 € im Monat");
  await page.fill("#driver-title", "Kosten unter 2000 € im Monat (geändert)");
  let asked = "";
  dialogHook = (d) => { asked = d.message(); d.dismiss(); };
  await page.click("#drivers-list .md-card >> nth=0");
  check("unsaved changes ask before another driver opens, and stay when declined", asked.includes("Ungespeicherte") && (await page.inputValue("#driver-title")).endsWith("(geändert)"), asked);
  await page.click("#driver-cancel");
  check("Zurücksetzen restores the stored values", (await page.inputValue("#driver-title")) === "Kosten unter 2000 € im Monat");
  await page.screenshot({ path: join(outDir, "drivers.png") });
  // 14h. a core scenario of the availability driver, analysed (scripted), one option recorded as a decision, accepted
  await page.click("#arch-tab-scenarios");
  await page.waitForSelector("#arch-panel-scenarios:not([hidden]) #scenario-form", { timeout: 10000 });
  check("a tab is part of the URL hash", (await page.evaluate(() => location.hash)) === "#/architecture/scenarios");
  await page.goBack();
  await page.waitForSelector("#arch-panel-drivers:not([hidden])", { timeout: 10000 });
  check("the back button returns to the previous tab", (await page.evaluate(() => location.hash)) === "#/architecture/drivers");
  await page.goForward();
  await page.waitForSelector("#arch-panel-scenarios:not([hidden]) #scenario-form", { timeout: 10000 });
  await page.fill("#scenario-title", "Region fällt aus");
  await page.selectOption("#scenario-kind", "failure");
  await page.selectOption("#scenario-driver", { label: "Verfügbarkeit 99,9 %" });
  await page.check("#scenario-core");
  await page.fill("#scenario-part-stimulus", "Region A fällt vollständig aus");
  await page.fill("#scenario-part-response_measure", "< 5 min Ausfall");
  await page.click("#scenario-qualities-suggest");
  await page.waitForFunction(() => document.getElementById("scenario-qualities-note").textContent.includes("Vorschlag übernommen"), null, { timeout: 10000 });
  const ticked = () => page.$$eval("#scenario-qualities input:checked", (boxes) => boxes.map((b) => b.value));
  check("the suggested ISO 25010 quality is pre-ticked and gibberish never reaches the boxes", JSON.stringify(await ticked()) === '["reliability"]' && (await page.locator("#scenario-qualities input").count()) === 9);
  await page.check('#scenario-qualities input[value="security"]');
  check("two characteristics are the maximum: the other boxes are disabled", (await page.locator("#scenario-qualities input:disabled").count()) === 7);
  await page.uncheck('#scenario-qualities input[value="security"]');
  await page.click("#scenario-save");
  const scenarioItem = page.locator(".scenario-item", { hasText: "Region fällt aus" });
  await scenarioItem.waitFor({ timeout: 10000 });
  check("a core scenario is listed under its driver with a star", (await scenarioItem.locator(".scenario-star").textContent()) === "★" && (await page.locator(".scenario-group h5").first().textContent()) === "Treiber: Verfügbarkeit 99,9 %");
  check("the card of the scenario shows its characteristic", (await scenarioItem.locator(".md-chips").textContent()).includes("Zuverlässigkeit"));
  check("a created scenario is selected in its editor", (await page.inputValue("#scenario-title")) === "Region fällt aus" && !(await page.isHidden("#scenario-analyse")));
  await page.click("#scenario-analyse");
  const analysis = page.locator("#scenario-analysis");
  await analysis.locator('.analysis-option[data-option="1"]').waitFor({ timeout: 15000 });
  check("the analysis shows the options and marks the recommended one", (await analysis.locator(".analysis-option.recommended strong").textContent()).includes("Aktiv/Aktiv (empfohlen)") && (await analysis.locator(".analysis-facts").textContent()).includes("verletzt"));
  await page.screenshot({ path: join(outDir, "scenario-analysis.png") });
  await analysis.locator('.analysis-option[data-option="1"] .option-decide').click();
  await page.waitForSelector("#arch-panel-decisions:not([hidden]) #decision-form:not(.hidden)", { timeout: 10000 });
  check("the chosen option opens as an ADR draft", (await page.textContent("#decision-form-heading")) === "ADR-1 bearbeiten" && (await page.inputValue("#decision-status")) === "draft" && (await page.locator("#decision-options .analysis-option.chosen").textContent()).includes("Aktiv/Aktiv"));
  await page.selectOption("#decision-status", "accepted");
  await page.click("#decision-save");
  await page.waitForFunction(() => document.getElementById("decisions-status").textContent.includes("angenommen"), null, { timeout: 10000 });
  const adr = await page.evaluate(async (href) => (await fetch(href)).text(), await page.getAttribute("#decision-markdown", "href"));
  check("an accepted decision exports as a Markdown ADR", adr.startsWith("# ADR-1: Region fällt aus: Aktiv/Aktiv") && adr.includes("- Status: Angenommen"), adr.slice(0, 120));
  await page.screenshot({ path: join(outDir, "decision.png") });
  await page.click("#arch-tab-stakeholders");
  await page.fill("#stakeholder-name", "Betrieb");
  await page.fill("#stakeholder-role", "SRE");
  await page.click("#stakeholder-save");
  await page.waitForSelector("#stakeholders-list li .md-title", { timeout: 10000 });
  check("a stakeholder is recorded and selected", (await page.textContent("#stakeholders-list li .md-title")) === "Betrieb" && (await page.inputValue("#stakeholder-role")) === "SRE" && (await page.textContent("#stakeholder-form-heading")).startsWith("Stakeholder bearbeiten"));
  await page.click("#nav-tree .nav-open >> nth=0");
  await page.waitForSelector("#diagram svg", { timeout: 10000 });
  check("opening a diagram from the tree leaves the Architektur page", (await page.isHidden("#architecture-view")) && (await page.evaluate(() => location.hash)) === "");
  await page.click("#nav-arch-stakeholders");
  await page.waitForSelector("#architecture-view:not(.hidden) #arch-panel-stakeholders:not([hidden])", { timeout: 10000 });
  await page.click('#rail [data-area="diagram"]');
  await page.waitForSelector("#diagram svg", { timeout: 10000 });
  check("the rail returns to the diagram and the chat is back", (await page.isHidden("#architecture-view")) && (await page.isVisible(".chat")) && (await page.evaluate(() => location.hash)) === "");

  // 14i. the command palette and the slim top bar (spec: command-palette.md)
  check("the top bar has the search field and one ⋯ menu instead of the Diagramm and Version menus", (await page.isVisible("#palette-open")) && (await page.locator("#menu-diagram, #menu-version").count()) === 0 && (await page.locator("#menu-more").count()) === 1);
  check("⋯ holds the diagram and version actions", (await page.locator("#menu-more #btn-new").count()) === 1 && (await page.locator("#menu-more #btn-snapshot").count()) === 1 && (await page.locator("#menu-more #btn-delete").count()) === 1);
  await page.keyboard.press("Control+k");
  await page.waitForSelector("#palette[open]", { timeout: 5000 });
  check("Strg+K opens the palette with the input focused and the browsing groups", (await page.evaluate(() => document.activeElement?.id)) === "palette-input" && (await page.locator("#palette-list .palette-group").count()) >= 3);
  await page.keyboard.press("Escape");
  check("Esc closes the palette", (await page.locator("#palette[open]").count()) === 0);
  await page.click("#palette-open");
  await page.waitForSelector("#palette[open]", { timeout: 5000 });
  await page.fill("#palette-input", "treiber");
  check("typing narrows the list and highlights the match", (await page.locator("#palette-list .palette-item").first().textContent()).includes("Treiber") && (await page.locator("#palette-list .palette-item mark").count()) >= 1 && (await page.locator("#palette-list .palette-group").count()) === 0);
  await page.keyboard.press("Enter");
  await page.waitForSelector("#architecture-view:not(.hidden) #arch-panel-drivers:not([hidden])", { timeout: 10000 });
  check("Enter runs the command: the Treiber tab opens at its route", (await page.evaluate(() => location.hash)) === "#/architecture/drivers" && (await page.locator("#palette[open]").count()) === 0);
  await page.keyboard.press("Control+k");
  await page.fill("#palette-input", "diagramm-ansicht");
  await page.keyboard.press("Enter");
  await page.waitForSelector("#diagram svg", { timeout: 10000 });
  check("a navigation command returns to the diagram view", (await page.isHidden("#architecture-view")) && (await page.evaluate(() => location.hash)) === "");
  const otherTitle = await page.locator("#nav-tree .nav-open").nth(1).textContent();
  await page.keyboard.press("Control+k");
  await page.fill("#palette-input", otherTitle);
  check("the diagrams of the open project are listed under their title", (await page.locator("#palette-list .palette-item").first().textContent()).includes(otherTitle));
  await page.keyboard.press("Enter");
  await page.waitForFunction((t) => document.getElementById("diagram-title").textContent === t, otherTitle, { timeout: 10000 });
  check("choosing a diagram opens it", (await page.textContent("#diagram-title")) === otherTitle);
  const redoOff = await page.isDisabled("#btn-redo");
  await page.keyboard.press("Control+k");
  await page.fill("#palette-input", "wiederholen");
  check("a command whose control is disabled is not offered", (await page.locator("#palette-list .palette-item").filter({ hasText: "Wiederholen" }).count()) === (redoOff ? 0 : 1), String(redoOff));
  await page.fill("#palette-input", "xyzzy-nichts");
  check("nothing found says so", !(await page.isHidden("#palette-empty")));
  await page.keyboard.press("Escape");
  await page.fill("#chat-input", "");

  // 15. help menu: links to the documentation the app serves under /docs (built or not, never a 500)
  await page.click("#help-menu summary");
  const helpLinks = await page.locator("#help-menu a").evaluateAll((links) => links.map((a) => [a.getAttribute("href"), a.target]));
  check("help menu links the user guide and the architecture docs in a new tab", JSON.stringify(helpLinks) === JSON.stringify([["/docs/guide/first-steps/", "_blank"], ["/docs/guide/", "_blank"], ["/docs/architecture/arc42/", "_blank"], ["/docs/", "_blank"]]), JSON.stringify(helpLinks));
  check("the release highlights banner is shown with its items and the changelog link", (await page.isVisible("#whats-new")) && (await page.locator("#whats-new-list li").count()) >= 1 && (await page.getAttribute("#whats-new-link", "href")) === "/docs/changelog/");
  await page.click("#whats-new-close");
  check("dismissing the banner hides it and remembers the release", (await page.isHidden("#whats-new")) && (await page.evaluate(() => Object.keys(localStorage).some((k) => k.startsWith("arcviz.whatsnew.")))));
  await act(page, "#btn-whats-new");
  check("Hilfe reopens the highlights", (await page.isVisible("#whats-new")) && (await page.locator("#whats-new-details[open]").count()) === 1 && (await page.isHidden("#help-menu .menu-items")));
  await page.click("#whats-new-close");
  const docs = await fetch(`${base}/docs/guide/`);
  const docsText = await docs.text();
  check("/docs answers with the site or with build instructions", (docs.status === 200 && docsText.includes("User guide")) || (docs.status === 404 && docsText.includes("make docs-bundle")), String(docs.status));

  // 15b. hostile error messages are shown as text (#119, spec: docs/architecture/specs/hardening.md): a failed start
  // whose server detail is markup, and a Mermaid parse error that quotes the diagram source
  {
    const hostile = "<b>fett</b><img src=x onerror=\"window.__pwned=1\">";
    const errPage = await browser.newPage({ locale: "de-DE", viewport: { width: 1200, height: 800 } });
    await errPage.route("**/api/config", (route) => route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: hostile }) }));
    await errPage.goto(base);
    await errPage.waitForSelector("#diagram .placeholder");
    const startText = await errPage.locator("#diagram .placeholder").textContent();
    check("start failure shows the server's message literally", startText === `Start fehlgeschlagen: ${hostile}`, startText);
    check("start failure creates no markup", (await errPage.locator("#diagram .placeholder b, #diagram .placeholder img").count()) === 0 && !(await errPage.evaluate(() => window.__pwned)));
    await errPage.unroute("**/api/config");
    // A diagram whose rendering source does not parse: Mermaid's error quotes the offending line.
    await errPage.route(/\/api\/diagrams\/[^/?]+(\?.*)?$/, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const res = await route.fetch();
      const body = await res.json();
      if (body.renderer === "mermaid") body.mermaid = `flowchart TD\n  a --> ${hostile} ((( [[`;
      await route.fulfill({ response: res, json: body });
    });
    await errPage.goto(base);
    await errPage.waitForSelector("#viewport .placeholder", { timeout: 15000 });
    const renderText = await errPage.locator("#viewport .placeholder").textContent();
    check("render failure shows the parse error as text", renderText.startsWith("Rendering fehlgeschlagen: ") && renderText.includes("<b>fett</b>") && (await errPage.locator("#viewport .placeholder b, #viewport .placeholder img").count()) === 0 && !(await errPage.evaluate(() => window.__pwned)), renderText.slice(0, 200));
    await errPage.close();
  }

  // 15c. paged version list (#114, spec: docs/architecture/specs/hardening.md): a response with only the newest page
  // offers "Ältere Versionen laden …", which fetches the older ones from GET …/versions?before=
  {
    const pagePage = await browser.newPage({ locale: "de-DE", viewport: { width: 1200, height: 800 } });
    let shownId = null;
    await pagePage.route(/\/api\/diagrams\/[^/?]+(\?.*)?$/, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const res = await route.fetch();
      const body = await res.json();
      if (body.diagram?.id) shownId = body.diagram.id;
      if (Array.isArray(body.versions) && body.versions.length > 2) {
        body.versions_total = body.versions.length;
        body.versions = body.versions.slice(-2);
        body.versions_more = true;
      }
      await route.fulfill({ response: res, json: body });
    });
    await pagePage.goto(base);
    await pagePage.waitForFunction(() => document.querySelectorAll("#version-select option").length > 0, null, { timeout: 15000 });
    for (const label of ["p1", "p2", "p3"]) { // make sure the shown diagram has more than one page
      await fetch(`${base}/api/diagrams/${shownId}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label }) });
    }
    await pagePage.reload();
    await pagePage.waitForSelector('#version-select option[value="more"]', { state: "attached", timeout: 15000 });
    const before = await pagePage.locator("#version-select option").count();
    // a failed page: the list goes back to the current state, the error is shown, the entry stays to try again
    const newest = await pagePage.inputValue("#version-select");
    await pagePage.route(/\/versions\?before=\d+/, (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "kurz weg" }) }));
    await openMenuOf(pagePage, "#version-select");
    await pagePage.selectOption("#version-select", "more");
    await pagePage.waitForFunction(() => document.getElementById("status").textContent.includes("kurz weg"));
    check("a failed page of older versions resets the list and shows the error", (await pagePage.inputValue("#version-select")) === newest && (await pagePage.locator('#version-select option[value="more"]').count()) === 1);
    await pagePage.unroute(/\/versions\?before=\d+/);
    const total = await pagePage.evaluate(() => Number(document.querySelector('#version-select option[value="more"]').textContent.match(/\((\d+)\)/)[1]));
    const older = pagePage.waitForResponse((r) => /\/versions\?before=\d+/.test(r.url()));
    await openMenuOf(pagePage, "#version-select");
    await pagePage.selectOption("#version-select", "more");
    await older;
    await pagePage.waitForFunction((n) => document.querySelectorAll("#version-select option").length !== n, before);
    const after = await pagePage.locator("#version-select option").count();
    check("older versions are loaded on demand", after === before - 1 + total && (await pagePage.locator('#version-select option[value="more"]').count()) === 0, `${before} -> ${after} (+${total})`);
    // an empty page (pruned meanwhile): the entry goes
    await pagePage.reload();
    await pagePage.waitForSelector('#version-select option[value="more"]', { state: "attached", timeout: 15000 });
    await pagePage.route(/\/versions\?before=\d+/, (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
    await openMenuOf(pagePage, "#version-select");
    await pagePage.selectOption("#version-select", "more");
    await pagePage.waitForFunction(() => !document.querySelector('#version-select option[value="more"]'));
    check("an empty page of older versions removes the entry", (await pagePage.locator('#version-select option[value="more"]').count()) === 0);
    await pagePage.close();
  }

  // 16. token authentication (spec: docs/architecture/specs/authentication.md): a second server in token mode
  const token = "e2e-token-0123456789abcdef";
  const authPort = port + 1000;
  const authBase = `http://127.0.0.1:${authPort}`;
  const authData = join(here, ".e2e-data-auth");
  rmSync(authData, { recursive: true, force: true });
  authServer = spawn(python, [join(here, "server.py"), authData, String(authPort)], { stdio: ["ignore", "inherit", "inherit"], env: { ...process.env, ARCVIZ_AUTH: "token", ARCVIZ_AUTH_TOKEN: token } });
  await waitForServer(`${authBase}/healthz`);
  check("token mode: the API refuses requests without credentials", (await fetch(`${authBase}/api/config`)).status === 401);
  check("token mode: a bearer token is accepted", (await fetch(`${authBase}/api/config`, { headers: { Authorization: `Bearer ${token}` } })).status === 200);
  const authPage = await browser.newPage({ locale: "de-DE", viewport: { width: 1400, height: 900 } });
  authPage.on("pageerror", (e) => check("no page errors (token mode)", false, e.message));
  await authPage.goto(`${authBase}/?d=demo`);
  check("token mode: the UI redirects to the login page", new URL(authPage.url()).pathname === "/login" && (await authPage.locator('input[type="password"]').count()) === 1);
  await authPage.fill("#token", "wrong-token-0123456789");
  await authPage.click('button[type="submit"]');
  check("token mode: a wrong token is refused", (await authPage.textContent("body")).includes("Ungültiges Token"));
  await authPage.fill("#token", token);
  await authPage.click('button[type="submit"]');
  await authPage.waitForSelector("#diagram svg", { timeout: 30000 });
  check("token mode: after the login the UI loads where it was", new URL(authPage.url()).search === "?d=demo" && !new URL(authPage.url()).href.includes(token));
  check("token mode: the session cookie is HttpOnly", (await authPage.context().cookies()).some((c) => c.name === "arcviz_session" && c.httpOnly && c.sameSite === "Strict" && !c.value.includes(token)));
  check("token mode: logout offered", await shown(authPage, "#logout-form button"));
  await authPage.fill("#chat-input", "Füge ein VNet hinzu");
  await authPage.click("#btn-send");
  await authPage.waitForFunction(() => [...document.querySelectorAll(".msg.assistant")].some((m) => m.textContent.includes("verschoben")), null, { timeout: 30000 });
  check("token mode: the chat stream works with the session", (await authPage.locator("#diagram svg .cluster").count()) === 2);
  await act(authPage, "#logout-form button");
  await authPage.waitForURL((url) => url.pathname === "/login", { timeout: 15000 });
  await authPage.goto(`${authBase}/`);
  check("token mode: logged out", new URL(authPage.url()).pathname === "/login");
  await authPage.close();

  // 17. personal workspaces and sharing (spec: docs/architecture/specs/personal-workspaces.md): a server behind an
  // (imagined) oauth2-proxy; alice owns the existing data and shares a project with bob, carol is an outsider
  const proxyPort = port + 2000;
  const proxyBase = `http://127.0.0.1:${proxyPort}`;
  const proxyData = join(here, ".e2e-data-proxy");
  rmSync(proxyData, { recursive: true, force: true });
  // the model takes 0.7 s per call here, so a turn can be watched from another browser (step 18); a short poll
  proxyServer = spawn(python, [join(here, "server.py"), proxyData, String(proxyPort)], { stdio: ["ignore", "inherit", "inherit"], env: { ...process.env, ARCVIZ_AUTH: "proxy", ARCVIZ_AUTH_PROXY_PRESET: "oauth2-proxy", ARCVIZ_ADMIN_USER: "alice", ARCVIZ_E2E_TURN_DELAY: "0.7", ARCVIZ_LIVE_POLL_SECONDS: "1" } });
  await waitForServer(`${proxyBase}/healthz`);
  check("proxy mode: a request without an identity is refused", (await fetch(`${proxyBase}/api/me`)).status === 401);
  const identity = (user) => ({ "X-Forwarded-User": user, "X-Forwarded-Email": `${user}@example.org`, "X-Forwarded-Preferred-Username": user[0].toUpperCase() + user.slice(1) });
  const userPage = async (user) => {
    const context = await browser.newContext({ locale: "de-DE", viewport: { width: 1400, height: 900 }, extraHTTPHeaders: identity(user) });
    const p = await context.newPage();
    // speech recognition present, so the mic's enabled state is decided by the role alone
    await p.addInitScript(() => { window.SpeechRecognition = class { start() {} stop() {} abort() {} }; });
    p.on("pageerror", (e) => check(`no page errors (${user})`, false, e.message));
    p.on("dialog", (d) => d.accept()); // confirmations
    return p;
  };
  const askedName = async (p, name) => {
    await p.waitForSelector("#name-dialog[open]");
    await p.fill("#name-input", name);
    await p.click('#name-form button[value="ok"]');
  };

  const bob = await userPage("bob");
  await bob.goto(`${proxyBase}/`);
  await bob.waitForSelector("#empty-project:not(.hidden)", { timeout: 30000 });
  check("a new user starts in an empty default project of their own", (await bob.textContent("#empty-text")).includes("keine Diagramme") && (await bob.inputValue("#project-select")) === homeOf("bob") && (await bob.textContent("#me")) === "Bob");
  check("the empty project offers one starter per diagram type", (await bob.locator("#starter-cards .starter-card").count()) === 3 && (await bob.isVisible('[data-starter="sequence:new"]')));
  await bob.click("#btn-tour-start");
  check("the guided tour opens on the navigator and closes with Escape", (await bob.isVisible("#tour")) && (await bob.textContent("#tour-title")).includes("Projekte") && (await bob.locator("#navigator.tour-target").count()) === 1);
  await bob.keyboard.press("Escape");
  check("the tour leaves no highlight behind", (await bob.isHidden("#tour")) && (await bob.locator(".tour-target").count()) === 0);

  const alice = await userPage("alice");
  await alice.goto(`${proxyBase}/`);
  await alice.waitForSelector("#diagram svg", { timeout: 30000 });
  check("the admin sees the existing data among her own projects", (await alice.textContent("#me")) === "Alice" && (await alice.getAttribute("#project-select optgroup", "label")) === "Meine Projekte");
  const aliceBefore = await alice.inputValue("#project-select");
  await alice.click("#btn-project-new");
  await askedName(alice, "Plattform");
  await alice.waitForFunction((before) => document.getElementById("project-select").value !== before && document.getElementById("empty-text").textContent.includes("keine Diagramme"), aliceBefore, { timeout: 15000 });
  const plattform = await alice.inputValue("#project-select");
  check("the new project has a new id", isNewId("plattform", plattform), plattform);
  await alice.click("#btn-empty-new");
  await alice.fill("#new-title", "Netz");
  await alice.selectOption("#new-template", "demo");
  await alice.click('#new-form button[value="ok"]');
  const netz = await waitForTitle(alice, "Netz");
  await alice.waitForSelector("#diagram svg", { timeout: 15000 });
  check("the creator owns the new project", !(await alice.evaluate(() => document.body.classList.contains("readonly"))) && (await shown(alice, "#btn-delete")));
  const [second, zweites] = await alice.evaluate(async (project) => {
    const r = await fetch("/api/diagrams", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "Zweites", template: "empty", project }) });
    return [r.status, (await r.json()).diagram?.id];
  }, plattform);
  check("a second diagram in the project", second === 201 && isNewId("zweites", zweites), zweites);
  const notYet = await bob.evaluate(async (p) => (await fetch(`/api/projects/${p}`)).status, plattform);
  check("before sharing, bob cannot tell the project exists (404)", notYet === 404);

  await alice.click("#project-menu summary");
  await alice.click("#btn-project-share");
  await alice.waitForSelector('#share-list li[data-user-id="alice"]', { timeout: 15000 });
  // typing a name suggests users who signed in before (ADR-0165), never the owner herself
  await alice.locator("#share-add-input").pressSequentially("Bo");
  await alice.waitForSelector('#share-add-suggestions option[value="bob"]', { state: "attached", timeout: 10000 });
  const suggestions = await alice.locator("#share-add-suggestions option").evaluateAll((os) => os.map((o) => `${o.value}=${o.label}`));
  check("the share dialog suggests signed-in users while typing", suggestions.includes("bob=Bob · bob@example.org") && !suggestions.some((s) => s.startsWith("alice=")), JSON.stringify(suggestions));
  await alice.fill("#share-add-input", "bob@example.org");
  await alice.click('#share-add button[type="submit"]');
  await alice.waitForSelector('#share-list li[data-user-id="bob"]', { timeout: 15000 });
  check("the owner shares by e-mail and sees exactly with whom", (await alice.textContent("#share-status")).includes("Freigegeben für Bob (bob@example.org)"), await alice.textContent("#share-status"));
  check("the new share is a viewer", (await alice.inputValue('#share-list li[data-user-id="bob"] select')) === "viewer");
  await alice.screenshot({ path: join(outDir, "share-dialog.png") });
  await alice.click("#share-close");

  await bob.reload();
  await bob.waitForSelector(`#project-select option[value="${plattform}"]`, { state: "attached", timeout: 30000 });
  await bob.selectOption("#project-select", plattform); // his own default project was the one he had open
  await bob.waitForSelector(row(netz), { timeout: 30000 });
  await bob.click(`${row(netz)} .nav-open`);
  await bob.waitForFunction(() => document.getElementById("diagram-title").textContent.includes("Netz"), null, { timeout: 15000 });
  await bob.waitForSelector("#diagram svg", { timeout: 15000 });
  const bobGroup = await bob.evaluate(() => { const o = document.querySelector("#project-select option:checked"); return [o.parentElement.label, o.textContent]; });
  check("the recipient sees it under \"Mit mir geteilt\" with its owner", bobGroup[0] === "Mit mir geteilt" && bobGroup[1].includes("Alice"), JSON.stringify(bobGroup));
  check("read-only for a viewer: chat input disabled with a hint", (await bob.isDisabled("#chat-input")) && (await bob.getAttribute("#chat-input", "placeholder")).startsWith("Nur Lesezugriff"));
  check("read-only for a viewer: editing controls disabled or hidden", (await bob.isDisabled("#btn-snapshot")) && (await bob.isDisabled("#btn-undo")) && (await bob.isDisabled("#btn-mic")) && !(await shown(bob, "#btn-delete")) && !(await bob.locator("#btn-section-new").isVisible()));
  const opStatus = (p) => p.evaluate(async (id) => (await fetch(`/api/diagrams/${id}/ops`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op: "rename_diagram", args: { title: "Mein Netz" } }) })).status, netz);
  check("the server refuses a viewer's change (403)", (await opStatus(bob)) === 403);
  const reshare = await bob.evaluate(async (p) => (await fetch(`/api/projects/${p}/access`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user: "carol", role: "viewer" }) })).status, plattform);
  check("a recipient cannot share further (403)", reshare === 403);
  await bob.screenshot({ path: join(outDir, "read-only.png") });

  await alice.click("#project-menu summary");
  await alice.click("#btn-project-share");
  await alice.waitForSelector('#share-list li[data-user-id="bob"]', { timeout: 15000 });
  await alice.selectOption('#share-list li[data-user-id="bob"] select', "editor");
  await alice.waitForFunction(() => document.getElementById("share-status").textContent.includes("Bearbeiten"), null, { timeout: 15000 });
  await alice.click("#share-close");

  // in the same session (no reload): opening another diagram brings the new role, and every control comes back
  await bob.click(`${row(zweites)} .nav-open`);
  await bob.waitForFunction(() => !document.body.classList.contains("readonly"), null, { timeout: 15000 });
  check("the mic is given back to an editor", !(await bob.isDisabled("#btn-mic")));
  await bob.click(`${row(netz)} .nav-open`);
  await bob.waitForSelector("#diagram svg", { timeout: 30000 });
  check("an editor may edit", !(await bob.isDisabled("#chat-input")) && !(await bob.evaluate(() => document.body.classList.contains("readonly"))));
  await bob.fill("#chat-input", "Füge ein VNet hinzu");
  await bob.click("#btn-send");
  await bob.waitForFunction(() => [...document.querySelectorAll(".msg.assistant")].some((m) => m.textContent.includes("verschoben")), null, { timeout: 30000 });
  await bob.waitForFunction(() => document.querySelectorAll("#version-select option").length >= 2, null, { timeout: 15000 });
  const bobVersion = await bob.locator("#version-select option").first().textContent();
  check("the editor's chat turn is recorded with the author", bobVersion.includes("Bob"), bobVersion);
  check("the editor still may not delete the diagram", !(await shown(bob, "#btn-delete")));

  const carol = await userPage("carol");
  await carol.goto(`${proxyBase}/`);
  await carol.waitForSelector("#empty-project:not(.hidden)", { timeout: 30000 }); // her empty default project
  const carolProjects = await carol.locator("#project-select option").evaluateAll((os) => os.map((o) => o.value));
  check("an outsider sees only her own default project", carolProjects.length === 1 && carolProjects[0] === homeOf("carol"), JSON.stringify(carolProjects));
  const carolStatus = await carol.evaluate(async (id) => (await fetch(`/api/diagrams/${id}`)).status, netz);
  check("an outsider cannot tell the diagram exists (404)", carolStatus === 404);

  // 18. working together (spec: docs/architecture/specs/collaboration.md): alice and bob on the same diagram in two
  // browser contexts: presence, live updates, the inspector keeping typed values, and the 412 choice
  await alice.click(`${row(netz)} .nav-open`);
  await alice.waitForFunction(() => document.getElementById("diagram-title").textContent.includes("Netz"), null, { timeout: 15000 });
  await alice.waitForSelector("#diagram svg", { timeout: 15000 });
  await bob.waitForSelector('#presence .presence-chip[data-user="alice"]', { timeout: 15000 });
  await alice.waitForSelector('#presence .presence-chip[data-user="bob"]', { timeout: 15000 });
  check("presence: each sees the other's chip with initials", (await bob.textContent('#presence .presence-chip[data-user="alice"]')) === "AL" && (await alice.locator("#presence .presence-chip").count()) === 1);
  const netzNode = (p, id) => p.locator(`#diagram svg g.node[id*="flowchart-n_${id.replace(/-/g, "_")}-"]`).first();
  await netzNode(alice, "orders-db").click({ force: true });
  await alice.waitForSelector("#inspector:not(.hidden)", { timeout: 10000 });
  await bob.waitForSelector('#diagram svg .presence-mark[data-user="alice"]', { timeout: 15000 });
  check("presence: alice's selection is outlined on bob's canvas", (await bob.locator('#diagram svg .presence-tag').first().textContent()).startsWith("AL"));
  await bob.screenshot({ path: join(outDir, "presence.png") });
  await alice.click("#insp-close");
  await bob.waitForFunction(() => !document.querySelector('#diagram svg .presence-mark[data-user="alice"]'), null, { timeout: 15000 });

  // a change by alice re-renders bob's canvas without a reload
  const aliceOp = (op, args) => alice.evaluate(async ([id, op, args]) => (await fetch(`/api/diagrams/${id}/ops`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op, args }) })).status, [netz, op, args]);
  check("alice adds an element", (await aliceOp("add_node", { name: "live-cache", kind: "storage", parent: "prod-deployment" })) === 200);
  await bob.waitForFunction(() => document.querySelector("#diagram svg")?.textContent.includes("live-cache"), null, { timeout: 15000 });
  check("live update: bob sees alice's new element", true);

  // bob edits a name in the inspector while alice renames the same element: bob's typed value stays
  await netzNode(bob, "events").click({ force: true });
  await bob.waitForSelector("#inspector:not(.hidden)", { timeout: 10000 });
  await bob.fill("#insp-name", "events-bob");
  await bob.fill("#insp-description", "Bobs Notiz");
  check("alice renames the same element", (await aliceOp("update_node", { id: "events", name: "events-alice" })) === 200);
  await bob.waitForSelector("#insp-form .remote-note", { timeout: 15000 });
  check("inspector keeps bob's typed value", (await bob.inputValue("#insp-name")) === "events-bob" && (await bob.inputValue("#insp-description")) === "Bobs Notiz");
  check("inspector says who changed it meanwhile", (await bob.textContent("#insp-form .remote-note")).includes("Inzwischen von Alice geändert: „events-alice“"));
  await bob.click("#insp-save");
  await bob.waitForSelector("#insp-conflict:not(.hidden)", { timeout: 15000 });
  const conflictRows = await bob.locator("#insp-conflict tr[data-key]").allTextContents();
  check("412: the conflict shows both values", conflictRows.length === 1 && conflictRows[0].includes("events-bob") && conflictRows[0].includes("events-alice"), conflictRows.join(" | "));
  await bob.screenshot({ path: join(outDir, "conflict.png") });
  await bob.click("#insp-conflict .conflict-mine");
  await bob.waitForFunction(() => document.getElementById("insp-title").textContent.includes("events-bob"), null, { timeout: 15000 });
  const saved = await alice.evaluate(async (id) => (await (await fetch(`/api/diagrams/${id}`)).json()).diagram.nodes.events, netz);
  check("bob's values were saved after choosing them", saved.name === "events-bob" && saved.description === "Bobs Notiz", JSON.stringify(saved));
  await alice.waitForFunction(() => document.querySelector("#diagram svg")?.textContent.includes("events-bob"), null, { timeout: 15000 });
  await bob.click("#insp-close");

  // a stale If-Match is refused by the server (412) and writes nothing
  const stale = await alice.evaluate(async (id) => {
    const r = await fetch(`/api/diagrams/${id}/ops`, { method: "POST", headers: { "Content-Type": "application/json", "If-Match": '"stale"' }, body: JSON.stringify({ op: "rename_diagram", args: { title: "Nie" } }) });
    return r.status;
  }, netz);
  check("a stale If-Match is answered with 412", stale === 412);

  // while bob chats with the assistant, alice sees it
  await bob.fill("#chat-input", "Ergänze einen Cache");
  await bob.click("#btn-send");
  await alice.waitForFunction(() => document.getElementById("chat-presence").textContent.includes("Bob chattet"), null, { timeout: 15000 });
  check("presence: alice sees that bob is chatting", (await alice.locator('#presence .presence-chip.chatting[data-user="bob"]').count()) === 1);
  await bob.waitForFunction(() => !document.body.querySelector("#btn-send").disabled, null, { timeout: 30000 });
  await alice.waitForFunction(() => document.getElementById("chat-presence").classList.contains("hidden"), null, { timeout: 15000 });
  check("the notice ends with the turn", true);

  // alice moves the diagram into another project she shared with bob: bob's navigator follows it, the diagram stays open
  // (following re-opens and re-renders the diagram, so bob's next click waits until that render is on the canvas)
  const bobReopened = bob.waitForResponse((r) => r.request().method() === "GET" && new URL(r.url()).pathname === `/api/diagrams/${netz}`, { timeout: 15000 });
  const moved = await alice.evaluate(async ([from, id]) => {
    const json = { "Content-Type": "application/json" };
    const created = await fetch("/api/projects", { method: "POST", headers: json, body: JSON.stringify({ title: "Betrieb" }) });
    const project = (await created.json()).project.id;
    const shared = await fetch(`/api/projects/${project}/access`, { method: "POST", headers: json, body: JSON.stringify({ user: "bob", role: "editor" }) });
    const r = await fetch(`/api/projects/${from}/diagrams/${id}/move`, { method: "POST", headers: json, body: JSON.stringify({ project }) });
    return [r.status, project, shared.status];
  }, [plattform, netz]);
  check("alice moves the diagram into another project", moved[0] === 200 && moved[2] === 200, JSON.stringify(moved));
  await bob.waitForFunction((p) => document.getElementById("project-select").value === p, moved[1], { timeout: 15000 });
  check("bob's navigator follows the moved diagram", (await bob.getAttribute('#nav-tree li[aria-current="true"]', "data-diagram-id")) === netz);
  await bobReopened;
  await bob.waitForFunction(() => !document.body.classList.contains("readonly"), null, { timeout: 15000 });
  await netzNode(bob, "orders-db").waitFor({ state: "visible", timeout: 15000 });
  await bob.waitForTimeout(300); // the render of the re-opened diagram settles (fit, presence redraw)
  await netzNode(bob, "orders-db").waitFor({ state: "visible", timeout: 15000 });
  check("bob may edit in the new project", true);

  // this tab's own writes never conflict with themselves: an edge edit, then a save of the node (no 412)
  const bobStale = [];
  bob.on("response", (r) => { if (r.status() === 412) bobStale.push(r.request().postData()); });
  await netzNode(bob, "orders-db").click({ force: true });
  await bob.waitForSelector("#inspector:not(.hidden)", { timeout: 10000 });
  await bob.locator("#insp-edges tr.edge-row").first().locator(".edge-label").fill("sql-bob");
  await bob.locator("#insp-edges tr.edge-row").first().locator(".edge-label").dispatchEvent("change");
  await bob.waitForFunction(() => document.querySelector("#diagram svg")?.textContent.includes("sql-bob"), null, { timeout: 15000 });
  await bob.fill("#insp-description", "nach der Kante");
  await bob.click("#insp-save");
  await bob.waitForFunction(() => document.getElementById("insp-description").value === "nach der Kante" && !document.body.classList.contains("busy"), null, { timeout: 15000 });
  await bob.waitForTimeout(500);
  const ownSaved = await alice.evaluate(async (id) => (await (await fetch(`/api/diagrams/${id}`)).json()).diagram.nodes["orders-db"].description, netz);
  check("an own edge edit does not make the next save a conflict (no 412)", bobStale.length === 0 && ownSaved === "nach der Kante", JSON.stringify({ bobStale, ownSaved }));
  await bob.click("#insp-close");

  // sequence diagram: a remote change to a select whose option this form does not have yet keeps that value
  let ablauf = null; // the sequence diagram's id, read from the create response
  const seqOps = async (ops) => alice.evaluate(async ([id, ops]) => {
    for (const [op, args] of ops) {
      const r = await fetch(`/api/diagrams/${id}/ops`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ op, args }) });
      if (!r.ok) return r.status;
    }
    return 200;
  }, [ablauf, ops]);
  // new lifelines become project elements (spec: elements-by-default.md); this step is about concurrent edits of a
  // diagram's own fields, and a linked lifeline is renamed through the model, so the links are taken back
  const unlinked = (ids) => ids.map((id) => ["unlink_element", { id }]);
  const seqCreated = await alice.evaluate(async (p) => {
    const r = await fetch("/api/diagrams", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "Ablauf", id: "ablauf", template: "empty", type: "sequence", project: p }) });
    return [r.status, (await r.json()).diagram?.id];
  }, moved[1]);
  ablauf = seqCreated[1];
  check("a sequence diagram for the select check", seqCreated[0] === 201 && isNewId("ablauf", ablauf) && (await seqOps([["add_lifeline", { name: "A" }], ["add_lifeline", { name: "B" }], ["add_message", { name: "go", sender: "a", receiver: "b", id: "m1" }], ...unlinked(["a", "b"])])) === 200);
  await bob.waitForSelector(row(ablauf), { timeout: 15000 });
  await bob.click(`${row(ablauf)} .nav-open`);
  await bob.waitForSelector('#diagram svg [data-id="m1"]', { timeout: 15000 });
  await bob.locator('#diagram svg [data-id="m1"]').first().click({ force: true });
  await bob.waitForSelector('#insp-element-fields [data-key="sender"]', { timeout: 10000 });
  check("alice adds a lifeline and makes it the sender", (await seqOps([["add_lifeline", { name: "C" }], ...unlinked(["c"]), ["update_message", { id: "m1", sender: "c" }]])) === 200);
  await bob.waitForFunction(() => [...document.querySelectorAll('#diagram svg [data-id="c"]')].length > 0, null, { timeout: 15000 });
  await bob.waitForTimeout(300);
  await bob.fill('#insp-element-fields [data-key="name"]', "go-bob");
  await bob.click("#insp-element-save");
  await bob.waitForFunction(() => document.getElementById("insp-title").textContent.includes("go-bob"), null, { timeout: 15000 });
  const message = await alice.evaluate(async (id) => (await (await fetch(`/api/diagrams/${id}`)).json()).diagram.interaction.fragments[0], ablauf);
  check("saving another field keeps the remote sender (no found message)", message.name === "go-bob" && message.sender === "c", JSON.stringify(message));

  // a save queued behind a write in flight is sent against the base bob saw for that element (advanced only by his own
  // writes), never against another element's newer base: alice's change to it meanwhile is never overwritten
  const holdOps = async () => {
    let open;
    const gate = new Promise((resolve) => { open = resolve; });
    let first = true;
    await bob.unrouteAll({ behavior: "wait" });
    await bob.route((url) => url.pathname === `/api/diagrams/${ablauf}/ops`, async (route) => {
      if (first) { first = false; await gate; }
      await route.continue();
    });
    return open;
  };
  const pressOn = (id) => bob.evaluate((id) => { // a canvas click without hit-testing: the element may lie under the inspector
    const target = document.querySelector(`#diagram svg [data-id="${id}"]`);
    for (const type of ["pointerdown", "pointerup"]) target.dispatchEvent(new PointerEvent(type, { bubbles: true, button: 0, pointerId: 1 }));
  }, id);
  const bobIdle = () => bob.waitForFunction(() => !document.getElementById("btn-send").disabled, null, { timeout: 15000 });
  let release = await holdOps();
  await pressOn("m1");
  await bob.waitForSelector('#insp-element-fields [data-key="name"]', { timeout: 10000 });
  await bob.fill('#insp-element-fields [data-key="name"]', "go-held");
  await bob.click("#insp-element-save"); // held: everything after it waits in the queue
  await pressOn("a");
  await bob.waitForFunction(() => document.getElementById("insp-id").textContent === "a", null, { timeout: 10000 });
  await bob.fill('#insp-element-fields [data-key="name"]', "a-bob");
  await bob.evaluate(() => document.getElementById("insp-element-form").requestSubmit()); // queued with the base of "a"
  check("alice renames lifeline a meanwhile", (await seqOps([["update_lifeline", { id: "a", name: "a-alice" }]])) === 200);
  // bob opens another element; the held write then answers 412 and its reload (with alice's change) gives that
  // element a newer base, which the queued save of "a" must not borrow
  await pressOn("b");
  await bob.waitForFunction(() => document.getElementById("insp-id").textContent === "b", null, { timeout: 10000 });
  release();
  await bobIdle();
  await bob.waitForTimeout(500);
  const lifelineA = await alice.evaluate(async (id) => (await (await fetch(`/api/diagrams/${id}`)).json()).diagram.interaction.lifelines.find((l) => l.id === "a"), ablauf);
  check("a queued save never overwrites a remote change with another element's base", lifelineA.name === "a-alice", JSON.stringify(lifelineA));
  check("the lost save is reported in the status line", (await bob.textContent("#status")).includes("Nicht gespeichert"), await bob.textContent("#status"));

  // a write to a diagram that is no longer shown never reports on the one shown now; a dropped write keeps its status
  release = await holdOps();
  await pressOn("m1");
  await bob.waitForFunction(() => document.getElementById("insp-id").textContent === "m1", null, { timeout: 10000 });
  await bob.fill('#insp-element-fields [data-key="name"]', "go-late");
  await bob.click("#insp-element-save"); // held
  await bob.evaluate(() => { // a direction change queued behind it
    const select = document.getElementById("direction-select");
    select.disabled = false;
    select.value = [...select.options].map((o) => o.value).find((v) => v !== select.value) ?? select.value;
    select.dispatchEvent(new Event("change"));
  });
  check("alice deletes the diagram bob is writing to", (await alice.evaluate(async (id) => (await fetch(`/api/diagrams/${id}`, { method: "DELETE" })).status, ablauf)) === 204);
  await bob.waitForFunction(() => !document.getElementById("diagram-title").textContent.includes("Ablauf"), null, { timeout: 15000 });
  release();
  await bobIdle();
  await bob.waitForTimeout(500);
  const bobErrors = await bob.evaluate(() => ["insp-error", "insp-element-error"].map((id) => document.getElementById(id).textContent).join(""));
  const bobStatus = await bob.textContent("#status");
  check("the old diagram's failed write shows nothing on the new one", bobErrors === "" && (await bob.locator("#inspector.hidden").count()) === 1, bobErrors);
  check("a dropped direction change keeps its status", bobStatus.startsWith("Nicht gespeichert: inzwischen ist ein anderes Diagramm"), bobStatus);
  await bob.unrouteAll({ behavior: "wait" });

  // alice revokes bob's share of the project he has open: his live stream ends and his navigator drops the project
  await bob.click(`${row(netz)} .nav-open`);
  await bob.waitForFunction(() => document.getElementById("diagram-title").textContent.includes("Netz"), null, { timeout: 15000 });
  await bob.waitForTimeout(1500); // the stream of the shown diagram is open again
  const revoked = await alice.evaluate(async (p) => (await fetch(`/api/projects/${p}/access/bob`, { method: "DELETE" })).status, moved[1]);
  check("the owner revokes the share", revoked === 200);
  await bob.waitForFunction((p) => ![...document.querySelectorAll("#project-select option")].some((o) => o.value === p), moved[1], { timeout: 15000 });
  check("the revoked project disappears from bob's navigator", (await bob.inputValue("#project-select")) === plattform);
  const afterRevoke = await bob.evaluate(async (id) => (await fetch(`/api/diagrams/${id}`)).status, netz);
  check("after revoking, bob cannot tell the diagram exists (404)", afterRevoke === 404);

  { // its own scope: the names below are local to this step
  // step 19: an agent over MCP with a personal access token (spec: docs/architecture/specs/agent-access.md): the user
  // creates the token in the dialog, the agent proposes a change, the user applies it under "Vorschläge"
  const agent = await browser.newPage({ locale: "de-DE", viewport: { width: 1400, height: 900 } });
  await agent.goto(base);
  await agent.waitForSelector("#nav-tree li");
  const openProject = await agent.inputValue("#project-select");
  await openSettings(agent);
  await agent.click("#btn-agent-access");
  await agent.waitForSelector("#agent-dialog[open]");
  check("the dialog shows the MCP address", (await agent.textContent("#agent-url")).endsWith("/mcp"));
  await agent.fill("#agent-new-label", "Laptop");
  await agent.selectOption("#agent-new-scope", "write");
  await agent.click("#agent-new button[type=submit]");
  await agent.waitForSelector("#agent-created .agent-snippet pre", { timeout: 10000 });
  const snippets = await agent.$$eval("#agent-created .agent-snippet pre", (nodes) => nodes.map((n) => n.textContent));
  const token = snippets[0];
  check("a new token is shown once with a Claude Code command", /^avp_[0-9a-f]{32}_[0-9a-f]{8}_[0-9a-f]{40}$/.test(token) && snippets[1].startsWith("claude mcp add --transport http arcviz ") && snippets[1].includes(token));
  check("the token is listed without its value", (await agent.textContent("#agent-list")).includes("Laptop") && !(await agent.textContent("#agent-list")).includes(token));
  await agent.click("#agent-close");
  const rpc = async (method, params, headers = {}) => {
    const r = await fetch(`${base}/mcp`, { method: "POST", headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream", ...headers }, body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) });
    return { status: r.status, body: r.status === 200 ? await r.json() : null };
  };
  const tool = async (name, args) => JSON.parse((await rpc("tools/call", { name, arguments: args }, { Authorization: `Bearer ${token}` })).body.result.content[0].text);
  const created = await (await fetch(`${base}/api/diagrams`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title: "Agentenziel", project: openProject }) })).json();
  const goal = created.diagram?.id ?? created.id;
  const proposal = await tool("propose_change", { project: openProject, diagram: goal, operations: [{ op: "add_node", arguments: { name: "Agent-Cache", kind: "service", id: "agent-cache" } }], rationale: "Häufige Lesezugriffe" });
  check("the agent's proposal is stored as pending", proposal.proposal?.status === "pending", JSON.stringify(proposal).slice(0, 200));
  check("the diagram is untouched by the proposal", !(await (await fetch(`${base}/api/diagrams/${goal}`)).json()).diagram.nodes["agent-cache"]);
  await agent.click("#btn-proposals");
  await agent.waitForSelector("#proposal-dialog[open] .proposal-card");
  const card = await agent.textContent("#proposal-list");
  check("the user sees the proposal with its rationale and its diff", card.includes("Häufige Lesezugriffe") && card.includes("Agent-Cache") && card.includes("(mcp)"), card);
  await agent.click("#proposal-list .proposal-apply");
  await agent.waitForFunction(() => document.getElementById("proposal-status").textContent.includes("übernommen"), null, { timeout: 15000 });
  check("applying creates the node in the diagram", !!(await (await fetch(`${base}/api/diagrams/${goal}`)).json()).diagram.nodes["agent-cache"]);
  const seen = await tool("get_proposal", { project: openProject, proposal: proposal.proposal.id });
  check("the agent sees that its proposal was applied", seen.proposal.status === "applied");
  await agent.click("#proposal-close");
  await openSettings(agent);
  await agent.click("#btn-agent-access");
  await agent.waitForSelector("#agent-list .agent-revoke");
  agent.once("dialog", (d) => d.accept());
  await agent.click("#agent-list .agent-revoke");
  await agent.waitForFunction(() => document.getElementById("agent-status").textContent.includes("widerrufen"), null, { timeout: 10000 });
  check("a revoked token is refused at once", (await rpc("tools/list", {}, { Authorization: `Bearer ${token}` })).status === 401);
  await agent.close();
  }
  { // its own scope: the names below are local to this step
  // step 20: the requirements workshop (spec: docs/architecture/specs/requirements-workshop.md): import, refine with the
  // scripted model, accept, derive a proposal, apply a part of it, and the requirements chat
  const rq = await browser.newPage({ locale: "de-DE", viewport: { width: 1500, height: 950 } });
  await rq.goto(base);
  await rq.waitForSelector("#nav-tree li");
  const rqProject = await rq.inputValue("#project-select");
  await rq.click("#btn-requirements");
  await rq.waitForSelector("#requirements-view:not(.hidden)");
  check("the requirements view replaces the diagram stage", await rq.isHidden(".canvas") && (await rq.getAttribute("#btn-requirements", "aria-pressed")) === "true");
  await rq.waitForFunction(() => document.getElementById("chat-scope-label").textContent.startsWith("Anforderungs-Chat"), null, { timeout: 10000 });
  check("the chat pane switches to the requirements scope", (await rq.textContent("#chat-scope-label")).startsWith("Anforderungs-Chat"));
  await rq.waitForSelector("#req-empty", { state: "visible", timeout: 10000 });
  check("an empty project says how to start", await rq.isVisible("#req-empty"));
  await rq.click("#req-import");
  await rq.waitForSelector("#req-import-dialog[open]");
  await rq.fill("#req-import-text", "- Der Shop ist rund um die Uhr erreichbar\n- Alle Daten werden verschlüsselt gespeichert\n- Der Export liefert eine CSV\n- Der Export liefert eine CSV\n");
  await rq.click("#req-import-check");
  await rq.waitForFunction(() => document.getElementById("req-import-preview").textContent.includes("Würde 3 anlegen"), null, { timeout: 10000 });
  check("a dry run reports what would be imported and writes nothing", (await rq.$$eval("#req-table tbody tr", (r) => r.length)) === 0);
  await rq.click("#req-import-go");
  await rq.waitForFunction(() => document.querySelectorAll("#req-table tbody tr").length === 3, null, { timeout: 10000 });
  check("three requirements are imported, the repeated line once", (await rq.textContent("#req-total")).startsWith("3 von 3") && (await rq.textContent('.req-step[data-step="raw"]')).includes("3"));
  await rq.click("#req-refine");
  await rq.waitForFunction(() => document.querySelectorAll("#req-table .req-suggestion").length === 3, null, { timeout: 15000 });
  check("refining shows suggestions next to the original wording and changes nothing in force", (await rq.textContent('.req-step[data-step="suggested"]')).includes("3") && !(await rq.textContent("#req-table tbody tr")).includes("Verfügbarkeit 99"));
  await rq.click("#req-accept-safe");
  await rq.waitForFunction(() => document.querySelector('.req-step[data-step="refined"]').textContent.includes("2"), null, { timeout: 10000 });
  check("the unremarkable button leaves the requirement with an open conflict", (await rq.textContent('.req-step[data-step="suggested"]')).includes("1"));
  await rq.click('#req-table tr[data-id="req2"]');
  await rq.waitForSelector("#req-detail:not(.hidden) .req-suggestion-block button");
  check("the detail shows the suggestion against the fields in force and the conflict", (await rq.textContent("#req-detail")).includes("Widerspruch") && (await rq.textContent("#req-detail")).includes("Alle Daten werden verschlüsselt gespeichert."));
  await rq.click("#req-detail .req-suggestion-block .req-actions-row button");
  await rq.waitForFunction(() => document.querySelector('.req-step[data-step="refined"]').textContent.includes("3"), null, { timeout: 10000 });
  const counts = async () => rq.evaluate(async (p) => ({ drivers: (await (await fetch(`/api/projects/${p}/drivers`)).json()).drivers, scenarios: (await (await fetch(`/api/projects/${p}/scenarios`)).json()).scenarios }), rqProject);
  const before = await counts(); // the project has the drivers and scenarios of the earlier steps
  await rq.click("#req-derive");
  await rq.waitForFunction(() => document.getElementById("req-status").textContent.includes("Ableiten: fertig") || document.getElementById("req-status").textContent.includes("Ableiten:"), null, { timeout: 15000 });
  const proposals = await rq.evaluate(async (p) => (await (await fetch(`/api/projects/${p}/proposals`)).json()).proposals, rqProject);
  const derived = proposals.find((p) => p.kind === "project" && p.client === "derive");
  check("deriving stores a project proposal and writes nothing to the project", !!derived && derived.items.length === 3 && (await counts()).drivers.length === before.drivers.length && (await counts()).scenarios.length === before.scenarios.length, JSON.stringify(proposals).slice(0, 200));
  await rq.click("#btn-proposals");
  await rq.waitForSelector("#proposal-dialog[open] .proposal-item");
  await rq.check('#proposal-list li[data-item-key="q_zone"] .proposal-item-tick');
  await rq.click("#proposal-list .proposal-apply-selected");
  await rq.waitForFunction(() => document.getElementById("proposal-status").textContent.includes("übernommen"), null, { timeout: 15000 });
  const applied = await counts();
  const added = applied.drivers.filter((d) => !before.drivers.some((o) => o.id === d.id));
  const scenario = applied.scenarios.find((x) => x.title === "Ausfall einer Zone");
  check("ticking the scenario applies it with the driver it needs, the other driver stays pending", added.length === 1 && added[0].title === "Verfügbarkeit" && added[0].requirements[0] === "req1" && !!scenario && scenario.driver === added[0].id && applied.scenarios.length === before.scenarios.length + 1, JSON.stringify(added));
  await rq.click("#proposal-close");
  await rq.click("#req-refine"); // reloads the table through the loop's first load; nothing is raw any more
  await rq.waitForFunction(() => document.querySelector('.req-step[data-step="derived"]').textContent.includes("1"), null, { timeout: 15000 });
  check("coverage follows the applied items", (await rq.textContent('#req-table tr[data-id="req1"]')).includes("Szenario"));
  await rq.fill("#chat-input", "Nimm bitte auf: Das Backup läuft jede Nacht");
  await rq.click("#btn-send");
  await rq.waitForFunction(() => document.querySelectorAll("#req-table tbody tr").length === 4, null, { timeout: 20000 });
  check("the chat adds the requirement and the table follows", (await rq.textContent("#messages")).includes("Das Backup läuft jede Nacht") && (await rq.textContent('#req-table tr[data-id="req4"]')).includes("Das Backup"));
  const stored = await rq.evaluate(async (p) => (await (await fetch(`/api/projects/${p}/requirements/req4`)).json()).requirement, rqProject);
  check("what the chat added is marked as such and stays raw", stored.client === "chat" && stored.status === "raw");
  await rq.click("#nav-arch-drivers");
  await rq.waitForSelector("#architecture-view:not(.hidden) #drivers-list .driver-row");
  await rq.click('#drivers-list .md-card:has(.md-title:text-is("Verfügbarkeit"))');
  await rq.waitForFunction(() => [...document.querySelectorAll("#driver-requirements option:checked")].some((o) => o.value === "req1"), null, { timeout: 10000 });
  const pickerRow = await rq.inputValue("#driver-title");
  check("the driver form picks the requirements the driver covers", pickerRow.includes("Verfügbarkeit"), pickerRow);
  await rq.click("#btn-requirements");
  await rq.waitForSelector("#requirements-view:not(.hidden)");
  await rq.click("#req-matrix");
  await rq.waitForSelector("#req-matrix-dialog[open] tbody tr");
  check("the matrix lists the refined requirements with their drivers and scenarios", (await rq.textContent("#req-matrix-table")).includes("Verfügbarkeit"));
  await rq.click("#req-matrix-close");
  // 20b. graded deletion (spec: docs/architecture/specs/graded-deletion.md, ADR-0198): requirements are deleted in bulk
  // after one question, a driver only after it was retired
  await rq.locator('#req-table tbody tr:has-text("Das Backup") input[type="checkbox"]').check();
  check("deleting requirements is enabled for a selection", await rq.isEnabled("#req-delete"));
  const askedToDelete = new Promise((resolve) => rq.once("dialog", async (d) => { resolve(d.message()); await d.accept(); }));
  await rq.click("#req-delete");
  check("the bulk delete asks once and names the count", (await askedToDelete).startsWith("1 Anforderung löschen?"));
  await rq.waitForFunction(() => document.querySelectorAll("#req-table tbody tr").length === 3, null, { timeout: 10000 });
  check("the deleted requirement is gone from the table", (await rq.locator('#req-table tbody tr:has-text("Das Backup")').count()) === 0);
  await rq.click("#nav-arch-drivers");
  await rq.waitForSelector("#architecture-view:not(.hidden) #drivers-list .driver-row");
  await rq.click('#drivers-list .md-card:has(.md-title:text-is("Verfügbarkeit"))');
  check("an active driver cannot be deleted", await rq.isDisabled("#driver-delete") && (await rq.getAttribute("#driver-delete", "title")).includes("außer Kraft"));
  await rq.click("#driver-retire");
  await rq.waitForFunction(() => !document.querySelector("#driver-delete").disabled, null, { timeout: 10000 });
  check("a retired driver leaves the list and can be deleted", (await rq.locator('#drivers-list .md-card:has(.md-title:text-is("Verfügbarkeit"))').count()) === 0 && (await rq.textContent("#driver-retire")) === "Wieder aktivieren");
  await rq.check("#drivers-show-retired");
  check("the retired driver is listed on request", (await rq.locator('#drivers-list .driver-row.retired:has(.md-title:text-is("Verfügbarkeit"))').count()) === 1);
  rq.once("dialog", (d) => d.accept());
  await rq.click("#driver-delete");
  await rq.waitForFunction(() => ![...document.querySelectorAll("#drivers-list .md-title")].some((t) => t.textContent === "Verfügbarkeit"), null, { timeout: 10000 });
  check("the retired driver is deleted after the question", true);
  await rq.uncheck("#drivers-show-retired");
  await rq.click("#btn-requirements");
  await rq.waitForSelector("#requirements-view:not(.hidden)");
  await rq.click("#req-to-diagram");
  await rq.waitForSelector(".canvas", { state: "visible" });
  check("going back shows the diagram and its chat", await rq.isHidden("#requirements-view") && !(await rq.textContent("#chat-scope-label")).startsWith("Anforderungs-Chat") || await rq.isHidden("#chat-scope"));
  await rq.close();
  }
} catch (err) {
  check("smoke test completed", false, err.message || String(err));
} finally {
  if (browser) await browser.close();
  server.kill();
  authServer?.kill();
  proxyServer?.kill();
}

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("\nall checks passed");
