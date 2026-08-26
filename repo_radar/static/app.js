const message = document.querySelector("#message");
const refreshButton = document.querySelector("#refresh");
const COLLAPSED_RESULT_LIMIT = 6;
const expandedLibraries = {
  interested: false,
  starred: false,
  feedbackInterested: false,
  feedbackStarred: false,
};

/**
 * creates a control for expanding or collapsing a result collection
 * @param {number} total total number of available results
 * @param {boolean} expanded whether every result is visible
 * @param {Function} toggle callback that changes the expanded state
 * @returns {HTMLButtonElement} expand or collapse control
 */
function collectionToggle(total, expanded, toggle) {
  const button = document.createElement("button");
  button.className = "secondary collection-toggle";
  button.type = "button";
  button.setAttribute("aria-expanded", String(expanded));
  button.textContent = expanded ? "See less" : `See more (${total - COLLAPSED_RESULT_LIMIT})`;
  button.addEventListener("click", toggle);
  return button;
}

/**
 * renders a collection with an optional expand or collapse control
 * @param {HTMLElement} container collection container
 * @param {Array<object>} items available collection items
 * @param {Function} renderItem function that creates one item element
 * @param {boolean} expanded whether every item is visible
 * @param {Function} toggle callback that changes the expanded state
 * @returns {void} no return value
 */
function renderCollection(container, items, renderItem, expanded, toggle) {
  const visible = expanded ? items : items.slice(0, COLLAPSED_RESULT_LIMIT);
  const elements = visible.map(renderItem);
  if (items.length > COLLAPSED_RESULT_LIMIT) {
    elements.push(collectionToggle(items.length, expanded, toggle));
  }
  container.replaceChildren(...elements);
}

/**
 * requests json from the local api
 * @param {string} url local api url
 * @param {RequestInit} options fetch options
 * @returns {Promise<object>} decoded response
 */
async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json" }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || "Request failed");
  return data;
}

/**
 * displays one user facing status message
 * @param {string} text message text
 * @param {boolean} error whether the message represents an error
 * @returns {void} no return value
 */
function showMessage(text, error = false) {
  message.textContent = text;
  message.classList.toggle("error", error);
}

/**
 * displays GitProfileLens import feedback inside the preferences card
 * @param {string} text message text
 * @param {boolean} error whether the message represents an error
 * @returns {void} no return value
 */
function showImportMessage(text, error = false) {
  const importMessage = document.querySelector("#import-message");
  importMessage.textContent = text;
  importMessage.classList.toggle("error", error);
}

/**
 * displays action feedback inside a repository card
 * @param {HTMLElement} card repository card or row
 * @param {string} text message text
 * @param {boolean} error whether the message represents an error
 * @returns {void} no return value
 */
function showCardMessage(card, text, error = false) {
  let cardMessage = card.querySelector(".card-message");
  if (!cardMessage) {
    cardMessage = document.createElement("p");
    cardMessage.className = "card-message";
    cardMessage.setAttribute("role", "status");
    cardMessage.setAttribute("aria-live", "polite");
    card.appendChild(cardMessage);
  }
  cardMessage.textContent = text;
  cardMessage.classList.toggle("error", error);
}

/**
 * converts comma separated text into values
 * @param {string} value comma separated input
 * @returns {Array<string>} cleaned values
 */
function splitValues(value) {
  return value.split(",").map((item) => item.trim()).filter(Boolean);
}

/**
 * loads local status data
 * @returns {Promise<void>} no return value
 */
async function loadStatus() {
  const status = await api("/api/status");
  document.querySelector("#status-content").textContent =
    `User: ${status.authenticated_user || "not synced"} | Stars: ${status.starred_count} | ` +
    `Imported: ${status.imported_count} | Last sync: ${status.last_sync || "never"}`;
}

/**
 * creates one profile signal section
 * @param {string} title section title
 * @param {object} values normalized signal values
 * @returns {HTMLElement} signal section
 */
function signalSection(title, values) {
  const section = document.createElement("section");
  const heading = document.createElement("h3");
  heading.textContent = title;
  section.appendChild(heading);
  for (const [name, score] of Object.entries(values).slice(0, 10)) {
    const row = document.createElement("div");
    const label = document.createElement("span");
    const bar = document.createElement("span");
    const fill = document.createElement("span");
    const value = document.createElement("span");
    row.className = "signal-row";
    bar.className = "bar";
    label.textContent = name;
    fill.style.width = `${score * 100}%`;
    value.textContent = score.toFixed(2);
    bar.appendChild(fill);
    row.append(label, bar, value);
    section.appendChild(row);
  }
  if (!Object.keys(values).length) section.append("No signals yet");
  return section;
}

