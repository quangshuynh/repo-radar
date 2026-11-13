/*
 * Repo Radar interface components.
 *
 * Small, framework-free builders shared by every view in app.js: elements, icons, buttons,
 * busy guards, toasts, the confirmation dialog, empty/error/loading states, and formatting.
 * Nothing here knows about the Repo Radar API; app.js owns data and behavior.
 */
"use strict";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const ICON_SPRITE = "/static/icons.svg";

/**
 * appends children, skipping empty values and flattening arrays
 * @param {Element} parent element receiving children
 * @param {Array<*>} children nodes, strings, numbers, or nested arrays
 * @returns {Element} the parent element
 */
function appendChildren(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false || child === "") continue;
    parent.append(child instanceof Node ? child : String(child));
  }
  return parent;
}

/**
 * creates an HTML element
 * @param {string} tag element name
 * @param {object} props attributes; `class`, `text`, `dataset`, `style`, and `on*` listeners are special
 * @param {...*} children child nodes or text
 * @returns {HTMLElement} created element
 */
function h(tag, props = {}, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") element.className = Array.isArray(value) ? value.filter(Boolean).join(" ") : value;
    else if (key === "text") element.textContent = value;
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key === "style") {
      for (const [property, setting] of Object.entries(value)) element.style.setProperty(property, setting);
    } else if (key.startsWith("on") && typeof value === "function") {
      element.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (value === true) element.setAttribute(key, "");
    else element.setAttribute(key, String(value));
  }
  return appendChildren(element, children);
}

/**
 * creates a sprite icon
 * @param {string} name Lucide icon name from icons.svg
 * @param {object} options optional accessible label and extra class
 * @returns {SVGSVGElement} icon element
 */
function icon(name, { label, className } = {}) {
  const svg = document.createElementNS(SVG_NAMESPACE, "svg");
  svg.setAttribute("class", className ? `icon ${className}` : "icon");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("focusable", "false");
  if (label) {
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", label);
  } else {
    svg.setAttribute("aria-hidden", "true");
  }
  const use = document.createElementNS(SVG_NAMESPACE, "use");
  use.setAttribute("href", `${ICON_SPRITE}#${name}`);
  svg.appendChild(use);
  return svg;
}

/**
 * creates a button using the shared button styles
 * @param {object} options label, icon, variant, size, listener, and accessibility attributes
 * @returns {HTMLButtonElement} button element
 */
function button({
  label,
  icon: iconName,
  variant = "secondary",
  size,
  type = "button",
  ariaLabel,
  className,
  iconOnly = false,
  onClick,
  attributes = {},
}) {
  const element = h("button", {
    type,
    class: ["button", `button--${variant}`, size && `button--${size}`, iconOnly && "button--icon", className],
    "aria-label": ariaLabel || (iconOnly ? label : undefined),
    ...attributes,
  });
  if (iconName) element.append(icon(iconName));
  if (!iconOnly) element.append(h("span", { class: "button-label", text: label }));
  if (onClick) element.addEventListener("click", onClick);
  return element;
}

/**
 * creates a link styled as a button
 * @param {object} options label, destination, icon, variant, size, and whether it leaves the app
 * @returns {HTMLAnchorElement} link element
 */
function linkButton({ label, href, icon: iconName, variant = "secondary", size, external = false, ariaLabel }) {
  const element = h("a", {
    class: ["button", `button--${variant}`, size && `button--${size}`],
    href,
    "aria-label": ariaLabel,
    target: external ? "_blank" : undefined,
    rel: external ? "noopener noreferrer" : undefined,
  });
  if (iconName) element.append(icon(iconName));
  element.append(h("span", { class: "button-label", text: label }));
  return element;
}

/**
 * replaces the visible label of a shared button
 * @param {HTMLElement} control button created with the shared markup
 * @param {string} text new label
 * @returns {void} no return value
 */
function setButtonLabel(control, text) {
  const label = control.querySelector(".button-label");
  if (label) label.textContent = text;
  else control.textContent = text;
}

/**
 * runs an action while a control shows a busy state, ignoring repeat activations
 *
 * The guard is what prevents duplicate submissions: a second click while the first request
 * is in flight returns immediately instead of sending another request.
 * @param {HTMLButtonElement} control button that started the action
 * @param {string} busyLabel label shown while the action runs
 * @param {Function} task asynchronous action
 * @returns {Promise<*>} task result, or undefined when the control was already busy
 */
