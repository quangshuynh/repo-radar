const message = document.querySelector("#message");
const refreshButton = document.querySelector("#refresh");

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
  for (const classification of ["interested", "not interested", "starred", "blocked"]) {
    const button = document.createElement("button");
    button.textContent = classification;
    button.className = classification === "interested" ? "positive" : classification === "blocked" ? "blocked" : "negative";
    button.addEventListener("click", () => submitFeedback(repository.full_name, classification, card));
    actions.appendChild(button);
  }
  card.append(head, description, meta, why, topics, actions);
  return card;
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
 * @param {string} repository repository full name
 * @param {string} classification feedback classification
 * @param {HTMLElement} card recommendation card
 * @returns {Promise<void>} no return value
 */
async function submitFeedback(repository, classification, card) {
  await api("/api/feedback", { method: "POST", body: JSON.stringify({ repository, classification }) });
  showMessage(`Saved ${classification} for ${repository}`);
  if (["not interested", "starred", "blocked"].includes(classification)) card.remove();
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
  await Promise.all([loadStatus(), loadProfile()]);
  showMessage(`Cached ${result.starred_count} starred repositories`);
}));
refreshButton.addEventListener("click", () => handle(loadRecommendations));
document.querySelector("#reload-profile").addEventListener("click", () => handle(loadProfile));
document.querySelector("#preferences-form").addEventListener("submit", (event) => handle(async () => {
  event.preventDefault();
  const payload = { languages: splitValues(document.querySelector("#languages").value), topics: splitValues(document.querySelector("#topics").value), keywords: splitValues(document.querySelector("#keywords").value) };
  await api("/api/preferences", { method: "POST", body: JSON.stringify(payload) });
  await Promise.all([loadStatus(), loadProfile()]);
}, "Preferences saved"));
document.querySelector("#import-form").addEventListener("submit", (event) => handle(async () => {
  event.preventDefault();
  const username = document.querySelector("#import-username").value;
  const result = await api("/api/import-profile", { method: "POST", body: JSON.stringify({ username }) });
  await Promise.all([loadStatus(), loadProfile()]);
  showMessage(`Imported ${result.repository_count} repositories, ${result.pinned_count} pinned, ${result.language_count} languages, ${result.topic_count} topics`);
}));

handle(async () => {
  const preferences = await api("/api/preferences");
  document.querySelector("#languages").value = preferences.languages.join(", ");
  document.querySelector("#topics").value = preferences.topics.join(", ");
  document.querySelector("#keywords").value = preferences.keywords.join(", ");
  await Promise.all([loadStatus(), loadProfile()]);
});