/**
 * loads and renders the combined profile
 * @returns {Promise<void>} no return value
 */
async function loadProfile() {
  const profile = await api("/api/profile");
  const container = document.querySelector("#profile-summary");
  const summary = document.createElement("p");
  const signals = document.createElement("div");
  summary.className = "meta";
  summary.textContent = `Stars: ${profile.starred_count} | Imported: ${profile.imported_count} | Seeds: ${profile.seed_count} | Feedback: ${profile.feedback_count} | Median stars: ${profile.median_stars}`;
  signals.className = "signal-grid";
  signals.append(signalSection("Languages", profile.languages), signalSection("Topics", profile.topics), signalSection("Keywords", profile.keywords));
  container.replaceChildren(summary, signals);
}

/**
 * creates one recommendation card
 * @param {object} repository recommendation response item
 * @returns {HTMLElement} recommendation card
 */
function recommendationCard(repository) {
  const card = document.createElement("article");
  const head = document.createElement("div");
  const title = document.createElement("h3");
  const link = document.createElement("a");
  const score = document.createElement("strong");
  const description = document.createElement("p");
  const meta = document.createElement("p");
  const why = document.createElement("p");
  const topics = document.createElement("div");
  const actions = document.createElement("div");
  card.className = "card";
  head.className = "card-head";
  link.href = repository.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = repository.full_name;
  title.appendChild(link);
  score.textContent = `${Math.round(repository.score * 100)}%`;
  score.className = "score";
  head.append(title, score);
  description.textContent = repository.description || "No description provided";
  meta.className = "meta";
  meta.textContent = `${repository.language || "Unknown language"} | ${repository.stars} stars`;
  why.className = "why";
  why.textContent = `Why it fits: ${repository.explanation}`;
  topics.className = "topics";
  for (const value of repository.topics) {
    const topic = document.createElement("span");
    topic.className = "topic";
    topic.textContent = value;
    topics.appendChild(topic);
  }
  actions.className = "actions";
  for (const classification of ["interested", "not interested", "blocked"]) {
    const button = document.createElement("button");
    button.textContent = classification === "not interested" ? "Not for me" : classification;
    button.className = classification === "interested" ? "positive" : classification === "blocked" ? "blocked" : "negative";
    button.addEventListener("click", () => handle(() => submitFeedback(repository, classification, card)));
    actions.appendChild(button);
  }
  const starButton = document.createElement("button");
  starButton.className = "star-action";
  starButton.textContent = "Star on GitHub";
  starButton.addEventListener("click", () => handle(() => starRepository(repository, card, starButton)));
  actions.appendChild(starButton);
  card.append(head, description, meta, why, topics, actions);
  return card;
}

const CONTRIBUTION_SOURCES = {
  saved: "from your saved list",
  starred: "from your starred library",
  new: "new to you",
};

/**
 * creates one contribution opportunity card
 * @param {object} contribution contribution response item
 * @returns {HTMLElement} contribution card
 */
function contributionCard(contribution) {
  const card = document.createElement("article");
  const head = document.createElement("div");
  const title = document.createElement("h3");
  const link = document.createElement("a");
  const score = document.createElement("strong");
  const meta = document.createElement("p");
  const labels = document.createElement("div");
  const why = document.createElement("div");
  const whyTitle = document.createElement("p");
  const reasons = document.createElement("ul");
  const scope = document.createElement("p");
  card.className = "card";
  head.className = "card-head";
  link.href = contribution.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = `${contribution.repository}#${contribution.number}`;
  title.appendChild(link);
  score.textContent = `${Math.round(contribution.score * 100)}%`;
  score.className = "score";
  head.append(title, score);
  const issueTitle = document.createElement("p");
  issueTitle.className = "issue-title";
  issueTitle.textContent = contribution.title;
  meta.className = "meta";
  const assignment = contribution.assignee_count ? `assigned to ${contribution.assignee_count}` : "unassigned";
  const origin = CONTRIBUTION_SOURCES[contribution.source] || contribution.source;
  meta.textContent = `${contribution.language || "Unknown language"} | ${assignment} | ${contribution.comments} comments | ${origin}`;
  labels.className = "topics";
  for (const value of contribution.labels) {
    const label = document.createElement("span");
    label.className = "topic";
    label.textContent = value;
    labels.appendChild(label);
  }
  why.className = "why";
  whyTitle.className = "why-title";
  whyTitle.textContent = "Why recommended";
  reasons.className = "reasons";
  for (const reason of contribution.reasons) {
    const item = document.createElement("li");
    item.textContent = reason;
    reasons.appendChild(item);
  }
  why.append(whyTitle, reasons);
  scope.className = "scope-signal";
  scope.textContent = `Scope signal: ${contribution.scope_signal}`;
  if (contribution.scope_evidence.length) {
    scope.title = contribution.scope_evidence.join(" | ");
  }
  card.append(head, issueTitle, meta, labels, why, scope);
  return card;
}

