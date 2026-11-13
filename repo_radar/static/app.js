/*
 * Repo Radar local interface.
 *
 * A hash-routed, framework-free client for the local FastAPI server. It formats what the API
 * returns and never scores, filters, or explains recommendations itself: every score and every
 * reason shown here comes from the server. Components live in ui.js.
 */
"use strict";

const COLLAPSED_RESULT_LIMIT = 6;

const ROUTES = {
  overview: { title: "Overview" },
  discover: { title: "Discover" },
  contribute: { title: "Contribute" },
  library: { title: "Library" },
  preferences: { title: "Preferences" },
  history: { title: "Feedback history" },
};

// section names from the previous single-page layout keep working as links
const ROUTE_ALIASES = {
  saved: { route: "library", tab: "saved" },
  starred: { route: "library", tab: "starred" },
  profile: { route: "preferences" },
  feedback: { route: "history" },
};

const CLASSIFICATIONS = {
  interested: {
    label: "Saved",
    tone: "positive",
    icon: "bookmark-check",
    description:
      "Kept in your Library and counted toward your profile. Clearing the record also removes the repository from Saved.",
    clearBody: "It leaves Saved, stops counting toward your profile, and can be recommended again.",
  },
  starred: {
    label: "Starred",
    tone: "neutral",
    icon: "star",
    description:
      "Starred through Repo Radar or found by a sync. Clearing the record never unstars anything on GitHub and leaves your synced library as it is.",
    clearBody:
      "Only Repo Radar's local record is cleared. The repository stays starred on GitHub and stays in your synced library.",
  },
  blocked: {
    label: "Blocked",
    tone: "danger",
    icon: "ban",
    description: "Left out of repository and contribution discovery. Clearing the record makes it eligible again.",
    clearBody: "It becomes eligible for repository and contribution discovery again.",
  },
  "not interested": {
    label: "Not for me",
    tone: "neutral",
    icon: "eye-off",
    description:
      "Dismissed from recommendations and left out of contribution discovery. Clearing the record makes it eligible again.",
    clearBody: "It becomes eligible for repository and contribution discovery again.",
  },
};
const CLASSIFICATION_ORDER = ["interested", "starred", "blocked", "not interested"];

const CONTRIBUTION_SOURCES = {
  saved: { label: "Saved", tone: "positive", icon: "bookmark-check" },
  starred: { label: "Starred", tone: "neutral", icon: "star" },
  new: { label: "New to you", tone: "accent", icon: "compass" },
};

// headings for the evidence groups the server attaches to each issue, in display order
const EVIDENCE_GROUPS = [
  ["repository", "Repository fit"],
  ["relevance", "Issue match"],
  ["friendliness", "Invites contributors"],
  ["freshness", "Freshness"],
];

const LIBRARY_SORTS = {
  saved: [
    ["signals", "Most profile signals"],
    ["name", "Name"],
    ["stars", "Most stars"],
  ],
  starred: [
    ["sync", "Sync order"],
    ["name", "Name"],
    ["stars", "Most stars"],
  ],
};

const DISCOVER_DEFAULTS = { search: "", min_stars: "0", max_stars: "", limit: "10", hidden_gems: false };
const CONTRIBUTE_DEFAULTS = { scope: "discover", labels: [], contributor_friendly: false, unassigned_only: false, limit: "10" };

const interfaceStore = browserStore("local", "repo-radar.interface.v1");
const resultStore = browserStore("session", "repo-radar.results.v1");

const state = {
  serverReachable: true,
  status: null,
  profile: null,
  preferences: null,
  interested: null,
  starred: null,
  feedback: null,
  errors: {},
  preferencesDirty: false,
  discover: { results: null, search: null, message: null, request: null, at: null, error: null, loading: false, generation: 0 },
  contribute: { results: null, warning: null, message: null, applied: null, request: null, at: null, error: null, loading: false },
  expanded: { saved: false, starred: false, history: {} },
  libraryTab: "saved",
  historyFilter: "all",
};

/* ---------- API ---------- */

/**
 * turns an error response body into one readable sentence
 * @param {object|null} data decoded error body
 * @returns {string|null} message
 */
function errorDetail(data) {
  if (!data || data.detail === undefined) return null;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((item) => item.msg || String(item)).join("; ");
  return String(data.detail);
}

/**
 * requests JSON from the local API and records whether the server answered
 * @param {string} url local API url
 * @param {RequestInit} options fetch options
 * @returns {Promise<object>} decoded response
 */
async function api(url, options = {}) {
  let response;
  try {
    response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  } catch {
    setServerReachable(false);
    throw new Error("Repo Radar's local server isn't responding. Check that python -m repo_radar web is still running.");
  }
  setServerReachable(true);
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;
  }
  if (!response.ok) throw new Error(errorDetail(data) || `The request failed with status ${response.status}`);
  return data;
}

/**
 * records server reachability and refreshes the status indicators when it changes
 * @param {boolean} reachable whether the last request reached the server
 * @returns {void} no return value
 */
function setServerReachable(reachable) {
  if (state.serverReachable === reachable) return;
  state.serverReachable = reachable;
  renderStatus();
}

/**
 * builds the request body describing one repository
 * @param {object} repository repository metadata
 * @returns {object} repository action payload
 */
function repositoryPayload(repository) {
  return {
    repository: repository.full_name,
    description: repository.description,
    language: repository.language,
    topics: repository.topics || [],
    stars: repository.stars || 0,
    url: repository.url || githubUrl(repository.full_name),
  };
}

/**
 * builds the API path segment for one repository
 * @param {string} fullName owner and name
 * @returns {string} encoded owner/name path
 */
function repositoryPath(fullName) {
  return fullName.split("/", 2).map(encodeURIComponent).join("/");
}

const RESOURCES = {
  status: ["/api/status", (data) => (state.status = data)],
  profile: ["/api/profile", (data) => (state.profile = data)],
  preferences: ["/api/preferences", (data) => (state.preferences = data)],
  interested: ["/api/interested", (data) => (state.interested = data.repositories)],
  starred: ["/api/starred", (data) => (state.starred = data.repositories)],
  feedback: ["/api/feedback", (data) => (state.feedback = data.records)],
};
const LOCAL_RESOURCES = ["status", "profile", "interested", "starred", "feedback"];

/**
 * reloads local resources and re-renders every view that reads them
 * @param {...string} names resource names; all local resources when omitted
 * @returns {Promise<void>} no return value
 */
async function refresh(...names) {
  const targets = names.length ? names : LOCAL_RESOURCES;
  await Promise.all(
    targets.map(async (name) => {
      const [url, apply] = RESOURCES[name];
      try {
        apply(await api(url));
        state.errors[name] = null;
      } catch (error) {
        state.errors[name] = error.message;
      }
    }),
  );
  renderLocalViews();
}

/* ---------- small helpers ---------- */

/**
 * builds the GitHub address of a repository
 * @param {string} fullName owner and name
 * @returns {string} repository URL
 */
function githubUrl(fullName) {
  return `https://github.com/${repositoryPath(fullName)}`;
}

/**
 * splits owner and repository name
 * @param {string} fullName owner and name
 * @returns {Array<string>} owner and name
 */
function splitName(fullName) {
  const separator = fullName.indexOf("/");
  return separator < 0 ? ["", fullName] : [fullName.slice(0, separator), fullName.slice(separator + 1)];
}

/**
 * maps repository names to their current feedback classification
 * @returns {Map<string, string>} lowercase name to classification
 */
function feedbackByRepository() {
  return new Map((state.feedback || []).map((record) => [record.repository.toLowerCase(), record.classification]));
}

/**
 * capitalizes the first letter of a server explanation for display
 * @param {string} text explanation
 * @returns {string} display text
 */
function sentenceCase(text) {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
}

/**
 * converts comma separated text into values
 * @param {string} value comma separated input
 * @returns {Array<string>} cleaned values
 */
function splitValues(value) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

/**
 * builds a linked owner/name title that opens GitHub in a new tab
 * @param {string} fullName owner and name
 * @param {string} url repository URL
 * @returns {HTMLAnchorElement} repository link
 */
function repositoryLink(fullName, url) {
  const [owner, name] = splitName(fullName);
  return h(
    "a",
    { href: url || githubUrl(fullName), target: "_blank", rel: "noopener noreferrer" },
    h("span", { class: "repo-name" }, owner ? h("span", { class: "repo-owner", text: `${owner}/` }) : null, name),
    icon("arrow-up-right"),
    h("span", { class: "visually-hidden", text: " (opens on GitHub)" }),
  );
}

/**
 * builds a metadata list of icon and text pairs
 * @param {Array<Array|null>} items [icon, text, optional title] entries; empty entries are skipped
 * @returns {HTMLUListElement} metadata list
 */
function metaList(items) {
  return h(
    "ul",
    { class: "meta" },
    items.filter(Boolean).map(([iconName, text, title]) => h("li", { title }, icon(iconName), h("span", { text }))),
  );
}

/**
 * builds the score column used by ranked results
 * @param {number} score score from zero to one
 * @returns {HTMLElement} score column
 */
function scoreColumn(score) {
  const bounded = Math.max(0, Math.min(1, Number(score) || 0));
  const percent = Math.round(bounded * 100);
  return h(
    "div",
    { class: "entry-score" },
    h(
      "p",
      { class: "score-value" },
      h("span", { "aria-hidden": "true" }, String(percent), h("small", { text: "%" })),
      h("span", { class: "visually-hidden", text: `Score ${percent} percent` }),
    ),
    h("span", { class: "score-meter", "aria-hidden": "true", style: { "--value": String(bounded) } }, h("span")),
  );
}

/**
 * writes a status line inside one result
 * @param {HTMLElement} entry result element
 * @param {string} text message
 * @param {string} tone neutral, positive, or danger
 * @returns {void} no return value
 */
function setEntryStatus(entry, text, tone = "neutral") {
  const status = entry.querySelector(".entry-status");
  if (!status) return;
  status.textContent = text;
  status.dataset.tone = tone;
}

/**
 * swaps the icon of a shared button
 * @param {HTMLElement} control button
 * @param {string} name new icon name
 * @returns {void} no return value
 */
function setButtonIcon(control, name) {
  const use = control.querySelector(".icon:not(.spinner) use");
  if (use) use.setAttribute("href", `${ICON_SPRITE}#${name}`);
}

/**
 * brings fresh results into view on phones, where the search form fills the first screen
 * @param {string} selector result container selector
 * @returns {void} no return value
 */
function revealResults(selector) {
  if (phoneLayout.matches) document.querySelector(selector)?.scrollIntoView({ block: "start" });
}

/**
 * announces a short message to screen readers without reading out a whole result list
 * @param {string} text message
 * @returns {void} no return value
 */
