/* Application shell: runtime configuration, token entry, identity, routing.
 *
 * No framework and no build step. The three views are ES modules loaded by the
 * browser directly; this file owns the parts they share - the API client, who
 * the token belongs to, and which view is on screen.
 */

import {
  createClient,
  describeError,
  hasToken,
  readToken,
  tokenStorage,
  writeToken,
} from "./api.js";
import { clear, el, renderMessage, replace } from "./dom.js";
import { renderAudit } from "./views/audit.js";
import { renderBoard } from "./views/board.js";
import { renderTree } from "./views/tree.js";

const VIEWS = {
  tree: { section: "view-tree", root: "tree-root", render: renderTree },
  board: { section: "view-board", root: "board-root", render: renderBoard },
  audit: { section: "view-audit", root: "audit-root", render: renderAudit },
};
const DEFAULT_VIEW = "tree";

/** Shared state handed to every view. */
const session = {
  client: null,
  /** The `/me` response, or null when unauthenticated or not loaded yet. */
  me: null,
  /**
   * Whether the token's role may write at all.
   *
   * This is the only part of authorization a client can know up front. Site
   * grants - which sites the token may write to - are readable only with an
   * admin token, so views must offer role-permitted actions and let the API
   * refuse with 403 `auth.scope_forbidden` when the grant is missing.
   */
  canWrite: false,
};

function element(id) {
  return document.getElementById(id);
}

function currentViewName() {
  const name = window.location.hash.replace(/^#\/?/, "");
  return Object.hasOwn(VIEWS, name) ? name : DEFAULT_VIEW;
}

function showBanner(description) {
  const banner = element("banner");
  if (description === null) {
    banner.hidden = true;
    clear(banner);
    return;
  }
  replace(banner, renderMessage(description));
  banner.hidden = false;
}

function renderIdentity() {
  const identity = element("identity");
  if (!hasToken()) {
    identity.textContent = "No token entered. The API answers nothing but /health without one.";
    return;
  }
  if (session.me === null) {
    identity.textContent = "Token entered; identity not confirmed.";
    return;
  }
  const { display_name: displayName, username, role } = session.me;
  const writes = session.canWrite
    ? "may run workflow actions, subject to its site grants"
    : "may read only";
  identity.textContent = `Token belongs to ${displayName} (${username}), role ${role} - ${writes}.`;
}

/** Confirm who the token belongs to; failures are reported, never guessed at. */
async function loadIdentity() {
  session.me = null;
  session.canWrite = false;
  if (!hasToken()) {
    renderIdentity();
    return;
  }
  try {
    session.me = await session.client.request("/me");
    session.canWrite = session.me.role === "engineer" || session.me.role === "admin";
    showBanner(null);
  } catch (error) {
    showBanner(describeError(error));
  }
  renderIdentity();
}

function selectView(name) {
  for (const [viewName, view] of Object.entries(VIEWS)) {
    element(view.section).hidden = viewName !== name;
  }
  for (const link of document.querySelectorAll(".tabs a")) {
    if (link.dataset.view === name) {
      link.setAttribute("aria-current", "page");
    } else {
      link.removeAttribute("aria-current");
    }
  }
}

let renderToken = 0;

/** Render the active view. Late responses from a superseded render are dropped. */
async function renderCurrentView() {
  const name = currentViewName();
  const view = VIEWS[name];
  selectView(name);
  const root = element(view.root);
  renderToken += 1;
  const ticket = renderToken;
  root.setAttribute("aria-busy", "true");
  replace(root, el("p", { class: "muted", text: "Loading..." }));
  try {
    const content = await view.render({ session, rerender: renderCurrentView });
    if (ticket !== renderToken) {
      return;
    }
    replace(root, content);
  } catch (error) {
    if (ticket !== renderToken) {
      return;
    }
    replace(root, renderMessage(describeError(error)));
  } finally {
    if (ticket === renderToken) {
      root.setAttribute("aria-busy", "false");
    }
  }
}

async function applyToken(value) {
  writeToken(value);
  element("token-input").value = readToken();
  updateTokenHelp();
  await loadIdentity();
  await renderCurrentView();
}

function updateTokenHelp() {
  const where =
    tokenStorage === "memory"
      ? "This browser refused persistent storage, so the token is kept in memory and is gone when the tab closes."
      : "Kept in this browser's localStorage so a reload keeps it - which also means any script on this origin could read it. Use Forget token when you are done.";
  element("token-help").textContent = where;
}

async function loadConfig() {
  const response = await fetch("./config.json", { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`The page could not load its own configuration (${response.status}).`);
  }
  return response.json();
}

async function start() {
  let config;
  try {
    config = await loadConfig();
  } catch (error) {
    showBanner({
      level: "error",
      heading: "The frontend could not start",
      detail: String(error.message ?? error),
    });
    return;
  }

  element("app-name").textContent = config.app_name;
  element("app-version").textContent = `version ${config.app_version} - API at ${config.api_prefix}`;
  document.title = config.app_name;
  session.client = createClient(config.api_prefix);

  element("token-input").value = readToken();
  updateTokenHelp();

  element("token-form").addEventListener("submit", (event) => {
    event.preventDefault();
    void applyToken(element("token-input").value);
  });
  element("token-forget").addEventListener("click", () => {
    void applyToken("");
  });
  window.addEventListener("hashchange", () => {
    void renderCurrentView();
  });

  await loadIdentity();
  await renderCurrentView();
}

void start();
