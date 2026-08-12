/* Audit timeline: the append-only trail, newest first.
 *
 * The API returns audit entries in descending id order, which is the order
 * they were written, so the page never re-sorts them - it renders the sequence
 * the server reports. Older entries are fetched a page at a time rather than
 * all at once: the trail is the one table in this system that only ever grows.
 */

import { badge, el, emptyState, formatInstant, replace } from "../dom.js";

const PAGE_SIZE = 50;

function renderPayload(label, value) {
  if (value === null || value === undefined) {
    return null;
  }
  return el("details", {}, [
    el("summary", { text: label }),
    el("pre", { text: JSON.stringify(value, null, 2) }),
  ]);
}

function renderEntry(entry) {
  return el("li", { class: "entry" }, [
    el("p", { class: "entry__when", text: formatInstant(entry.occurred_at) }),
    el("p", { class: "entry__headline" }, [
      el("span", { class: "entry__actor", text: entry.actor }),
      " ",
      badge(entry.action),
      ` ${entry.entity_type} `,
      el("span", { class: "raw", text: entry.entity_id }),
    ]),
    el("p", { class: "muted" }, [
      entry.scope === null
        ? "No site scope recorded for this action."
        : `Authorized by scope ${entry.scope}.`,
    ]),
    renderPayload("Before", entry.before),
    renderPayload("After", entry.after),
  ]);
}

export async function renderAudit({ session }) {
  const first = await session.client.request("/audit-entries", {
    params: { limit: PAGE_SIZE, offset: 0 },
  });

  if (first.total === 0) {
    return emptyState("The API returned no audit entries yet.");
  }

  const container = el("div", {});
  const list = el("ol", { class: "timeline" });
  const footer = el("div", { class: "timeline-footer" });
  const counter = el("p", { class: "muted", role: "status" });
  let loaded = 0;
  let total = first.total;

  function appendPage(page) {
    total = page.total;
    for (const entry of page.items) {
      list.append(renderEntry(entry));
    }
    loaded += page.items.length;
    counter.textContent =
      `Showing the ${loaded} most recent of ${total} entries. The trail is append-only: ` +
      "the API never writes to it from these pages and the database refuses updates and " +
      "deletes on it outright, so every line above is permanent.";
  }

  const more = el("button", {
    type: "button",
    text: "Load older entries",
    on: {
      click: async () => {
        more.disabled = true;
        more.textContent = "Loading...";
        try {
          const page = await session.client.request("/audit-entries", {
            params: { limit: PAGE_SIZE, offset: loaded },
          });
          appendPage(page);
        } finally {
          more.disabled = loaded >= total;
          more.textContent = loaded >= total ? "All entries loaded" : "Load older entries";
        }
      },
    },
  });

  appendPage(first);
  more.disabled = loaded >= total;
  if (more.disabled) {
    more.textContent = "All entries loaded";
  }
  replace(footer, [
    more,
    el("span", {
      class: "muted",
      text:
        "Paging is by offset, so an entry written while you page can shift the window by a line.",
    }),
  ]);

  return replace(container, [counter, list, footer]);
}