function announce(text) {
  const announcer = document.querySelector("#announcer");
  announcer.textContent = "";
  window.setTimeout(() => (announcer.textContent = text), 50);
}

/**
 * renders a list with the shared See more / See less control
 * @param {HTMLElement} container list container
 * @param {Array<object>} items list items
 * @param {Function} renderItem function creating one element
 * @param {boolean} expanded whether every item is shown
 * @param {Function} toggle callback flipping the expanded state
 * @param {object} list class and element name for the list
 * @returns {void} no return value
 */
function renderCollection(container, items, renderItem, expanded, toggle, { className = "ledger-list", tag = "div" } = {}) {
  const visible = expanded ? items : items.slice(0, COLLAPSED_RESULT_LIMIT);
  const children = [h(tag, { class: className }, visible.map(renderItem))];
  if (items.length > COLLAPSED_RESULT_LIMIT) {
    const control = button({
      label: expanded ? "See less" : `See more (${items.length - COLLAPSED_RESULT_LIMIT})`,
      icon: "chevron-down",
      variant: "ghost",
      size: "sm",
      attributes: { "aria-expanded": String(expanded), "data-collection-toggle": "" },
      onClick: () => {
        toggle();
        // the control is rebuilt by the re-render, so focus follows it
        container.querySelector("[data-collection-toggle]")?.focus();
      },
    });
    if (expanded) control.querySelector(".icon").style.transform = "rotate(180deg)";
    children.push(h("div", { class: "collection-toggle" }, control));
  }
  container.replaceChildren(...children);
}

/* ---------- routing and navigation ---------- */

const elements = {
  main: document.querySelector("#main"),
  sidebar: document.querySelector("#sidebar"),
  scrim: document.querySelector("#nav-scrim"),
  navToggle: document.querySelector("#nav-toggle"),
  mobileBar: document.querySelector(".mobile-bar"),
};
const phoneLayout = window.matchMedia("(max-width: 767px)");
// a control to focus after the next route change instead of the section heading
let focusAfterRoute = null;

/**
 * resolves the location hash to a route
 * @returns {{route: string, tab?: string}|null} route, or null for a hash that is not a route
 */