/**
 * loads ranked contribution opportunities
 * @returns {Promise<void>} no return value
 */
async function loadContributions() {
  const button = document.querySelector("#refresh-contributions");
  const container = document.querySelector("#contributions");
  const warning = document.querySelector("#contribution-warning");
  const selectedScope = document.querySelector('input[name="contribution-scope"]:checked');
  const parameters = new URLSearchParams({
    limit: document.querySelector("#contribution-limit").value,
    unassigned_only: document.querySelector("#contribution-unassigned").checked,
    scope: selectedScope ? selectedScope.value : "discover",
  });
  button.disabled = true;
  button.textContent = "Reading open issues...";
  warning.textContent = "";
  try {
    const data = await api(`/api/contributions?${parameters}`);
    container.replaceChildren(...data.contributions.map(contributionCard));
    if (!data.contributions.length) {
      const empty = document.createElement("div");
      const heading = document.createElement("p");
      const detail = document.createElement("p");
      empty.className = "recommendation-empty";
      heading.className = "empty-title";
      heading.textContent = "No open issues stood out.";
      detail.className = "empty";
      detail.textContent =
        data.message ||
        (data.scope === "saved_starred"
          ? "Try the Discover scope, or save and star a few more repositories."
          : "Add a few more interests to your profile and try again.");
      empty.append(heading, detail);
      container.replaceChildren(empty);
    }
    if (data.warning) {
      warning.textContent = `Partial results. GitHub issue search stopped early: ${data.warning}`;
      warning.classList.add("error");
    } else {
      warning.classList.remove("error");
    }
  } finally {
    button.disabled = false;
    button.textContent = "Find an issue to work on";
  }
}

/**
 * creates a request payload from repository metadata
 * @param {object} repository repository metadata
 * @returns {object} repository action payload
 */
function repositoryPayload(repository) {
  return {
    repository: repository.full_name,
    description: repository.description,
    language: repository.language,
    topics: repository.topics,
    stars: repository.stars,
    url: repository.url,
  };
}

/**
 * creates one saved repository row
 * @param {object} repository saved repository metadata
 * @returns {HTMLElement} saved repository row
 */
function savedRepository(repository) {
  const row = document.createElement("article");
  const content = document.createElement("div");
  const link = document.createElement("a");
  const description = document.createElement("p");
  const meta = document.createElement("p");
  const influence = document.createElement("p");
  const removeButton = document.createElement("button");
  row.className = "saved-repository";
  link.href = repository.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = repository.full_name;
  description.textContent = repository.description || "No description provided";
  meta.className = "meta";
  meta.textContent = `${repository.language || "Unknown language"} | ${repository.stars} stars`;
  influence.className = "influence";
  influence.textContent = `Profile influence ${repository.preference_weight.toFixed(2)} | ${repository.signal_count} signals`;
  content.append(link, description, meta, influence);
  removeButton.className = "saved-remove";
  removeButton.type = "button";
  removeButton.title = `Remove ${repository.full_name} from saved`;
  removeButton.setAttribute("aria-label", `Remove ${repository.full_name} from saved`);
  removeButton.textContent = "×";
  removeButton.addEventListener("click", () => handle(() => removeInterested(repository, row)));
  const starButton = document.createElement("button");
  starButton.className = "star-action";
  starButton.textContent = "Star on GitHub";
  starButton.addEventListener("click", () => handle(() => starRepository(repository, row, starButton)));
  row.append(content, starButton, removeButton);
  return row;
}

/**
 * creates one cached starred repository row
 * @param {object} repository starred repository metadata
 * @returns {HTMLElement} starred repository row
 */
