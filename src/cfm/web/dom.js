/* Minimal DOM helpers.
 *
 * Everything the API returns reaches the page as a text node: this module has
 * no `innerHTML` and neither does any caller, so a location name or a problem
 * detail can never be interpreted as markup.
 */

/**
 * Build an element.
 *
 * `attributes` accepts plain attributes plus three special keys: `text` sets
 * textContent, `on` registers event listeners, and `dataset` sets data-*.
 * A null or undefined attribute value is skipped, so callers can pass
 * optional attributes inline.
 */
export function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attributes)) {
    if (value === null || value === undefined || value === false) {
      continue;
    }
    if (name === "text") {
      node.textContent = String(value);
    } else if (name === "on") {
      for (const [type, handler] of Object.entries(value)) {
        node.addEventListener(type, handler);
      }
    } else if (name === "dataset") {
      Object.assign(node.dataset, value);
    } else {
      node.setAttribute(name, value === true ? "" : String(value));
    }
  }
  append(node, children);
  return node;
}

/** Append children, accepting strings, nodes, arrays, and falsy placeholders. */
export function append(parent, children) {
  const list = Array.isArray(children) ? children : [children];
  for (const child of list) {
    if (child === null || child === undefined || child === false) {
      continue;
    }
    if (Array.isArray(child)) {
      append(parent, child);
    } else {
      parent.append(typeof child === "string" ? document.createTextNode(child) : child);
    }
  }
  return parent;
}

/** Remove every child of a node. */
export function clear(node) {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
  return node;
}

/** Replace a node's contents in one step. */
export function replace(node, children) {
  return append(clear(node), children);
}

/** A monospace pill for a raw API enumeration value, shown exactly as sent. */
export function badge(value, { prefix = null } = {}) {
  return el("span", { class: `badge badge--${value}`, title: prefix ? `${prefix}: ${value}` : null }, [
    prefix ? `${prefix} ` : null,
    value,
  ]);
}

/** A definition list of label/value pairs; null values are dropped. */
export function fieldList(pairs) {
  const list = el("dl", { class: "field-list" });
  for (const [label, value] of pairs) {
    if (value === null || value === undefined || value === "") {
      continue;
    }
    append(list, [
      el("dt", { text: label }),
      el("dd", {}, [typeof value === "string" || typeof value === "number" ? String(value) : value]),
    ]);
  }
  return list;
}

/** An italic placeholder used wherever a list came back empty. */
export function emptyState(text) {
  return el("p", { class: "empty", text });
}

/**
 * Render a message block from the shape `describeError` returns.
 *
 * The API's own `detail` is printed verbatim under our heading, and the
 * machine-readable `error_code` under that, so an operator can quote the exact
 * refusal to whoever administers their token.
 */
export function renderMessage({ level = "info", heading, detail = "", hint = "", code = "" }) {
  return el("div", { class: `message level-${level}` }, [
    el("p", { class: "message__heading", text: heading }),
    hint ? el("p", { class: "message__detail", text: hint }) : null,
    detail ? el("p", { class: "message__detail", text: detail }) : null,
    code ? el("code", { class: "message__code", text: code }) : null,
  ]);
}

/**
 * Render a UTC instant.
 *
 * The API sends ISO-8601 timestamps that are always UTC. They are displayed in
 * UTC too, labelled as such, so a reader never has to guess whose clock a line
 * of the audit trail is on.
 */
export function formatInstant(isoString) {
  const parsed = new Date(isoString);
  if (Number.isNaN(parsed.getTime())) {
    return isoString;
  }
  const pad = (value, width = 2) => String(value).padStart(width, "0");
  return (
    `${parsed.getUTCFullYear()}-${pad(parsed.getUTCMonth() + 1)}-${pad(parsed.getUTCDate())} ` +
    `${pad(parsed.getUTCHours())}:${pad(parsed.getUTCMinutes())}:${pad(parsed.getUTCSeconds())} UTC`
  );
}