function resolveRoute() {
  const name = decodeURIComponent(window.location.hash.replace(/^#\/?/, "")).split(/[/?]/)[0].toLowerCase();
  if (!name) return { route: "overview" };
  if (ROUTES[name]) return { route: name };
  return ROUTE_ALIASES[name] || null;
}

/**
 * shows the routed view, marks the active navigation item, and moves focus to its heading
 * @param {boolean} moveFocus whether focus moves to the new view heading
 * @returns {void} no return value
 */
function showRoute(moveFocus) {
  const resolved = resolveRoute();
  if (!resolved) return;
  const { route, tab } = resolved;
  for (const view of document.querySelectorAll("[data-view]")) view.hidden = view.dataset.view !== route;
  for (const link of document.querySelectorAll(".nav-link")) {
    if (link.dataset.route === route) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  }
  document.title = `${ROUTES[route].title} | Repo Radar`;
  if (tab) selectLibraryTab(tab, false);
  closeNavigation(false);
  if (route === "discover") renderDiscoverResults();
  if (route === "contribute") renderContributeResults();
  if (route === "overview") renderOverview();
  if (moveFocus) {
    window.scrollTo(0, 0);
    const target = (focusAfterRoute && document.querySelector(focusAfterRoute)) || document.querySelector(`[data-view="${route}"] h1`);
    target?.focus({ preventScroll: true });
  }
  focusAfterRoute = null;
}

/**
 * opens the phone navigation drawer and keeps focus inside it
 * @returns {void} no return value
 */
function openNavigation() {
  elements.sidebar.classList.add("is-open");
  elements.scrim.hidden = false;
  elements.navToggle.setAttribute("aria-expanded", "true");
  elements.main.inert = true;
  elements.mobileBar.inert = true;
  (elements.sidebar.querySelector('[aria-current="page"]') || elements.sidebar.querySelector(".nav-link"))?.focus();
}

/**
 * closes the phone navigation drawer
 * @param {boolean} restoreFocus whether focus returns to the menu button
 * @returns {void} no return value
 */
function closeNavigation(restoreFocus = true) {
  if (!elements.sidebar.classList.contains("is-open")) return;
  elements.sidebar.classList.remove("is-open");
  elements.scrim.hidden = true;
  elements.navToggle.setAttribute("aria-expanded", "false");
  elements.main.inert = false;
  elements.mobileBar.inert = false;
  if (restoreFocus) elements.navToggle.focus();
}

/* ---------- status and navigation counts ---------- */

/**
 * summarizes the GitHub connection from local status
 * @returns {{tone: string, title: string, detail: string, when?: string}} summary
 */
function githubSummary() {
  const status = state.status;
  if (!state.serverReachable) {
    return { tone: "danger", title: "Local server unreachable", detail: "Start it again with python -m repo_radar web." };
  }
  if (!status) return { tone: "neutral", title: "Checking GitHub", detail: "" };
  if (!status.github_token_configured) {
    return {
      tone: "warning",
      title: "GitHub token missing",
      detail: "Add GITHUB_TOKEN to .env and restart. Saved data and preferences still work.",
    };
  }
  if (!status.last_sync) {
    return { tone: "warning", title: "Not synced yet", detail: "Sync stars to bring in your GitHub library." };
  }
  const login = status.authenticated_user;
  return {
    tone: "positive",
    title: login ? `Synced as ${login}` : "Synced with GitHub",
    detail: `${pluralize(status.starred_count, "starred repository", "starred repositories")}, synced ${formatRelative(status.last_sync)}`,
    when: formatDateTime(status.last_sync),
  };
}

/**
 * renders the GitHub and local status indicators in the sidebar and phone bar
 * @returns {void} no return value
 */
function renderStatus() {
  const summary = githubSummary();
  const dot = document.querySelector("#github-dot");
  const mobile = document.querySelector("#mobile-status");
  dot.dataset.tone = summary.tone;
  mobile.dataset.tone = summary.tone;
  mobile.setAttribute("aria-label", `GitHub: ${summary.title}`);
  mobile.title = summary.title;
  document.querySelector("#github-state").textContent = summary.title;
  const detail = document.querySelector("#github-detail");
  detail.textContent = summary.detail;
  detail.title = summary.when ? `Last sync ${summary.when}` : "";
  document.querySelector("#local-state").textContent = state.serverReachable
    ? "Local data stays on this machine"
    : "Can't reach the local server";
}

/**
 * renders the item counts beside navigation entries
 * @returns {void} no return value
 */
function renderNavCounts() {
  const library = state.interested && state.starred ? state.interested.length + state.starred.length : "";
  const history = state.feedback ? state.feedback.length : "";
  document.querySelector('[data-count="library"]').textContent = library === "" ? "" : formatCount(library);
  document.querySelector('[data-count="history"]').textContent = history === "" ? "" : formatCount(history);
}

/**
 * re-renders every view that reads local data
 * @returns {void} no return value
 */
function renderLocalViews() {
  renderStatus();
  renderNavCounts();
  renderOverview();
  renderLibrary();
  renderPreferences();
  renderHistory();
}

/**
 * synchronizes starred repositories from GitHub
 * @param {HTMLButtonElement} control button that started the sync
 * @returns {Promise<void>} no return value
 */
async function syncStars(control) {
  await runBusy(control, "Syncing...", async () => {
    const dots = [document.querySelector("#github-dot"), document.querySelector("#mobile-status")];
    for (const dot of dots) dot.dataset.tone = "busy";
    document.querySelector("#github-state").textContent = "Syncing with GitHub";
    try {
      const result = await api("/api/sync", { method: "POST" });
      await refresh();
      const reconciled = result.reconciled_count
        ? ` ${pluralize(result.reconciled_count, "saved repository", "saved repositories")} you starred elsewhere moved to Starred.`
        : "";
      Toasts.show({
        message: `Synced ${pluralize(result.starred_count, "starred repository", "starred repositories")} from GitHub.${reconciled}`,
        tone: "positive",
      });
    } catch (error) {
      renderStatus();
      Toasts.show({ message: `Sync didn't finish: ${error.message}`, tone: "danger" });
    }
  });
}

/* ---------- overview ---------- */

/**
 * builds one overview statistic
 * @param {string} label what is counted
 * @param {number|string} value count
 * @param {string} note supporting detail
 * @returns {HTMLElement} statistic group
 */
function statistic(label, value, note) {
  return h(
    "div",
    { class: "stat" },
    h("dt", { text: label }),
    h("dd", {}, h("span", { class: "stat-value", text: value }), note ? h("span", { class: "stat-note", text: note }) : null),
  );
}

/**
 * builds a signal strength section from normalized profile values
 * @param {string} title signal group name
 * @param {object} values normalized values keyed by signal
 * @param {number} limit maximum rows
 * @returns {HTMLElement} signal section
 */
function signalSection(title, values, limit = 10) {
  const entries = Object.entries(values || {}).slice(0, limit);
  return h(
    "section",
    {},
    h("h3", { text: title }),
    entries.length
      ? entries.map(([name, score]) =>
          h(
            "div",
            { class: "signal-row" },
            h("span", { class: "signal-name", title: name, text: name }),
            h("span", { class: "signal-bar", "aria-hidden": "true", style: { "--value": String(score) } }, h("span")),
            h("span", { class: "signal-value", text: Number(score).toFixed(2) }),
          ),
        )
      : h("p", { class: "block-note", text: "No signals yet" }),
  );
}

/**
 * reports whether the profile has any source of preference signals
 * @returns {boolean} whether any signal source exists
 */
function profileHasSignals() {
  const profile = state.profile;
  if (!profile) return true;
  return Boolean(
    Object.keys(profile.languages || {}).length ||
      Object.keys(profile.topics || {}).length ||
      Object.keys(profile.keywords || {}).length,
  );
}

/**
 * builds the getting started steps shown while the profile is empty
 * @returns {HTMLElement} setup block
 */
function setupBlock() {
  const status = state.status || {};
  const profile = state.profile || {};
  const step = (done, title, body, action) =>
    h(
      "li",
      {},
      h("h3", { class: done ? "is-done" : undefined }, done ? icon("check", { label: "Done" }) : null, ` ${title}`),
      h("p", { text: body }),
      done ? null : action,
    );
  const syncButton = button({ label: "Sync stars", icon: "refresh-cw", size: "sm" });
  syncButton.addEventListener("click", () => syncStars(syncButton));
  return h(
    "section",
    { class: "setup", "aria-labelledby": "setup-title" },
    h("h2", { id: "setup-title", text: "Give Repo Radar something to go on" }),
    h("p", { text: "Recommendations need at least one source of interests. Any of these is enough to start." }),
    h(
      "ol",
      { class: "setup-steps" },
      step(
        profile.seed_count > 0,
        "Add a few interests",
        "Languages, topics, and keywords you want more of.",
        linkButton({ label: "Add interests", href: "#preferences", size: "sm" }),
      ),
      step(
        profile.imported_count > 0,
        "Import your public work",
        "GitProfileLens reads the repositories you own on GitHub.",
        linkButton({ label: "Import profile", href: "#preferences", size: "sm" }),
      ),
      step(status.starred_count > 0, "Sync your stars", "Starred repositories are the strongest signal.", syncButton),
    ),
  );
}

/**
 * builds a short list of the top results from the latest search of one kind
 * @param {string} kind discover or contribute
 * @returns {HTMLElement|null} latest results group, or null when there is none
 */
function latestGroup(kind) {
  const source = state[kind];
  if (!source.results || !source.results.length) return null;
  const isDiscover = kind === "discover";
  const top = source.results.slice(0, 3);
  const when = formatRelative(source.at);
  return h(
    "section",
    { class: "latest-group" },
    h(
      "h3",
      {},
      h("span", { text: isDiscover ? "Repositories" : "Open issues" }),
      h("a", { class: "text-link", href: `#${kind}`, text: `All ${source.results.length}${when ? `, found ${when}` : ""}` }),
    ),
    h(
      "ol",
      { class: "compact-list" },
      top.map((item) =>
        h(
          "li",
          { class: "compact-item" },
          h(
            "span",
            { class: "score-value" },
            h("span", { "aria-hidden": "true" }, String(Math.round(item.score * 100)), h("small", { text: "%" })),
            h("span", { class: "visually-hidden", text: `Score ${Math.round(item.score * 100)} percent` }),
          ),
          h(
            "div",
            {},
            isDiscover
              ? h("span", { class: "entry-title" }, repositoryLink(item.full_name, item.url))
              : h(
                  "a",
                  { class: "entry-link", href: item.url, target: "_blank", rel: "noopener noreferrer" },
                  item.title,
                  h("span", { class: "visually-hidden", text: " (opens on GitHub)" }),
                ),
            h("p", {
              text: isDiscover ? item.description || "No description provided" : `${item.repository} #${item.number}`,
            }),
          ),
        ),
      ),
    ),
  );
}

/**
 * renders the overview from local state and this session's latest results
 * @returns {void} no return value
 */
function renderOverview() {
  const status = state.status;
  const profile = state.profile;
  const lede = document.querySelector("#overview-lede");
  if (status && status.authenticated_user && status.last_sync) {
    lede.textContent = `Synced with GitHub as ${status.authenticated_user}. Recommendations and issues are ranked against your profile, and everything here is read from this machine.`;
  }

  const setup = document.querySelector("#overview-setup");
  setup.replaceChildren(...(profile && !profileHasSignals() ? [setupBlock()] : []));

  const stats = document.querySelector("#overview-stats");
  if (state.errors.profile || state.errors.status) {
    stats.replaceChildren(
      stateBlock({ kind: "error", iconName: "circle-alert", title: "Local data didn't load", body: state.errors.profile || state.errors.status, compact: true }),
    );
  } else if (profile && status) {
    stats.setAttribute("aria-busy", "false");
    stats.replaceChildren(
      statistic("Saved repositories", formatNumber(profile.interested_count), "In your Library"),
      statistic(
        "Starred repositories",
        formatNumber(status.starred_count),
        status.last_sync ? `Synced ${formatRelative(status.last_sync)}` : "Not synced yet",
      ),
      statistic(
        "Imported repositories",
        formatNumber(profile.imported_count),
        status.imported_username ? `GitProfileLens, ${status.imported_username}` : "No GitProfileLens import",
      ),
      statistic("Manual interests", formatNumber(profile.seed_count), "Languages, topics, keywords"),
      statistic("Feedback records", formatNumber(profile.feedback_count), "Saved, starred, dismissed, blocked"),
    );
  }

  const latest = document.querySelector("#overview-latest");
  const groups = [latestGroup("discover"), latestGroup("contribute")].filter(Boolean);
  latest.replaceChildren(
    ...(groups.length
      ? groups
      : [
          stateBlock({
            iconName: "search",
            title: "No searches yet in this session",
            body: "Results from Discover and Contribute appear here so you can pick up where you left off.",
            actions: [
              linkButton({ label: "Discover repositories", href: "#discover", size: "sm", icon: "compass" }),
              linkButton({ label: "Find issues", href: "#contribute", size: "sm", icon: "circle-dot" }),
            ],
            compact: true,
          }),
        ]),
  );

  const profileContainer = document.querySelector("#overview-profile");
  if (profile) {
    profileContainer.replaceChildren(
      profileHasSignals()
        ? h(
            "div",
            { class: "signal-summary" },
            signalSection("Top languages", profile.languages, 4),
            signalSection("Top topics", profile.topics, 4),
          )
        : h("p", { class: "block-note", text: "No profile signals yet." }),
    );
  }

  const feedbackContainer = document.querySelector("#overview-feedback");
  if (state.feedback) {
    const counts = Object.fromEntries(CLASSIFICATION_ORDER.map((name) => [name, 0]));
    for (const record of state.feedback) counts[record.classification] = (counts[record.classification] || 0) + 1;
    feedbackContainer.replaceChildren(
      h(
        "dl",
        { class: "tally" },
        CLASSIFICATION_ORDER.map((name) =>
          h(
            "div",
            { class: "tally-row" },
            h("dt", {}, icon(CLASSIFICATIONS[name].icon), CLASSIFICATIONS[name].label),
            h("dd", { text: formatNumber(counts[name]) }),
          ),
        ),
      ),
    );
  }
}

/* ---------- discover ---------- */

const discoverForm = document.querySelector("#discover-form");
const discoverFields = {
  search: document.querySelector("#filter-search"),
  min_stars: document.querySelector("#filter-min"),
  max_stars: document.querySelector("#filter-max"),
  limit: document.querySelector("#filter-limit"),
  hidden_gems: document.querySelector("#filter-gems"),
};

/**
 * reads the discover controls
 * @returns {object} raw control values
 */
function readDiscoverControls() {
  return {
    search: discoverFields.search.value.trim(),
    min_stars: discoverFields.min_stars.value.trim(),
    max_stars: discoverFields.max_stars.value.trim(),
    limit: discoverFields.limit.value.trim(),
    hidden_gems: discoverFields.hidden_gems.checked,
  };
}

/**
 * writes values into the discover controls
 * @param {object} values control values
 * @returns {void} no return value
 */
function writeDiscoverControls(values) {
  discoverFields.search.value = values.search ?? "";
  discoverFields.min_stars.value = values.min_stars ?? "0";
  discoverFields.max_stars.value = values.max_stars ?? "";
  discoverFields.limit.value = values.limit ?? "10";
  discoverFields.hidden_gems.checked = Boolean(values.hidden_gems);
}

/**
 * shows or clears a field validation message
 * @param {HTMLInputElement} input validated input
 * @param {string} message error message, or an empty string when valid
 * @returns {boolean} whether the field is valid
 */
function setFieldError(input, message) {
  const error = document.querySelector(`#${input.id}-error`);
  input.setAttribute("aria-invalid", message ? "true" : "false");
  if (error) {
    error.textContent = message;
    error.hidden = !message;
  }
  return !message;
}

/**
 * validates a whole number control
 * @param {HTMLInputElement} input number input
 * @param {object} rules minimum, maximum, whether empty is allowed, and the field name
 * @returns {string} error message or an empty string
 */
function wholeNumberError(input, { min, max, optional = false, name }) {
  const raw = input.value.trim();
  if (!raw) return optional ? "" : `Enter ${name}.`;
  if (!/^\d+$/.test(raw)) return `${sentenceCase(name)} must be a whole number.`;
  const value = Number(raw);
  if (min !== undefined && value < min) return `${sentenceCase(name)} must be at least ${min}.`;
  if (max !== undefined && value > max) return `${sentenceCase(name)} can be at most ${max}.`;
  return "";
}

/**
 * validates the discover filters and focuses the first invalid one
 * @returns {boolean} whether every filter is valid
 */
function validateDiscover() {
  const minimumError = wholeNumberError(discoverFields.min_stars, { min: 0, optional: true, name: "minimum stars" });
  let maximumError = wholeNumberError(discoverFields.max_stars, { min: 0, optional: true, name: "maximum stars" });
  const minimum = Number(discoverFields.min_stars.value || 0);
  if (!maximumError && discoverFields.max_stars.value.trim() && Number(discoverFields.max_stars.value) < minimum) {
    maximumError = "Maximum stars can't be lower than minimum stars.";
  }
  const limitError = wholeNumberError(discoverFields.limit, { min: 1, max: 50, name: "a result count" });
  const results = [
    setFieldError(discoverFields.min_stars, minimumError),
    setFieldError(discoverFields.max_stars, maximumError),
    setFieldError(discoverFields.limit, limitError),
  ];
  const firstInvalid = [discoverFields.min_stars, discoverFields.max_stars, discoverFields.limit][results.indexOf(false)];
  if (firstInvalid) {
    document.querySelector("#discover-filters").open = true;
    firstInvalid.focus();
  }
  return results.every(Boolean);
}

/**
 * counts the discover filters that differ from their defaults
 * @param {object} values control values
 * @returns {number} active filter count
 */
function activeDiscoverFilters(values) {
  return [
    values.min_stars && values.min_stars !== "0",
    Boolean(values.max_stars),
    values.limit && values.limit !== DISCOVER_DEFAULTS.limit,
    values.hidden_gems,
  ].filter(Boolean).length;
}

/**
 * remembers the discover controls and refreshes their summary
 * @returns {void} no return value
 */
function persistDiscoverControls() {
  const values = readDiscoverControls();
  interfaceStore.write({ discover: values });
  const count = activeDiscoverFilters(values);
  document.querySelector("#discover-filter-count").textContent = count ? `${count} active` : "";
}

let resolutionTimer = null;
let resolutionSequence = 0;

/**
 * shows which language the current query resolves to, using the server's parser
 * @returns {Promise<void>} no return value
 */
async function updateResolution() {
  const sequence = ++resolutionSequence;
  const target = document.querySelector("#search-resolution");
  try {
    const search = await api(`/api/search/parse?${new URLSearchParams({ search: discoverFields.search.value })}`);
    if (sequence !== resolutionSequence) return;
    const language = h("span", { class: "resolution-language" }, icon("code-xml"), h("span", {}, "Language: ", h("strong", { text: search.language })));
    const note = search.explicit_language
      ? "Named in your search"
      : "Python is used when the search names no language";
    const terms = search.terms.length ? h("span", { text: `Topic terms: ${search.terms.join(" ")}` }) : null;
    target.replaceChildren();
    appendChildren(target, [language, h("span", { text: note }), terms]);
  } catch {
    if (sequence === resolutionSequence) target.replaceChildren();
  }
}

/**
 * describes the filters a discover request used
 * @param {object} request the request values
 * @returns {string} readable filter summary
 */
function describeDiscoverRequest(request) {
  const parts = [];
  const minimum = Number(request.min_stars || 0);
  if (request.max_stars) parts.push(`${formatNumber(minimum)} to ${formatNumber(request.max_stars)} stars`);
  else if (request.hidden_gems) parts.push(`${formatNumber(minimum)} to 1,000 stars (hidden gems)`);
  else if (minimum > 0) parts.push(`at least ${formatNumber(minimum)} stars`);
  parts.push(`up to ${request.limit} results`);
  return sentenceCase(parts.join(", "));
}

/**
 * runs a repository search with the current controls
 * @returns {Promise<void>} no return value
 */
async function runDiscover() {
  if (state.discover.loading || !validateDiscover()) return;
  const request = readDiscoverControls();
  const parameters = new URLSearchParams({
    limit: request.limit,
    search: request.search,
    min_stars: request.min_stars || "0",
    hidden_gems: request.hidden_gems,
  });
  if (request.max_stars) parameters.set("max_stars", request.max_stars);
  persistDiscoverControls();
  state.discover.loading = true;
  state.discover.error = null;
  renderDiscoverResults();
  announce("Searching GitHub for repositories.");
  await runBusy(document.querySelector("#refresh"), "Searching...", async () => {
    try {
      const data = await api(`/api/recommendations?${parameters}`);
      state.discover = {
        ...state.discover,
        results: data.recommendations,
        message: data.message,
        search: data.search || null,
        request,
        at: new Date().toISOString(),
        restored: false,
        generation: state.discover.generation + 1,
      };
      persistResults("discover");
    } catch (error) {
      state.discover.error = error.message;
    } finally {
      state.discover.loading = false;
    }
  });
  renderDiscoverResults();
  renderOverview();
  revealResults("#discover-results");
  announce(
    state.discover.error
      ? `Search failed. ${state.discover.error}`
      : `Found ${pluralize(state.discover.results.length, "repository", "repositories")}.`,
  );
}

/**
 * stores the latest results of one kind for this browser session
 * @param {string} kind discover or contribute
 * @returns {void} no return value
 */
function persistResults(kind) {
  const source = state[kind];
  const stored = { results: source.results, message: source.message, request: source.request, at: source.at };
  if (kind === "discover") stored.search = source.search;
  else Object.assign(stored, { warning: source.warning, applied: source.applied });
  resultStore.write({ [kind]: stored });
}

/**
 * builds one repository recommendation
 * @param {object} repository recommendation from the API
 * @param {number} index position in the result list
 * @param {Map<string,string>} feedback current classifications
 * @returns {HTMLElement} recommendation entry
 */
function recommendationEntry(repository, index, feedback) {
  const titleId = `discover-result-${index}`;
  const entry = h("article", { class: "entry", "aria-labelledby": titleId });
  const isSaved = feedback.get(repository.full_name.toLowerCase()) === "interested";
  const name = repository.full_name;
  const save = button({
    label: isSaved ? "Saved" : "Save",
    icon: isSaved ? "bookmark-check" : "bookmark",
    size: "sm",
    ariaLabel: `${isSaved ? "Saved" : "Save"} ${name}`,
  });
  save.disabled = isSaved;
  save.addEventListener("click", () => saveRecommendation(repository, save, entry));
  const dismiss = button({ label: "Not for me", icon: "eye-off", variant: "ghost", size: "sm", ariaLabel: `Not for me: ${name}` });
  dismiss.addEventListener("click", () => hideRecommendation(repository, "not interested", dismiss, entry));
  const block = button({ label: "Block", icon: "ban", variant: "ghost", size: "sm", ariaLabel: `Block ${name}` });
  block.addEventListener("click", () => hideRecommendation(repository, "blocked", block, entry));
  const star = button({ label: "Star on GitHub", icon: "star", size: "sm", ariaLabel: `Star ${name} on GitHub` });
  star.addEventListener("click", () =>
    starRepository(repository, star, entry, () => removeRecommendation(repository, { focus: true })),
  );
  entry.append(
    scoreColumn(repository.score),
    h(
      "div",
      { class: "entry-body" },
      h("h3", { class: "entry-title", id: titleId }, repositoryLink(name, repository.url)),
      repository.description
        ? h("p", { class: "entry-description", text: repository.description })
        : h("p", { class: "entry-description entry-description--empty", text: "No description provided" }),
      metaList([
        repository.language ? ["code-xml", repository.language] : null,
        ["star", `${formatCount(repository.stars)} stars`, `${formatNumber(repository.stars)} stars`],
      ]),
      chipList(repository.topics, 6, "topics"),
      h("p", { class: "why" }, h("span", { class: "why-label", text: "Why" }), h("span", { text: sentenceCase(repository.explanation) })),
    ),
    h("div", { class: "entry-actions" }, save, star, dismiss, block),
    h("p", { class: "entry-status", role: "status" }),
  );
  return entry;
}

/**
 * builds the heading that states what a repository search returned and which filters it used
 * @param {object} source discover state
 * @returns {HTMLElement} results heading
 */
function discoverHeader(source) {
  const language = source.search ? source.search.language : "";
  const terms = source.search && source.search.terms.length ? ` for "${source.search.terms.join(" ")}"` : "";
  const count = pluralize(source.results.length, `${language} repository`.trim(), `${language} repositories`.trim());
  return h(
    "div",
    { class: "results-head" },
    h("h2", { class: "results-title", tabindex: "-1", text: `${count}${terms}` }),
    h("p", {
      class: "results-meta",
      text: `${source.request ? describeDiscoverRequest(source.request) : ""}${source.at ? `. Searched ${formatRelative(source.at)}` : ""}`,
    }),
  );
}

/**
 * renders the discover result area for the current state
 * @param {object} options optional result index to focus after a removal
 * @returns {void} no return value
 */
function renderDiscoverResults({ focusIndex } = {}) {
  const container = document.querySelector("#discover-results");
  const source = state.discover;
  container.setAttribute("aria-busy", String(source.loading));
  if (source.loading) {
    container.replaceChildren(
      loadingBlock(
        "Searching GitHub",
        "Running focused searches and ranking the candidates against your profile. This usually takes a few seconds.",
      ),
    );
    return;
  }
  if (source.error) {
    const retry = button({ label: "Try again", icon: "refresh-cw", size: "sm", onClick: () => runDiscover() });
    container.replaceChildren(
      stateBlock({ kind: "error", iconName: "circle-alert", title: "The search didn't finish", body: source.error, actions: [retry] }),
    );
    return;
  }
  if (!source.results) {
    container.replaceChildren(
      stateBlock({
        iconName: "compass",
        title: "Search to see repositories picked for you",
        body: "Leave the box empty for Python repositories that fit your profile, or name a language and a few topic words.",
      }),
    );
    return;
  }
  if (!source.results.length) {
    const noSignals = source.message === "No preference signals are available yet";
    container.replaceChildren(
      discoverHeader(source),
      stateBlock({
        iconName: "inbox",
        title: noSignals ? "Your profile has no signals yet" : "No repositories passed your filters",
        body: noSignals
          ? "Add interests, import your public repositories, or sync your stars, then search again."
          : "Try a wider star range, turn off Hidden gems, or use fewer topic words. Repositories you already saved, starred, dismissed, or blocked are left out.",
        actions: noSignals ? [linkButton({ label: "Add interests", href: "#preferences", size: "sm" })] : [],
      }),
    );
    return;
  }
  const header = discoverHeader(source);
  const explainer = h(
    "details",
    { class: "results-explainer" },
    h("summary", {}, icon("info"), "How these scores work"),
    h("p", {
      text:
        "Each score reflects how well a repository's topics, language, and description keywords match your profile, plus how recently it was active and its star and fork counts. A result that closely resembles one ranked above it is nudged down so the list stays varied. Results always stay inside the searched language.",
    }),
  );
  const notices = [];
  if (source.restored) {
    notices.push(
      h("p", { class: "notice notice--info" }, icon("history"), h("span", { text: "These results are from earlier in this session. Search again for a fresh list." })),
    );
  }
  const feedback = feedbackByRepository();
  const list = h("div", { class: "ledger-list" }, source.results.map((item, index) => recommendationEntry(item, index, feedback)));
  container.replaceChildren(header, explainer, ...notices, list);
  if (focusIndex !== undefined) {
    const entries = list.querySelectorAll(".entry");
    const target = entries[Math.min(focusIndex, entries.length - 1)];
    (target ? target.querySelector(".entry-title a") : header.querySelector(".results-title"))?.focus();
  }
}

/**
 * removes one recommendation from the visible results
 * @param {object} repository recommendation
 * @param {object} options whether focus should move to the next result
 * @returns {number} the removed index, or -1
 */
function removeRecommendation(repository, { focus = false } = {}) {
  const results = state.discover.results || [];
  const index = results.indexOf(repository);
  if (index < 0) return -1;
  results.splice(index, 1);
  persistResults("discover");
  renderDiscoverResults(focus ? { focusIndex: index } : {});
  renderOverview();
  return index;
}

/**
 * saves one recommendation for later
 * @param {object} repository recommendation
 * @param {HTMLButtonElement} control save button
 * @param {HTMLElement} entry recommendation entry
 * @returns {Promise<void>} no return value
 */
async function saveRecommendation(repository, control, entry) {
  const saved = await runBusy(control, "Saving...", async () => {
    try {
      await api("/api/feedback", {
        method: "POST",
        body: JSON.stringify({ ...repositoryPayload(repository), classification: "interested" }),
      });
      return true;
    } catch (error) {
      setEntryStatus(entry, `Couldn't save: ${error.message}`, "danger");
      return false;
    }
  });
  if (!saved) return;
  setButtonLabel(control, "Saved");
  setButtonIcon(control, "bookmark-check");
  control.setAttribute("aria-label", `Saved ${repository.full_name}`);
  control.disabled = true;
  setEntryStatus(entry, "");
  Toasts.show({
    message: `Saved ${repository.full_name} to your Library.`,
    tone: "positive",
    action: { label: "Undo", onClick: () => undoFeedback(repository.full_name, "Removed it from Saved.") },
  });
  await refresh("interested", "profile", "feedback");
}

/**
 * dismisses or blocks one recommendation and offers to undo it
 * @param {object} repository recommendation
 * @param {string} classification "not interested" or "blocked"
 * @param {HTMLButtonElement} control button that was used
 * @param {HTMLElement} entry recommendation entry
 * @returns {Promise<void>} no return value
 */
async function hideRecommendation(repository, classification, control, entry) {
  const done = await runBusy(control, null, async () => {
    try {
      await api("/api/feedback", {
        method: "POST",
        body: JSON.stringify({ ...repositoryPayload(repository), classification }),
      });
      return true;
    } catch (error) {
      setEntryStatus(entry, `Couldn't update: ${error.message}`, "danger");
      return false;
    }
  });
  if (!done) return;
  const generation = state.discover.generation;
  const index = removeRecommendation(repository, { focus: true });
  Toasts.show({
    message:
      classification === "blocked"
        ? `Blocked ${repository.full_name}. It won't be recommended again.`
        : `Hid ${repository.full_name} from your recommendations.`,
    tone: "positive",
    action: {
      label: "Undo",
      onClick: async () => {
        const cleared = await undoFeedback(repository.full_name, "It can be recommended again.");
        // only restore the result into the list it came from
        if (cleared && generation === state.discover.generation && state.discover.results && index >= 0) {
          state.discover.results.splice(Math.min(index, state.discover.results.length), 0, repository);
          persistResults("discover");
          renderDiscoverResults();
          renderOverview();
        }
      },
    },
  });
  await refresh("feedback");
}

/**
 * removes one feedback record after an Undo
 * @param {string} fullName repository name
 * @param {string} outcome sentence describing the effect
 * @returns {Promise<boolean>} whether the record was cleared
 */
async function undoFeedback(fullName, outcome) {
  try {
    await api(`/api/feedback/${repositoryPath(fullName)}`, { method: "DELETE" });
  } catch (error) {
    Toasts.show({ message: `Couldn't undo for ${fullName}: ${error.message}`, tone: "danger" });
    return false;
  }
  await refresh("interested", "profile", "feedback");
  renderDiscoverResults();
  Toasts.show({ message: `Undone for ${fullName}. ${outcome}`, tone: "positive" });
  return true;
}

/**
 * stars one repository on GitHub after confirmation, reporting success only once GitHub confirms
 * @param {object} repository repository metadata
 * @param {HTMLButtonElement} control star button
 * @param {HTMLElement} entry entry showing the repository
 * @param {Function} onStarred callback after a confirmed star
 * @returns {Promise<void>} no return value
 */
async function starRepository(repository, control, entry, onStarred) {
  const login = state.status && state.status.authenticated_user;
  const isSaved = feedbackByRepository().get(repository.full_name.toLowerCase()) === "interested";
  const confirmed = await confirmDialog({
    title: `Star ${repository.full_name} on GitHub?`,
    body: `This adds a star from your GitHub account${login ? ` (${login})` : ""}. Repo Radar records it in your starred library${isSaved ? ", removes it from Saved," : ""} and stops recommending it.`,
    confirmLabel: "Star on GitHub",
  });
  if (!confirmed) return;
  setEntryStatus(entry, "Waiting for GitHub to confirm the star...");
  const starred = await runBusy(control, "Starring...", async () => {
    try {
      await api("/api/star", { method: "POST", body: JSON.stringify(repositoryPayload(repository)) });
      return true;
    } catch (error) {
      setEntryStatus(entry, `GitHub didn't add the star: ${error.message}`, "danger");
      return false;
    }
  });
  if (!starred) return;
  Toasts.show({ message: `Starred ${repository.full_name} on GitHub.`, tone: "positive" });
  onStarred();
  await refresh();
}

/* ---------- contribute ---------- */

const contributeForm = document.querySelector("#contribute-form");

/**
 * reads the contribution controls
 * @returns {object} control values
 */
function readContributeControls() {
  const scope = document.querySelector('input[name="contribution-scope"]:checked');
  return {
    scope: scope ? scope.value : "discover",
    labels: [...document.querySelectorAll('input[name="contribution-label"]:checked')].map((input) => input.value),
    contributor_friendly: document.querySelector("#contribution-friendly").checked,
    unassigned_only: document.querySelector("#contribution-unassigned").checked,
    limit: document.querySelector("#contribution-limit").value.trim(),
  };
}

/**
 * writes values into the contribution controls
 * @param {object} values control values
 * @returns {void} no return value
 */
function writeContributeControls(values) {
  for (const input of document.querySelectorAll('input[name="contribution-scope"]')) {
    input.checked = input.value === (values.scope || "discover");
  }
  const labels = new Set(values.labels || []);
  for (const input of document.querySelectorAll('input[name="contribution-label"]')) input.checked = labels.has(input.value);
  document.querySelector("#contribution-friendly").checked = Boolean(values.contributor_friendly);
  document.querySelector("#contribution-unassigned").checked = Boolean(values.unassigned_only);
  document.querySelector("#contribution-limit").value = values.limit || "10";
}

/**
 * names the selected issue types using the labels shown on their controls
 * @param {Array<string>} values selected category values
 * @returns {Array<string>} display names
 */
function issueTypeNames(values) {
  return values.map((value) => {
    const input = document.querySelector(`input[name="contribution-label"][value="${CSS.escape(value)}"]`);
    return input ? input.parentElement.textContent.trim() : value;
  });
}

/**
 * describes contribution filters in a sentence
 * @param {object} values scope, categories, and toggles
 * @returns {string} filter summary
 */
function describeContributionFilters(values) {
  const parts = [values.scope === "saved_starred" ? "Saved and starred repositories" : "Across GitHub"];
  parts.push(values.labels.length ? issueTypeNames(values.labels).join(" or ") : "any issue type");
  if (values.contributor_friendly) parts.push("contributor-friendly only");
  if (values.unassigned_only) parts.push("unassigned only");
  return parts.join(", ");
}

/**
 * remembers the contribution controls and refreshes their summary
 * @returns {void} no return value
 */
function persistContributeControls() {
  const values = readContributeControls();
  interfaceStore.write({ contribute: values });
  document.querySelector("#contribution-filter-summary").textContent = describeContributionFilters(values);
}

/**
 * runs a contribution search with the current controls
 * @returns {Promise<void>} no return value
 */
async function runContributions() {
  if (state.contribute.loading) return;
  const limitInput = document.querySelector("#contribution-limit");
  if (!setFieldError(limitInput, wholeNumberError(limitInput, { min: 1, max: 50, name: "a result count" }))) {
    limitInput.focus();
    return;
  }
  const request = readContributeControls();
  const parameters = new URLSearchParams({
    limit: request.limit,
    unassigned_only: request.unassigned_only,
    scope: request.scope,
    contributor_friendly: request.contributor_friendly,
  });
  // repeated label parameters are one OR group; the response echoes the filters it applied
  for (const category of request.labels) parameters.append("label", category);
  persistContributeControls();
  state.contribute.loading = true;
  state.contribute.error = null;
  renderContributeResults();
  announce("Searching GitHub for open issues.");
  await runBusy(document.querySelector("#refresh-contributions"), "Reading open issues...", async () => {
    try {
      const data = await api(`/api/contributions?${parameters}`);
      state.contribute = {
        ...state.contribute,
        results: data.contributions,
        message: data.message,
        warning: data.warning,
        applied: { scope: data.scope, labels: data.labels || [], contributor_friendly: Boolean(data.contributor_friendly) },
        request,
        at: new Date().toISOString(),
        restored: false,
      };
      persistResults("contribute");
    } catch (error) {
      state.contribute.error = error.message;
    } finally {
      state.contribute.loading = false;
    }
  });
  renderContributeResults();
  renderOverview();
  revealResults("#contribute-results");
  const warning = state.contribute.warning ? " Results are partial." : "";
  announce(
    state.contribute.error
      ? `Issue search failed. ${state.contribute.error}`
      : `Found ${pluralize(state.contribute.results.length, "open issue")}.${warning}`,
  );
}

/**
 * builds one ledger row
 * @param {string} label evidence heading
 * @param {Array<Node|string>} content evidence content
 * @param {string} className optional row modifier
 * @returns {HTMLElement} ledger row
 */
function ledgerRow(label, content, className) {
  return h("div", { class: ["ledger-row", className] }, h("dt", { text: label }), h("dd", {}, content));
}

/**
 * builds an inline list of evidence statements
 * @param {Array<string>} values statements
 * @returns {HTMLUListElement} statement list
 */
function evidenceList(values) {
  return h("ul", {}, values.map((value) => h("li", { text: sentenceCase(value) })));
}

/**
 * builds one contribution opportunity
 * @param {object} item contribution from the API
 * @param {number} index position in the result list
 * @returns {HTMLElement} contribution entry
 */
function contributionEntry(item, index) {
  const titleId = `contribute-result-${index}`;
  const [owner, name] = splitName(item.repository);
  const source = CONTRIBUTION_SOURCES[item.source] || { label: item.source, tone: "neutral", icon: "compass" };
  const evidence = item.evidence || {};
  const rows = [];
  if (item.evidence) {
    for (const [key, label] of EVIDENCE_GROUPS) {
      if (evidence[key] && evidence[key].length) rows.push(ledgerRow(label, evidenceList(evidence[key])));
    }
  } else if (item.reasons && item.reasons.length) {
    rows.push(ledgerRow("Why recommended", evidenceList(item.reasons)));
  }
  const scopeTone = item.scope_signal === "Focused" ? "positive" : item.scope_signal === "Needs discussion" ? "warning" : "neutral";
  rows.push(
    ledgerRow("Scope readiness", [
      h("span", { class: `badge badge--${scopeTone}`, text: item.scope_signal }),
      item.scope_evidence && item.scope_evidence.length ? item.scope_evidence.join("; ") : null,
    ]),
  );
  if (evidence.assignment && evidence.assignment.length) {
    rows.push(ledgerRow("Assignment", evidenceList(evidence.assignment), "ledger-row--caution"));
  }
  return h(
    "article",
    { class: "entry", "aria-labelledby": titleId },
    scoreColumn(item.score),
    h(
      "div",
      { class: "entry-body" },
      h(
        "p",
        { class: "entry-repo" },
        icon("folder-git-2"),
        h(
          "a",
          { class: "entry-link", href: githubUrl(item.repository), target: "_blank", rel: "noopener noreferrer" },
          h("span", { class: "visually-hidden", text: "Repository " }),
          h("span", { class: "repo-name" }, h("span", { class: "repo-owner", text: `${owner}/` }), name),
          h("span", { class: "visually-hidden", text: " (opens on GitHub)" }),
        ),
        h("span", { class: `badge badge--${source.tone}` }, icon(source.icon), source.label),
        item.language ? h("span", { text: item.language }) : null,
      ),
      h(
        "h3",
        { class: "entry-title", id: titleId },
        h(
          "a",
          { href: item.url, target: "_blank", rel: "noopener noreferrer" },
          item.title,
          h("span", { class: "issue-number" }, `#${item.number}`, icon("arrow-up-right")),
          h("span", { class: "visually-hidden", text: " (opens the issue on GitHub)" }),
        ),
      ),
      metaList([
        ["message-square", pluralize(item.comments, "comment")],
        ["users", item.assignee_count ? `Assigned to ${item.assignee_count}` : "Unassigned"],
      ]),
      chipList(item.labels, 8, "labels"),
      h("dl", { class: "ledger" }, rows),
    ),
    h(
      "div",
      { class: "entry-actions" },
      linkButton({
        label: "Open issue",
        href: item.url,
        icon: "external-link",
        size: "sm",
        external: true,
        ariaLabel: `Open issue ${item.repository}#${item.number} on GitHub`,
      }),
    ),
  );
}

/**
 * reports whether the contribution controls still match the search that produced the results
 * @returns {boolean} whether the controls changed since the last search
 */
function contributionControlsChanged() {
  const request = state.contribute.request;
  if (!request) return false;
  const current = readContributeControls();
  return JSON.stringify({ ...current, labels: [...current.labels].sort() }) !== JSON.stringify({ ...request, labels: [...request.labels].sort() });
}

/**
 * builds the heading that states what an issue search returned and the filters the server applied
 * @param {object} source contribute state
 * @returns {HTMLElement} results heading
 */
function contributeHeader(source) {
  const applied = source.applied || { scope: "discover", labels: [], contributor_friendly: false };
  const appliedSummary = describeContributionFilters({
    scope: applied.scope,
    labels: applied.labels,
    contributor_friendly: applied.contributor_friendly,
    unassigned_only: source.request ? source.request.unassigned_only : false,
  });
  return h(
    "div",
    { class: "results-head" },
    h("h2", { class: "results-title", tabindex: "-1", text: pluralize(source.results.length, "open issue") }),
    h("p", { class: "results-meta", text: `${appliedSummary}${source.at ? `. Searched ${formatRelative(source.at)}` : ""}` }),
  );
}

/**
 * renders the contribution result area for the current state
 * @returns {void} no return value
 */
function renderContributeResults() {
  const container = document.querySelector("#contribute-results");
  const source = state.contribute;
  container.setAttribute("aria-busy", String(source.loading));
  if (source.loading) {
    container.replaceChildren(
      loadingBlock(
        "Reading open issues",
        "Searching GitHub in a few grouped requests, then ranking each issue. Issue search is rate limited, so this can take a little while.",
      ),
    );
    return;
  }
  if (source.error) {
    const retry = button({ label: "Try again", icon: "refresh-cw", size: "sm", onClick: () => runContributions() });
    container.replaceChildren(
      stateBlock({ kind: "error", iconName: "circle-alert", title: "The issue search didn't finish", body: source.error, actions: [retry] }),
    );
    return;
  }
  if (!source.results) {
    container.replaceChildren(
      stateBlock({
        iconName: "circle-dot",
        title: "Find an issue worth your time",
        body: "Choose where to look and which kinds of issues you want, then run the search. Every result explains which signals placed it there.",
      }),
    );
    return;
  }
  const notices = [];
  if (source.warning) {
    notices.push(
      h(
        "p",
        { class: "notice notice--warning", role: "status" },
        icon("triangle-alert"),
        h("span", { text: `Partial results. GitHub issue search stopped early: ${source.warning}` }),
      ),
    );
  }
  if (source.restored) {
    notices.push(
      h("p", { class: "notice notice--info" }, icon("history"), h("span", { text: "These results are from earlier in this session. Search again for a fresh list." })),
    );
  }
  if (contributionControlsChanged()) {
    notices.push(
      h("p", { class: "notice notice--info" }, icon("list-filter"), h("span", { text: "Your filters changed since this search. Select Find issues to apply them." })),
    );
  }
  if (!source.results.length) {
    const noSignals = source.message === "No preference signals are available yet";
    const needsLibrary = source.applied && source.applied.scope === "saved_starred" && !noSignals && /Save a repository/.test(source.message || "");
    container.replaceChildren(
      contributeHeader(source),
      ...notices,
      stateBlock({
        iconName: "inbox",
        title: noSignals ? "Your profile has no signals yet" : "No open issues matched",
        body:
          source.message && !noSignals
            ? `${source.message}. ${needsLibrary ? "" : "Try fewer issue types, turn off a filter, or look across GitHub."}`.trim()
            : "Add interests, import your public repositories, or sync your stars, then search again.",
        actions: noSignals ? [linkButton({ label: "Add interests", href: "#preferences", size: "sm" })] : [],
      }),
    );
    return;
  }
  const header = contributeHeader(source);
  const explainer = h(
    "details",
    { class: "results-explainer" },
    h("summary", {}, icon("info"), "How these scores work"),
    h("p", {
      text:
        "Relevance to your profile carries most of the score: how well the repository fits, and whether the issue's title, labels, and description mention your interests. Explicit invitations to contribute, recent activity, and signs of a clearly scoped task break ties between comparably relevant issues. No result includes a difficulty or time estimate, because issue metadata can't support one. At most three issues come from any one repository.",
    }),
  );
  container.replaceChildren(
    header,
    explainer,
    ...notices,
    h("div", { class: "ledger-list" }, source.results.map((item, index) => contributionEntry(item, index))),
  );
}

/* ---------- library ---------- */

/**
 * selects a library tab
 * @param {string} tab saved or starred
 * @param {boolean} moveFocus whether focus moves to the tab
 * @returns {void} no return value
 */
function selectLibraryTab(tab, moveFocus) {
  if (!LIBRARY_SORTS[tab]) return;
  state.libraryTab = tab;
  interfaceStore.write({ libraryTab: tab });
  for (const control of document.querySelectorAll(".tab[data-tab]")) {
    const selected = control.dataset.tab === tab;
    control.setAttribute("aria-selected", String(selected));
    control.tabIndex = selected ? 0 : -1;
    if (selected && moveFocus) control.focus();
  }
  document.querySelector("#library-panel-saved").hidden = tab !== "saved";
  document.querySelector("#library-panel-starred").hidden = tab !== "starred";
  const sort = document.querySelector("#library-sort");
  const chosen = (interfaceStore.read().librarySort || {})[tab] || LIBRARY_SORTS[tab][0][0];
  sort.replaceChildren(...LIBRARY_SORTS[tab].map(([value, label]) => h("option", { value, text: label })));
  sort.value = chosen;
  renderLibrary();
}

/**
 * filters and sorts one library collection
 * @param {Array<object>} repositories collection
 * @param {string} tab saved or starred
 * @returns {Array<object>} visible repositories
 */
function arrangeLibrary(repositories, tab) {
  const query = document.querySelector("#library-filter").value.trim().toLowerCase();
  const matches = query
    ? repositories.filter((repository) =>
        [repository.full_name, repository.description, repository.language, ...(repository.topics || [])]
          .filter(Boolean)
          .some((value) => value.toLowerCase().includes(query)),
      )
    : [...repositories];
  const sort = document.querySelector("#library-sort").value;
  if (sort === "name") matches.sort((left, right) => left.full_name.localeCompare(right.full_name, undefined, { sensitivity: "base" }));
  if (sort === "stars") matches.sort((left, right) => right.stars - left.stars || left.full_name.localeCompare(right.full_name));
  return matches;
}

/**
 * builds one saved repository
 * @param {object} repository saved repository from the API
 * @returns {HTMLElement} saved entry
 */
function savedEntry(repository) {
  const entry = h("article", { class: "entry entry--plain" });
  const star = button({ label: "Star on GitHub", icon: "star", size: "sm", ariaLabel: `Star ${repository.full_name} on GitHub` });
  star.addEventListener("click", () => starRepository(repository, star, entry, () => {}));
  const remove = button({ label: "Remove", icon: "trash-2", variant: "ghost", size: "sm", ariaLabel: `Remove ${repository.full_name} from Saved` });
  remove.addEventListener("click", () => removeSaved(repository, remove, entry));
  entry.append(
    h(
      "div",
      { class: "entry-body" },
      h("h3", { class: "entry-title" }, repositoryLink(repository.full_name, repository.url)),
      repository.description
        ? h("p", { class: "entry-description", text: repository.description })
        : h("p", { class: "entry-description entry-description--empty", text: "No description provided" }),
      metaList([
        repository.language ? ["code-xml", repository.language] : null,
        ["star", `${formatCount(repository.stars)} stars`, `${formatNumber(repository.stars)} stars`],
      ]),
      chipList(repository.topics, 6, "topics"),
      h("p", {
        class: "influence",
        text: `Adds ${pluralize(repository.signal_count, "profile signal")} at weight ${Number(repository.preference_weight).toFixed(2)}`,
      }),
    ),
    h("div", { class: "entry-actions" }, star, remove),
    h("p", { class: "entry-status", role: "status" }),
  );
  return entry;
}

/**
 * builds one synchronized starred repository
 * @param {object} repository starred repository from the API
 * @returns {HTMLElement} starred entry
 */
function starredEntry(repository) {
  const activity = formatRelative(repository.pushed_at || repository.updated_at);
  return h(
    "article",
    { class: "entry entry--plain" },
    h(
      "div",
      { class: "entry-body" },
      h("h3", { class: "entry-title" }, repositoryLink(repository.full_name, repository.url)),
      repository.description
        ? h("p", { class: "entry-description", text: repository.description })
        : h("p", { class: "entry-description entry-description--empty", text: "No description provided" }),
      metaList([
        repository.language ? ["code-xml", repository.language] : null,
        ["star", `${formatCount(repository.stars)} stars`, `${formatNumber(repository.stars)} stars`],
        activity ? ["clock", `Pushed ${activity}`, formatDateTime(repository.pushed_at || repository.updated_at)] : null,
      ]),
      chipList(repository.topics, 6, "topics"),
    ),
  );
}

/**
 * renders the selected library collection
 * @returns {void} no return value
 */
function renderLibrary() {
  document.querySelector('[data-count="saved"]').textContent = state.interested ? formatNumber(state.interested.length) : "";
  document.querySelector('[data-count="starred"]').textContent = state.starred ? formatNumber(state.starred.length) : "";
  const hasSaved = Boolean(state.interested && state.interested.length);
  document.querySelector("#star-all-saved").disabled = !hasSaved;
  document.querySelector("#clear-saved").disabled = !hasSaved;
  const status = state.status || {};
  document.querySelector("#starred-note").textContent = status.last_sync
    ? `From the GitHub sync ${formatRelative(status.last_sync)}. Use Sync stars to refresh it.`
    : "Nothing has been synced from GitHub yet.";

  const tab = state.libraryTab;
  const key = tab === "saved" ? "interested" : "starred";
  const container = document.querySelector(tab === "saved" ? "#interested-repositories" : "#starred-repositories");
  if (state.errors[key]) {
    container.replaceChildren(stateBlock({ kind: "error", iconName: "circle-alert", title: "This list didn't load", body: state.errors[key] }));
    return;
  }
  const repositories = state[key];
  if (!repositories) {
    container.replaceChildren(h("p", { class: "inline-loading", role: "status", text: "Loading..." }));
    return;
  }
  if (!repositories.length) {
    if (tab === "saved") {
      container.replaceChildren(
        stateBlock({
          iconName: "bookmark",
          title: "Nothing saved yet",
          body: "Save repositories from Discover to keep them here. Saved repositories also strengthen your profile.",
          actions: [linkButton({ label: "Open Discover", href: "#discover", icon: "compass", size: "sm" })],
        }),
      );
    } else {
      const sync = button({ label: "Sync stars", icon: "refresh-cw", size: "sm" });
      sync.addEventListener("click", () => syncStars(sync));
      container.replaceChildren(
        stateBlock({
          iconName: "star",
          title: "No starred repositories cached",
          body: "Sync with GitHub to see your starred repositories here and use them as your strongest profile signal.",
          actions: [sync],
        }),
      );
    }
    return;
  }
  const arranged = arrangeLibrary(repositories, tab);
  if (!arranged.length) {
    container.replaceChildren(
      stateBlock({ iconName: "search", title: "No repositories match that filter", body: "Try a shorter word, or clear the filter.", compact: true }),
    );
    return;
  }
  renderCollection(container, arranged, tab === "saved" ? savedEntry : starredEntry, state.expanded[tab], () => {
    state.expanded[tab] = !state.expanded[tab];
    renderLibrary();
  });
}

/**
 * removes one saved repository after confirmation
 * @param {object} repository saved repository
 * @param {HTMLButtonElement} control remove button
 * @param {HTMLElement} entry saved entry
 * @returns {Promise<void>} no return value
 */
async function removeSaved(repository, control, entry) {
  const confirmed = await confirmDialog({
    title: `Remove ${repository.full_name} from Saved?`,
    body: "It stops counting toward your profile and can be recommended again. Nothing changes on GitHub.",
    confirmLabel: "Remove",
    tone: "danger",
  });
  if (!confirmed) return;
  const removed = await runBusy(control, "Removing...", async () => {
    try {
      await api(`/api/interested/${repositoryPath(repository.full_name)}`, { method: "DELETE" });
      return true;
    } catch (error) {
      setEntryStatus(entry, `Couldn't remove: ${error.message}`, "danger");
      return false;
    }
  });
  if (!removed) return;
  Toasts.show({ message: `Removed ${repository.full_name} from Saved.`, tone: "positive" });
  await refresh("interested", "profile", "feedback");
  document.querySelector("#library-filter").focus();
}

/**
 * stars every saved repository after confirmation
 * @param {HTMLButtonElement} control bulk star button
 * @returns {Promise<void>} no return value
 */
async function starAllSaved(control) {
  const saved = state.interested || [];
  if (!saved.length) return;
  const login = state.status && state.status.authenticated_user;
  const confirmed = await confirmDialog({
    title: `Star ${pluralize(saved.length, "saved repository", "saved repositories")} on GitHub?`,
    body: `Each one is starred from your GitHub account${login ? ` (${login})` : ""} and moves from Saved to Starred. Starring stops at the first error, and anything starred before it stays starred.`,
    details: saved.map((repository) => repository.full_name),
    confirmLabel: `Star ${pluralize(saved.length, "repository", "repositories")}`,
  });
  if (!confirmed) return;
  await runBusy(control, "Starring...", async () => {
    try {
      const result = await api("/api/interested/star-all", { method: "POST" });
      Toasts.show({
        message: `Starred ${pluralize(result.starred_count, "repository", "repositories")} on GitHub.`,
        tone: "positive",
      });
    } catch (error) {
      Toasts.show({
        message: `Starring stopped: ${error.message} Repositories starred before the error moved to Starred.`,
        tone: "danger",
      });
    }
  });
  await refresh();
}

/**
 * removes every saved repository after confirmation
 * @param {HTMLButtonElement} control bulk remove button
 * @returns {Promise<void>} no return value
 */
async function clearSaved(control) {
  const saved = state.interested || [];
  if (!saved.length) return;
  const confirmed = await confirmDialog({
    title: `Remove all ${pluralize(saved.length, "saved repository", "saved repositories")}?`,
    body: "They stop counting toward your profile and can be recommended again. Nothing changes on GitHub. This can't be undone.",
    confirmLabel: "Remove all",
    tone: "danger",
  });
  if (!confirmed) return;
  await runBusy(control, "Removing...", async () => {
    try {
      const result = await api("/api/interested", { method: "DELETE" });
      Toasts.show({ message: `Removed ${pluralize(result.removed_count, "saved repository", "saved repositories")}.`, tone: "positive" });
    } catch (error) {
      Toasts.show({ message: `Couldn't remove saved repositories: ${error.message}`, tone: "danger" });
    }
  });
  await refresh("interested", "profile", "feedback");
}

/* ---------- preferences ---------- */

const preferenceFields = {
  languages: document.querySelector("#languages"),
  topics: document.querySelector("#topics"),
  keywords: document.querySelector("#keywords"),
};

/**
 * fills the manual interest fields unless there are unsaved edits
 * @returns {void} no return value
 */
function fillPreferenceFields() {
  if (!state.preferences || state.preferencesDirty) return;
  for (const [name, input] of Object.entries(preferenceFields)) input.value = (state.preferences[name] || []).join(", ");
}

/**
 * renders the preferences view from local state
 * @returns {void} no return value
 */
function renderPreferences() {
  fillPreferenceFields();
  const status = state.status || {};
  const profile = state.profile;
  const current = document.querySelector("#import-current");
  current.replaceChildren(
    ...(status.imported_username
      ? [
          h("span", { class: "badge badge--positive" }, icon("check"), "Imported"),
          h("span", { text: `${status.imported_username}, ${pluralize(status.imported_count, "repository", "repositories")}` }),
        ]
      : [h("span", { text: "No public profile imported yet." })]),
  );

  const weights = document.querySelector("#source-weights");
  if (profile && profile.source_weights) {
    const local = {
      starred: status.starred_count,
      interested: profile.interested_count,
      seed: profile.seed_count,
    };
    const rows = profile.source_weights.map((source) =>
      h(
        "tr",
        {},
        h("td", { text: source.label }),
        h("td", { class: "numeric", text: Number(source.weight).toFixed(2) }),
        h("td", { class: "numeric", text: source.source in local ? formatNumber(local[source.source] || 0) : "—" }),
      ),
    );
    const table = weights.closest("table");
    table.querySelector("tfoot")?.remove();
    table.append(
      h(
        "tfoot",
        {},
        h(
          "tr",
          {},
          h("td", {
            colspan: "3",
            text: `Archived and forked imported repositories add nothing. ${
              status.imported_username
                ? `GitProfileLens rows share the ${pluralize(status.imported_count, "repository", "repositories")} imported for ${status.imported_username}.`
                : ""
            }`.trim(),
          }),
        ),
      ),
    );
    weights.replaceChildren(...rows);
  }

  const summary = document.querySelector("#profile-summary");
  if (state.errors.profile) {
    summary.replaceChildren(stateBlock({ kind: "error", iconName: "circle-alert", title: "The profile didn't load", body: state.errors.profile }));
  } else if (profile) {
    summary.replaceChildren(
      h(
        "p",
        { class: "profile-facts" },
        h("span", {}, "Median stars of starred repositories ", h("strong", { text: formatNumber(Math.round(profile.median_stars)) })),
        h("span", {}, "Feedback records ", h("strong", { text: formatNumber(profile.feedback_count) })),
      ),
      profileHasSignals()
        ? h(
            "div",
            { class: "signal-grid" },
            signalSection("Languages", profile.languages),
            signalSection("Topics", profile.topics),
            signalSection("Keywords", profile.keywords),
          )
        : stateBlock({
            iconName: "sliders-horizontal",
            title: "No signals yet",
            body: "Add manual interests, import a public profile, or sync your stars to build a profile.",
            compact: true,
          }),
    );
  }
}

/**
 * saves manual interests
 * @param {SubmitEvent} event form submission
 * @returns {Promise<void>} no return value
 */
async function savePreferences(event) {
  event.preventDefault();
  const formState = document.querySelector("#preferences-state");
  const payload = {
    languages: splitValues(preferenceFields.languages.value),
    topics: splitValues(preferenceFields.topics.value),
    keywords: splitValues(preferenceFields.keywords.value),
  };
  await runBusy(document.querySelector("#save-preferences"), "Saving...", async () => {
    try {
      state.preferences = await api("/api/preferences", { method: "POST", body: JSON.stringify(payload) });
      state.preferencesDirty = false;
      formState.textContent = "";
      Toasts.show({ message: "Interests saved. New searches use them right away.", tone: "positive" });
    } catch (error) {
      formState.textContent = `Couldn't save: ${error.message}`;
      formState.dataset.tone = "danger";
    }
  });
  await refresh("status", "profile");
}

/**
 * imports a public GitProfileLens profile
 * @param {SubmitEvent} event form submission
 * @returns {Promise<void>} no return value
 */
async function importProfile(event) {
  event.preventDefault();
  const input = document.querySelector("#import-username");
  const message = document.querySelector("#import-message");
  const username = input.value.trim();
  const invalid = !username
    ? "Enter a GitHub username."
    : /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/.test(username)
      ? ""
      : "GitHub usernames use letters, numbers, and single hyphens, up to 39 characters.";
  if (!setFieldError(input, invalid)) {
    input.focus();
    return;
  }
  message.dataset.tone = "neutral";
  message.textContent = `Importing the public profile for ${username}...`;
  await runBusy(document.querySelector("#import-submit"), "Importing...", async () => {
    try {
      const result = await api("/api/import-profile", { method: "POST", body: JSON.stringify({ username }) });
      message.dataset.tone = "positive";
      message.textContent = `Imported ${pluralize(result.repository_count, "repository", "repositories")}: ${result.pinned_count} pinned, ${pluralize(result.language_count, "language")}, ${pluralize(result.topic_count, "topic")}.`;
      Toasts.show({ message: `Imported the public profile for ${result.username}.`, tone: "positive" });
    } catch (error) {
      message.dataset.tone = "danger";
      message.textContent = `Import failed: ${error.message}`;
    }
  });
  await refresh("status", "profile");
}

/* ---------- feedback history ---------- */

/**
 * renders the feedback history filter
 * @param {object} counts records per classification
 * @param {number} total all records
 * @returns {void} no return value
 */
function renderHistoryFilter(counts, total) {
  const container = document.querySelector("#history-filter");
  const options = [["all", "All", total], ...CLASSIFICATION_ORDER.map((name) => [name, CLASSIFICATIONS[name].label, counts[name] || 0])];
  container.replaceChildren(
    ...options.map(([value, label, count]) =>
      h(
        "label",
        { class: "segment" },
        h("input", {
          type: "radio",
          name: "history-filter",
          value,
          checked: state.historyFilter === value,
          onChange: () => {
            state.historyFilter = value;
            interfaceStore.write({ historyFilter: value });
            renderHistory();
            document.querySelector(`input[name="history-filter"][value="${CSS.escape(value)}"]`)?.focus();
          },
        }),
        h("span", { class: "segment-label", text: label }),
        h("span", { class: "segment-count", text: formatNumber(count) }),
      ),
    ),
  );
}

/**
 * builds one feedback history row
 * @param {object} record feedback record
 * @returns {HTMLElement} history row
 */
function historyRow(record) {
  const meta = CLASSIFICATIONS[record.classification] || { label: record.classification, clearBody: "" };
  const clear = button({
    label: "Clear",
    icon: "rotate-ccw",
    variant: "ghost",
    size: "sm",
    ariaLabel: `Clear the ${meta.label} record for ${record.repository}`,
  });
  clear.addEventListener("click", () => clearFeedback(record, clear));
  return h("li", { class: "history-row" }, h("span", { class: "entry-title" }, repositoryLink(record.repository)), clear);
}

/**
 * renders feedback history grouped by classification
 * @returns {void} no return value
 */
function renderHistory() {
  const container = document.querySelector("#feedback-records");
  if (state.errors.feedback) {
    container.replaceChildren(stateBlock({ kind: "error", iconName: "circle-alert", title: "Feedback history didn't load", body: state.errors.feedback }));
    return;
  }
  if (!state.feedback) {
    container.replaceChildren(h("p", { class: "inline-loading", role: "status", text: "Loading..." }));
    return;
  }
  const counts = {};
  for (const record of state.feedback) counts[record.classification] = (counts[record.classification] || 0) + 1;
  renderHistoryFilter(counts, state.feedback.length);
  if (!state.feedback.length) {
    container.replaceChildren(
      stateBlock({
        iconName: "history",
        title: "No feedback yet",
        body: "Save, dismiss, block, or star repositories in Discover and each decision is listed here, where you can clear it.",
        actions: [linkButton({ label: "Open Discover", href: "#discover", icon: "compass", size: "sm" })],
      }),
    );
    return;
  }
  const shown = state.historyFilter === "all" ? CLASSIFICATION_ORDER : [state.historyFilter];
  const groups = shown.flatMap((classification) => {
    const records = state.feedback.filter((record) => record.classification === classification);
    if (!records.length) {
      return state.historyFilter === "all"
        ? []
        : [stateBlock({ iconName: "inbox", title: `No ${CLASSIFICATIONS[classification].label.toLowerCase()} records`, compact: true })];
    }
    const meta = CLASSIFICATIONS[classification];
    const titleId = `history-${classification.replace(/\s+/g, "-")}`;
    const list = h("div", {});
    renderCollection(
      list,
      records,
      historyRow,
      Boolean(state.expanded.history[classification]),
      () => {
        state.expanded.history[classification] = !state.expanded.history[classification];
        renderHistory();
      },
      { className: "history-list", tag: "ul" },
    );
    return [
      h(
        "section",
        { "aria-labelledby": titleId },
        h(
          "div",
          { class: "history-group-head" },
          h("h2", { id: titleId }, h("span", { class: `badge badge--${meta.tone}` }, icon(meta.icon), meta.label), h("span", { class: "nav-count", text: formatNumber(records.length) })),
          h("p", { text: meta.description }),
        ),
        list,
      ),
    ];
  });
  container.replaceChildren(...groups);
}

/**
 * clears one local feedback record after confirmation
 * @param {object} record feedback record
 * @param {HTMLButtonElement} control clear button
 * @returns {Promise<void>} no return value
 */
async function clearFeedback(record, control) {
  const meta = CLASSIFICATIONS[record.classification] || { label: record.classification, clearBody: "" };
  const confirmed = await confirmDialog({
    title: `Clear the ${meta.label.toLowerCase()} record for ${record.repository}?`,
    body: meta.clearBody,
    confirmLabel: "Clear record",
    tone: record.classification === "interested" ? "danger" : "primary",
  });
  if (!confirmed) return;
  const cleared = await runBusy(control, "Clearing...", async () => {
    try {
      await api(`/api/feedback/${repositoryPath(record.repository)}`, { method: "DELETE" });
      return true;
    } catch (error) {
      Toasts.show({ message: `Couldn't clear ${record.repository}: ${error.message}`, tone: "danger" });
      return false;
    }
  });
  if (!cleared) return;
  Toasts.show({ message: `Cleared the ${meta.label.toLowerCase()} record for ${record.repository}.`, tone: "positive" });
  await refresh("interested", "profile", "feedback");
  document.querySelector("#history-title").focus();
}

/* ---------- wiring ---------- */

/**
 * restores controls and session results saved by this browser
 * @returns {void} no return value
 */
function restoreInterfaceState() {
  const saved = interfaceStore.read();
  writeDiscoverControls({ ...DISCOVER_DEFAULTS, ...(saved.discover || {}) });
  writeContributeControls({ ...CONTRIBUTE_DEFAULTS, ...(saved.contribute || {}) });
  const filters = document.querySelector("#discover-filters");
  filters.open = saved.discoverFiltersOpen ?? !phoneLayout.matches;
  if (saved.historyFilter && (saved.historyFilter === "all" || CLASSIFICATIONS[saved.historyFilter])) {
    state.historyFilter = saved.historyFilter;
  }
  state.libraryTab = LIBRARY_SORTS[saved.libraryTab] ? saved.libraryTab : "saved";
  const results = resultStore.read();
  if (results.discover && Array.isArray(results.discover.results)) {
    Object.assign(state.discover, results.discover, { restored: true });
  }
  if (results.contribute && Array.isArray(results.contribute.results)) {
    Object.assign(state.contribute, results.contribute, { restored: true });
  }
  persistDiscoverControls();
  persistContributeControls();
}

document.querySelector(".skip-link").addEventListener("click", (event) => {
  event.preventDefault();
  elements.main.focus();
});
window.addEventListener("hashchange", () => showRoute(true));
elements.navToggle.addEventListener("click", () =>
  elements.sidebar.classList.contains("is-open") ? closeNavigation() : openNavigation(),
);
document.querySelector("#nav-close").addEventListener("click", () => closeNavigation());
elements.scrim.addEventListener("click", () => closeNavigation());
phoneLayout.addEventListener("change", () => closeNavigation(false));
for (const link of document.querySelectorAll(".nav-link, .brand")) {
  link.addEventListener("click", () => {
    // selecting the current section still closes the drawer and returns to the heading
    if (resolveRoute() && link.getAttribute("href") === window.location.hash) showRoute(true);
  });
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && elements.sidebar.classList.contains("is-open")) {
    closeNavigation();
    return;
  }
  const target = event.target;
  const typing = target instanceof HTMLElement && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
  if (event.key === "/" && !typing && !event.ctrlKey && !event.metaKey && !event.altKey && !document.querySelector("dialog[open]")) {
    event.preventDefault();
    if (resolveRoute()?.route === "discover") {
      discoverFields.search.focus();
    } else {
      focusAfterRoute = "#filter-search";
      window.location.hash = "discover";
    }
  }
});