async function runBusy(control, busyLabel, task) {
  if (!control) return task();
  if (control.getAttribute("aria-busy") === "true") return undefined;
  const label = control.querySelector(".button-label");
  const original = label ? label.textContent : null;
  const spinner = icon("loader-circle", { className: "spinner" });
  control.setAttribute("aria-busy", "true");
  control.disabled = true;
  control.prepend(spinner);
  if (label && busyLabel) label.textContent = busyLabel;
  try {
    return await task();
  } finally {
    spinner.remove();
    control.removeAttribute("aria-busy");
    control.disabled = false;
    if (label && original !== null) label.textContent = original;
  }
}

/**
 * builds an empty, error, or loading state
 * @param {object} options state kind, icon, title, body, actions, and compact layout
 * @returns {HTMLElement} state element
 */
function stateBlock({ kind = "empty", iconName = "inbox", title, body, actions = [], compact = false }) {
  const role = kind === "error" ? "alert" : kind === "loading" ? "status" : undefined;
  return h(
    "div",
    { class: ["state", `state--${kind}`, compact && "state--compact"], role },
    icon(iconName),
    h("p", { class: "state-title", text: title }),
    body ? h("p", { class: "state-body", text: body }) : null,
    actions.length ? h("div", { class: "state-actions" }, actions) : null,
  );
}

/**
 * builds a loading state with an indeterminate progress line and placeholder rows
 * @param {string} title what is happening
 * @param {string} body what the wait is for
 * @returns {HTMLElement} loading state
 */
function loadingBlock(title, body) {
  const block = stateBlock({ kind: "loading", iconName: "loader-circle", title, body });
  block.querySelector(".icon").classList.add("spinner");
  block.append(
    h("div", { class: "progress", "aria-hidden": "true" }),
    h("div", { class: "skeleton", "aria-hidden": "true" }, Array.from({ length: 6 }, () => h("span"))),
  );
  return block;
}

/**
 * builds a wrapping chip list that collapses long lists behind a "more" control
 * @param {Array<string>} values chip text
 * @param {number} visible number of chips shown before collapsing
 * @param {string} noun what the chips are, used in the expand control label
 * @returns {HTMLElement|null} chip list or null for an empty list
 */
function chipList(values, visible = 6, noun = "topics") {
  if (!values || !values.length) return null;
  const list = h("ul", { class: "chips" });
  const render = (expanded) => {
    const shown = expanded ? values : values.slice(0, visible);
    list.replaceChildren(...shown.map((value) => h("li", { class: "chip", text: value })));
    if (!expanded && values.length > visible) {
      const hidden = values.length - visible;
      list.append(
        h(
          "li",
          {},
          h("button", {
            type: "button",
            class: "chip chip-more",
            "aria-label": `Show ${hidden} more ${noun}`,
            text: `+${hidden}`,
            onClick: () => render(true),
          }),
        ),
      );
    }
  };
  render(false);
  return list;
}

/* ---------- formatting ---------- */

const compactNumber = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });
const plainNumber = new Intl.NumberFormat("en");
const relativeTime = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/**
 * formats a count compactly, such as 39.7K
 * @param {number} value count
 * @returns {string} formatted count
 */
function formatCount(value) {
  return compactNumber.format(Number(value) || 0);
}

/**
 * formats a count with grouping, such as 39,732
 * @param {number} value count
 * @returns {string} formatted count
 */
function formatNumber(value) {
  return plainNumber.format(Number(value) || 0);
}

/**
 * pluralizes a noun for a count
 * @param {number} count item count
 * @param {string} singular singular noun
 * @param {string} plural plural noun
 * @returns {string} count and noun
 */
function pluralize(count, singular, plural = `${singular}s`) {
  return `${formatNumber(count)} ${count === 1 ? singular : plural}`;
}

/**
 * describes a timestamp relative to now, such as "2 hours ago"
 * @param {string} value ISO timestamp
 * @param {number} now reference time in milliseconds
 * @returns {string|null} relative description or null for an unreadable timestamp
 */
function formatRelative(value, now = Date.now()) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return null;
  const seconds = Math.round((date.getTime() - now) / 1000);
  if (Math.abs(seconds) < 45) return "just now";
  const steps = [
    [60, "second"],
    [60, "minute"],
    [24, "hour"],
    [30, "day"],
    [12, "month"],
    [Infinity, "year"],
  ];
  let amount = seconds;
  for (const [size, unit] of steps) {
    if (Math.abs(amount) < size) return relativeTime.format(Math.round(amount), unit);
    amount /= size;
  }
  return null;
}