function starredRepository(repository) {
  const row = document.createElement("article");
  const link = document.createElement("a");
  const description = document.createElement("p");
  const meta = document.createElement("p");
  row.className = "library-repository";
  link.href = repository.url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = repository.full_name;
  description.textContent = repository.description || "No description provided";
  meta.className = "meta";
  meta.textContent = `${repository.language || "Unknown language"} | ${repository.stars} stars`;
  row.append(link, description, meta);
  return row;
}

/**
 * loads cached GitHub starred repositories
 * @returns {Promise<void>} no return value
 */
async function loadStarred() {
  const data = await api("/api/starred");
  const container = document.querySelector("#starred-repositories");
  if (!data.repositories.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No starred repositories are cached yet. Run a sync to bring them in.";
    container.replaceChildren(empty);
    return;
  }
  renderCollection(container, data.repositories, starredRepository, expandedLibraries.starred, () => {
    expandedLibraries.starred = !expandedLibraries.starred;
    loadStarred();
  });
}

/**
 * creates one feedback history row
 * @param {object} record feedback record
 * @returns {HTMLElement} feedback row
 */
function feedbackRecord(record) {
  const row = document.createElement("div");
  const repository = document.createElement("span");
  const button = document.createElement("button");
  row.className = "feedback-record";
  repository.textContent = record.repository;
  button.className = "secondary";
  button.textContent = "Undo";
  button.addEventListener("click", () => handle(() => removeFeedback(record, row)));
  row.append(repository, button);
  return row;
}

/**
 * loads and groups local feedback history
 * @returns {Promise<void>} no return value
 */
async function loadFeedback() {
  const data = await api("/api/feedback");
  const container = document.querySelector("#feedback-records");
  if (!data.records.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "No feedback records yet.";
    container.replaceChildren(empty);
    return;
  }
  const classifications = ["interested", "starred", "blocked", "not interested"];
  const groups = classifications.flatMap((classification) => {
    const records = data.records.filter((record) => record.classification === classification);
    if (!records.length) return [];
    const section = document.createElement("section");
    const heading = document.createElement("h3");
    const stateKey = classification === "interested" ? "feedbackInterested" : "feedbackStarred";
    const isExpandable = classification === "interested" || classification === "starred";
    const expanded = isExpandable && expandedLibraries[stateKey];
    const visibleRecords = isExpandable && !expanded ? records.slice(0, COLLAPSED_RESULT_LIMIT) : records;
    heading.textContent = `${classification} (${records.length})`;
    section.append(heading, ...visibleRecords.map(feedbackRecord));
    if (isExpandable && records.length > COLLAPSED_RESULT_LIMIT) {
      section.append(collectionToggle(records.length, expanded, () => {
        expandedLibraries[stateKey] = !expandedLibraries[stateKey];
        loadFeedback();
      }));
    }
    return [section];
  });
  container.replaceChildren(...groups);
}

/**
 * clears one local feedback record after confirmation
 * @param {object} record feedback record
 * @param {HTMLElement} row feedback row
 * @returns {Promise<void>} no return value
 */
async function removeFeedback(record, row) {
  if (!window.confirm(`Undo ${record.classification} feedback for ${record.repository}?`)) return;
  const [owner, name] = record.repository.split("/", 2).map(encodeURIComponent);
  await api(`/api/feedback/${owner}/${name}`, { method: "DELETE" });
  row.remove();
  await Promise.all([loadFeedback(), loadInterested(), loadProfile()]);
  showMessage(`Cleared feedback for ${record.repository}`);
}

/**
 * removes one repository from the saved list after confirmation
 * @param {object} repository saved repository metadata
 * @param {HTMLElement} row saved repository row
 * @returns {Promise<void>} no return value
 */
async function removeInterested(repository, row) {
  if (!window.confirm(`Remove ${repository.full_name} from saved repositories?`)) return;
  const [owner, name] = repository.full_name.split("/", 2).map(encodeURIComponent);
  await api(`/api/interested/${owner}/${name}`, { method: "DELETE" });
  row.remove();
  await Promise.all([loadInterested(), loadProfile(), loadFeedback()]);
  showMessage(`Removed ${repository.full_name} from saved`);
}

/**
 * removes every repository from the saved list after confirmation
 * @returns {Promise<void>} no return value
 */
async function clearInterested() {
  if (!window.confirm("Remove every repository from your saved list? This cannot be undone.")) return;
  const result = await api("/api/interested", { method: "DELETE" });
  await Promise.all([loadInterested(), loadProfile(), loadFeedback()]);
  showMessage(`Removed ${result.removed_count} saved repositories`);
}