document.querySelector("#sync").addEventListener("click", (event) => syncStars(event.currentTarget));

discoverForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runDiscover();
});
discoverForm.addEventListener("input", (event) => {
  if (event.target === discoverFields.search) {
    window.clearTimeout(resolutionTimer);
    resolutionTimer = window.setTimeout(updateResolution, 180);
  } else if (event.target instanceof HTMLInputElement && event.target.getAttribute("aria-invalid") === "true") {
    setFieldError(event.target, "");
  }
  persistDiscoverControls();
});
document.querySelector("#discover-filters").addEventListener("toggle", (event) => {
  interfaceStore.write({ discoverFiltersOpen: event.currentTarget.open });
});
document.querySelector("#discover-reset").addEventListener("click", () => {
  writeDiscoverControls({ ...DISCOVER_DEFAULTS, search: discoverFields.search.value });
  for (const input of [discoverFields.min_stars, discoverFields.max_stars, discoverFields.limit]) setFieldError(input, "");
  persistDiscoverControls();
  Toasts.show({ message: "Filters reset. The search text was kept.", tone: "info" });
});

contributeForm.addEventListener("submit", (event) => {
  event.preventDefault();
  runContributions();
});
contributeForm.addEventListener("change", () => {
  persistContributeControls();
  renderContributeResults();
});
contributeForm.addEventListener("input", (event) => {
  if (event.target.id === "contribution-limit") setFieldError(event.target, "");
  persistContributeControls();
});
document.querySelector("#contribute-reset").addEventListener("click", () => {
  writeContributeControls(CONTRIBUTE_DEFAULTS);
  setFieldError(document.querySelector("#contribution-limit"), "");
  persistContributeControls();
  renderContributeResults();
  Toasts.show({ message: "Issue filters reset to their defaults.", tone: "info" });
});