/**
 * formats a timestamp as a readable local date and time
 * @param {string} value ISO timestamp
 * @returns {string|null} formatted date or null for an unreadable timestamp
 */
function formatDateTime(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return null;
  return date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

/* ---------- browser storage, used only for per-browser conveniences ---------- */

/**
 * creates a JSON store over local or session storage that tolerates unavailable storage
 * @param {string} kind "local" or "session"
 * @param {string} key storage key
 * @returns {{read: Function, write: Function}} store accessors
 */
function browserStore(kind, key) {
  const area = () => (kind === "session" ? window.sessionStorage : window.localStorage);
  return {
    read() {
      try {
        return JSON.parse(area().getItem(key) || "{}") || {};
      } catch {
        return {};
      }
    },
    write(patch) {
      try {
        area().setItem(key, JSON.stringify({ ...this.read(), ...patch }));
      } catch {
        // storage can be full or blocked; every caller works without it
      }
    },
  };
}

/* ---------- toasts ---------- */

const Toasts = {
  limit: 4,

  /**
   * shows a transient notification, optionally with one action such as Undo
   * @param {object} options message, tone, action, and display time
   * @returns {HTMLElement} toast element
   */
  show({ message, tone = "info", action, duration }) {
    const container = document.querySelector("#toasts");
    const iconName = tone === "positive" ? "circle-check" : tone === "danger" ? "circle-alert" : "info";
    const toast = h("div", { class: ["toast", `toast--${tone}`], role: tone === "danger" ? "alert" : "status" });
    let timer = null;
    const dismiss = () => {
      window.clearTimeout(timer);
      toast.remove();
    };
    const schedule = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(dismiss, duration || (action ? 9000 : tone === "danger" ? 10000 : 5000));
    };
    toast.append(icon(iconName), h("p", { class: "toast-message", text: message }));
    if (action) {
      toast.append(
        button({
          label: action.label,
          variant: "ghost",
          size: "sm",
          onClick: async (event) => {
            event.currentTarget.disabled = true;
            dismiss();
            await action.onClick();
          },
        }),
      );
    }
    toast.append(button({ label: "Dismiss notification", icon: "x", variant: "ghost", size: "sm", iconOnly: true, onClick: dismiss }));
    // a toast never disappears while someone is reading or using it
    toast.addEventListener("pointerenter", () => window.clearTimeout(timer));
    toast.addEventListener("pointerleave", schedule);
    toast.addEventListener("focusin", () => window.clearTimeout(timer));
    toast.addEventListener("focusout", schedule);
    container.append(toast);
    while (container.children.length > this.limit) container.firstElementChild.remove();
    schedule();
    return toast;
  },
};

/* ---------- confirmation dialog ---------- */

/**
 * asks for confirmation in an accessible modal dialog
 *
 * Focus starts on Cancel, Escape and the backdrop both cancel, and focus returns to the
 * control that opened the dialog.
 * @param {object} options title, body, optional detail lines, confirm label, and tone
 * @returns {Promise<boolean>} whether the person confirmed
 */
function confirmDialog({ title, body, details = [], confirmLabel, tone = "primary" }) {
  const dialog = document.querySelector("#confirm-dialog");
  const accept = dialog.querySelector("#confirm-accept");
  const cancel = dialog.querySelector("#confirm-cancel");
  const list = dialog.querySelector("#confirm-details");
  const opener = document.activeElement;
  dialog.querySelector("#confirm-title").textContent = title;
  dialog.querySelector("#confirm-body").textContent = body;
  list.replaceChildren(...details.map((detail) => h("li", { text: detail })));
  list.hidden = !details.length;
  accept.textContent = confirmLabel;
  accept.className = `button button--${tone === "danger" ? "danger" : "primary"}`;
  return new Promise((resolve) => {
    const finish = (confirmed) => {
      accept.onclick = null;
      cancel.onclick = null;
      dialog.oncancel = null;
      dialog.onclick = null;
      if (dialog.open) dialog.close();
      if (opener && typeof opener.focus === "function" && document.contains(opener)) opener.focus();
      resolve(confirmed);
    };
    accept.onclick = () => finish(true);
    cancel.onclick = () => finish(false);
    dialog.oncancel = (event) => {
      event.preventDefault();
      finish(false);
    };
    dialog.onclick = (event) => {
      if (event.target === dialog) finish(false);
    };
    dialog.showModal();
    cancel.focus();
  });
}