/**
 * stars every saved repository after confirmation
 * @returns {Promise<void>} no return value
 */
async function starAllInterested() {
  if (!window.confirm("Star every saved repository on GitHub?")) return;
  const button = document.querySelector("#star-all-saved");
  button.disabled = true;
  button.textContent = "Starring saved repos...";
  try {
    const result = await api("/api/interested/star-all", { method: "POST" });
    await Promise.all([loadInterested(), loadStarred(), loadStatus(), loadProfile(), loadFeedback()]);
    showMessage(`Starred ${result.starred_count} saved repositories on GitHub`);
  } finally {
    button.disabled = !document.querySelector(".saved-repository");
    button.textContent = "Star all on GitHub";
  }
}

/**
 * loads repositories saved for later
 * @returns {Promise<void>} no return value
 */
async function loadInterested() {
  const data = await api("/api/interested");
  const container = document.querySelector("#interested-repositories");
  document.querySelector("#star-all-saved").disabled = !data.repositories.length;
  document.querySelector("#clear-saved").disabled = !data.repositories.length;
  if (!data.repositories.length) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = "Nothing saved yet. Mark a recommendation as interested to keep it here.";
    container.replaceChildren(empty);
    return;
  }
  renderCollection(container, data.repositories, savedRepository, expandedLibraries.interested, () => {
    expandedLibraries.interested = !expandedLibraries.interested;
    loadInterested();
  });
}

/**
 * updates the recommendation loading experience
 * @param {boolean} isLoading whether recommendations are loading
 * @returns {void} no return value
 */
function setRecommendationLoading(isLoading) {
  refreshButton.disabled = isLoading;
  refreshButton.textContent = isLoading ? "Scanning GitHub..." : "Find something good";
  if (!isLoading) return;
  const container = document.querySelector("#recommendations");
  const loading = document.createElement("div");
  const heading = document.createElement("p");
  const detail = document.createElement("p");
  const track = document.createElement("div");
  const bar = document.createElement("span");
  loading.className = "recommendation-loading";
  heading.className = "loading-title";
  heading.textContent = "Looking beyond the obvious...";
  detail.className = "empty";
  detail.textContent = "Searching your strongest interests, comparing candidates, and making room for a few hidden gems.";
  track.className = "loading-track";
  track.setAttribute("role", "progressbar");
  track.setAttribute("aria-label", "Generating repository recommendations");
  bar.className = "loading-bar";
  track.appendChild(bar);
  loading.append(heading, detail, track);
  container.replaceChildren(loading);
}

/**
 * loads recommendations with active filters
 * @returns {Promise<void>} no return value
 */
async function loadRecommendations() {
  setRecommendationLoading(true);
  const parameters = new URLSearchParams({
    limit: document.querySelector("#filter-limit").value,
    language: document.querySelector("#filter-language").value,
    min_stars: document.querySelector("#filter-min").value,
    hidden_gems: document.querySelector("#filter-gems").checked,
  });
  const maximum = document.querySelector("#filter-max").value;
  if (maximum) parameters.set("max_stars", maximum);
  try {
    const data = await api(`/api/recommendations?${parameters}`);
    const container = document.querySelector("#recommendations");
    container.replaceChildren(...data.recommendations.map(recommendationCard));
    if (!data.recommendations.length) {
      const empty = document.createElement("div");
      const heading = document.createElement("p");
      const detail = document.createElement("p");
      empty.className = "recommendation-empty";
      heading.className = "empty-title";
      heading.textContent = "Nothing quite fits yet.";
      detail.className = "empty";
      detail.textContent = data.message || "Try broadening the filters or adding a few more interests to your profile.";
      empty.append(heading, detail);
      container.replaceChildren(empty);
    }
  } catch (error) {
    const container = document.querySelector("#recommendations");
    const empty = document.createElement("div");
    const heading = document.createElement("p");
    const detail = document.createElement("p");
    empty.className = "recommendation-empty";
    heading.className = "empty-title";
    heading.textContent = "The search hit a snag.";
    detail.className = "empty";
    detail.textContent = "Your profile is safe. Try searching again in a moment.";
    empty.append(heading, detail);
    container.replaceChildren(empty);
    throw error;
  } finally {
    setRecommendationLoading(false);
  }
}

/**
 * saves feedback for one repository
 * @param {object} repository repository metadata
 * @param {string} classification feedback classification
 * @param {HTMLElement} card recommendation card
 * @returns {Promise<void>} no return value
 */