for (const tab of document.querySelectorAll(".tab[data-tab]")) {
  tab.addEventListener("click", () => selectLibraryTab(tab.dataset.tab, false));
  tab.addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll(".tab[data-tab]")];
    const index = tabs.indexOf(tab);
    const next = { ArrowRight: 1, ArrowLeft: -1 }[event.key];
    if (next) {
      event.preventDefault();
      selectLibraryTab(tabs[(index + next + tabs.length) % tabs.length].dataset.tab, true);
    } else if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      selectLibraryTab(tabs[event.key === "Home" ? 0 : tabs.length - 1].dataset.tab, true);
    }
  });
}
document.querySelector("#library-filter").addEventListener("input", () => renderLibrary());
document.querySelector("#library-sort").addEventListener("change", (event) => {
  const sorts = interfaceStore.read().librarySort || {};
  interfaceStore.write({ librarySort: { ...sorts, [state.libraryTab]: event.currentTarget.value } });
  renderLibrary();
});
document.querySelector("#star-all-saved").addEventListener("click", (event) => starAllSaved(event.currentTarget));
document.querySelector("#clear-saved").addEventListener("click", (event) => clearSaved(event.currentTarget));

document.querySelector("#preferences-form").addEventListener("submit", savePreferences);
document.querySelector("#preferences-form").addEventListener("input", () => {
  state.preferencesDirty = true;
  const formState = document.querySelector("#preferences-state");
  formState.textContent = "Unsaved changes";
  formState.dataset.tone = "neutral";
});
document.querySelector("#import-form").addEventListener("submit", importProfile);
document.querySelector("#import-username").addEventListener("input", (event) => setFieldError(event.currentTarget, ""));
document.querySelector("#reload-profile").addEventListener("click", (event) =>
  runBusy(event.currentTarget, "Recalculating...", () => refresh("profile")),
);

restoreInterfaceState();
selectLibraryTab(state.libraryTab, false);
showRoute(false);
renderStatus();
updateResolution();
refresh(...LOCAL_RESOURCES, "preferences");
