// Entry point of the page (spec: docs/architecture/specs/frontend-modules.md). The only module with
// top-level side effects: binds the elements, initialises the features in a fixed order (it decides
// the order of listeners that share an element) and boots.
import { api } from "./api.js";
import { initI18n, t } from "./i18n.js";
import { initAppearance } from "./appearance.js";
import { initCanvas, registerIcons } from "./canvas.js";
import { initChat, sendMessage } from "./chat.js";
import { fillDirections, initDiagram } from "./diagram.js";
import { bindElements, el, placeholder } from "./dom.js";
import { initArchView } from "./archview.js";
import { initExport } from "./export.js";
import { handleDiagramClick, initInspector } from "./inspector.js";
import { initLive } from "./live.js";
import { initModelPanel } from "./modelpanel.js";
import { initNavigator, loadDiagramList } from "./navigator.js";
import { initProposalPanel } from "./proposalpanel.js";
import { initReleaseBanner } from "./releasebanner.js";
import { initRequirementView } from "./requirementview.js";
import { initReview } from "./review.js";
import { initSplitter } from "./splitter.js";
import { state } from "./state.js";
import { initTokenPanel } from "./tokenpanel.js";
import { initSettings } from "./settings.js";
import { initPalette } from "./paletteview.js";
import { initShell, setMainView } from "./shell.js";
import { initTopbar } from "./topbar.js";
import { initVersions } from "./versions.js";
import { initVoice } from "./voice.js";
import { initWelcome } from "./welcome.js";
import { initWorkspaces, setMe } from "./workspaces.js";

await initI18n();  // before everything that shows text: the language and the catalog are in place
bindElements();
initAppearance();  // first: the skin is in place before anything renders
initCanvas({ onClick: handleDiagramClick });
initSplitter();
initInspector();
initVoice();      // before the chat and diagram listeners: stop recording/speaking first, show its notice first
initDiagram();
initNavigator();
initWorkspaces();
initModelPanel();
initProposalPanel();
initRequirementView();
initTokenPanel();
initSettings();
initWelcome();
initReleaseBanner();
initChat();
initVersions();
initExport();
initReview({ onApply: (text) => sendMessage(text, { propose: true }) }); // a suggestion is always previewed
initArchView({ // an analysis option, previewed too: the chat is hidden on the page, so go back to the diagram first
  onApply: async (text) => { await setMainView("diagram"); sendMessage(text, { propose: true }); },
});
initShell();      // rail, navigator entries and hash routes (spec: app-shell.md)
initPalette();    // Strg+K and the search field of the top bar (spec: command-palette.md)
initTopbar();     // menu behaviour of the top bar (one menu open at a time)
initLive();       // last: follows the shown diagram or project (spec: collaboration.md)

(async function boot() {
  try {
    const [cfg, me] = await Promise.all([api("/api/config"), api("/api/me")]);
    setMe(me);
    state.config = cfg;
    registerIcons(cfg.icon_packs);
    fillDirections(cfg.directions);
    el.provider.textContent = `${cfg.provider}${cfg.model ? " · " + cfg.model : ""}`;
    document.getElementById("logout-form").hidden = cfg.auth?.mode !== "token";
    await loadDiagramList();
  } catch (err) {
    el.diagram.replaceChildren(placeholder(t("Start fehlgeschlagen: {message}", { message: err.message || err })));
  }
})();