async function submitFeedback(repository, classification, card) {
  const payload = { ...repositoryPayload(repository), classification };
  await api("/api/feedback", { method: "POST", body: JSON.stringify(payload) });
  if (classification === "interested") {
    await Promise.all([loadInterested(), loadProfile(), loadFeedback()]);
    showMessage(`Saved ${repository.full_name} for later and updated your profile`);
    card.querySelector(".positive").disabled = true;
    card.querySelector(".positive").textContent = "Saved";
    return;
  }
  await loadFeedback();
  showMessage(`Saved ${classification} for ${repository.full_name}`);
  card.remove();
}

/**
 * stars one repository through GitHub
 * @param {object} repository repository metadata
 * @param {HTMLElement} card repository card or row
 * @param {HTMLButtonElement} button star action button
 * @returns {Promise<void>} no return value
 */
async function starRepository(repository, card, button) {
  button.disabled = true;
  button.textContent = "Starring...";
  showCardMessage(card, "Asking GitHub to add this star...");
  try {
    await api("/api/star", { method: "POST", body: JSON.stringify(repositoryPayload(repository)) });
    await Promise.all([loadStatus(), loadProfile(), loadInterested(), loadStarred(), loadFeedback()]);
    showMessage(`Starred ${repository.full_name} on GitHub`);
    card.remove();
  } catch (error) {
    button.disabled = false;
    button.textContent = "Star on GitHub";
    showCardMessage(card, error.message, true);
  }
}

/**
 * handles an asynchronous interface action
 * @param {Function} action asynchronous action
 * @param {string} success success message
 * @returns {Promise<void>} no return value
 */
async function handle(action, success = "") {
  try {
    await action();
    if (success) showMessage(success);
  } catch (error) {
    showMessage(error.message, true);
  }
}

document.querySelector("#sync").addEventListener("click", () => handle(async () => {
  const result = await api("/api/sync", { method: "POST" });
  await Promise.all([loadStatus(), loadProfile(), loadInterested(), loadStarred(), loadFeedback()]);
  const reconciled = result.reconciled_count
    ? ` and removed ${result.reconciled_count} matching repositories from saved`
    : "";
  showMessage(`Cached ${result.starred_count} starred repositories${reconciled}`);
}));
refreshButton.addEventListener("click", () => handle(loadRecommendations));
document.querySelector("#refresh-contributions").addEventListener("click", () => handle(loadContributions));
document.querySelector("#reload-profile").addEventListener("click", () => handle(loadProfile));
document.querySelector("#reload-saved").addEventListener("click", () => handle(loadInterested));
document.querySelector("#reload-starred").addEventListener("click", () => handle(loadStarred));
document.querySelector("#reload-feedback").addEventListener("click", () => handle(loadFeedback));
document.querySelector("#clear-saved").addEventListener("click", () => handle(clearInterested));
document.querySelector("#star-all-saved").addEventListener("click", () => handle(starAllInterested));
document.querySelector("#preferences-form").addEventListener("submit", (event) => handle(async () => {
  event.preventDefault();
  const payload = { languages: splitValues(document.querySelector("#languages").value), topics: splitValues(document.querySelector("#topics").value), keywords: splitValues(document.querySelector("#keywords").value) };
  await api("/api/preferences", { method: "POST", body: JSON.stringify(payload) });
  await Promise.all([loadStatus(), loadProfile()]);
}, "Preferences saved"));
document.querySelector("#import-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  const username = document.querySelector("#import-username").value;
  button.disabled = true;
  button.textContent = "Importing...";
  showImportMessage(`Importing the public profile for ${username}...`);
  try {
    const result = await api("/api/import-profile", { method: "POST", body: JSON.stringify({ username }) });
    await Promise.all([loadStatus(), loadProfile()]);
    showImportMessage(`Imported ${result.repository_count} repositories, ${result.pinned_count} pinned, ${result.language_count} languages, and ${result.topic_count} topics`);
  } catch (error) {
    showImportMessage(error.message, true);
  } finally {
    button.disabled = false;
    button.textContent = "Import from GitProfileLens";
  }
});

handle(async () => {
  const preferences = await api("/api/preferences");
  document.querySelector("#languages").value = preferences.languages.join(", ");
  document.querySelector("#topics").value = preferences.topics.join(", ");
  document.querySelector("#keywords").value = preferences.keywords.join(", ");
  await Promise.all([loadStatus(), loadProfile(), loadInterested(), loadStarred(), loadFeedback()]);
});
