/* Order board: maintenance orders grouped by workflow status.
 *
 * Which actions a card offers is decided in two steps, and neither of them
 * guesses:
 *
 *   1. The order's current status decides which transitions exist at all.
 *      That table lives in `order-actions.json` next to this file, and a
 *      backend test pins it against `cfm.domain.ORDER_TRANSITIONS` and against
 *      the routes the API actually publishes, so it cannot drift from the
 *      server without the suite failing.
 *   2. The token's role decides whether writes are possible at all
 *      (`viewer` may read everything and change nothing). Site grants - which
 *      sites the token may write to - are readable only with an admin token,
 *      so they are deliberately *not* predicted here: the action is offered,
 *      attempted, and the API's 403 `auth.scope_forbidden` is shown as it came.
 *
 * A completed action updates its card from the order the API returned and the
 * board re-paints from memory. Nothing is re-fetched and the page never
 * reloads.
 */

import { describeError } from "../api.js";
import { append, badge, el, emptyState, renderMessage, replace } from "../dom.js";

const ACTIONS_URL = new URL("../order-actions.json", import.meta.url);

async function loadActionTable() {
  const response = await fetch(ACTIONS_URL, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    throw new Error(`The board could not load its action table (${response.status}).`);
  }
  return response.json();
}

function assetLabel(order, assetsById) {
  const asset = assetsById.get(order.asset_id);
  return asset === undefined ? order.asset_id : `${asset.tag} - ${asset.name}`;
}

export async function renderBoard({ session }) {
  const [table, orderPage, assetPage] = await Promise.all([
    loadActionTable(),
    session.client.listAll("/maintenance-orders"),
    session.client.listAll("/assets"),
  ]);

  const assetsById = new Map(assetPage.items.map((asset) => [asset.id, asset]));
  const orders = new Map(orderPage.items.map((order) => [order.id, order]));
  /** Per-order result of the last attempted action, kept across re-paints. */
  const flash = new Map();
  /** Order id whose action form is open, so a re-paint does not close it. */
  let openForm = null;
  let focusAfterPaint = null;

  const container = el("div", { class: "board-root" });
  const header = el("div", {});

  function runAction(order, action, value) {
    const body =
      action.field === undefined
        ? undefined
        : action.field.required || (value ?? "") !== ""
          ? { [action.field.name]: value }
          : {};
    flash.set(order.id, { level: "info", heading: `Sending ${action.label.toLowerCase()}...` });
    openForm = null;
    focusAfterPaint = order.id;
    paint();
    session.client
      .request(`/maintenance-orders/${order.id}/${action.path}`, { method: "POST", body })
      .then((updated) => {
        orders.set(updated.id, updated);
        flash.set(updated.id, {
          level: "ok",
          heading: `The API moved this order to ${updated.status}.`,
        });
      })
      .catch((error) => {
        flash.set(order.id, describeError(error));
      })
      .finally(() => {
        focusAfterPaint = order.id;
        paint();
      });
  }

  function renderActionForm(order, action) {
    const inputId = `field-${order.id}-${action.path}`;
    const input = el("textarea", {
      id: inputId,
      rows: "3",
      required: action.field.required || null,
      "aria-describedby": `${inputId}-hint`,
    });
    const form = el("form", {
      class: "action-form",
      on: {
        submit: (event) => {
          event.preventDefault();
          runAction(order, action, input.value.trim());
        },
      },
    });
    append(form, [
      el("label", { for: inputId, text: `${action.field.label} for "${order.title}"` }),
      input,
      el("p", {
        id: `${inputId}-hint`,
        class: "muted",
        text: action.field.required
          ? "The API requires this note and rejects the action without it."
          : "Optional; the API accepts this action with or without a note.",
      }),
      el("div", { class: "action-form__buttons" }, [
        el("button", { type: "submit", class: "primary", text: `Confirm ${action.label}` }),
        el("button", {
          type: "button",
          text: "Discard",
          on: {
            click: () => {
              openForm = null;
              focusAfterPaint = order.id;
              paint();
            },
          },
        }),
      ]),
    ]);
    return form;
  }

  function renderActions(order) {
    const available = table.actions[order.status] ?? [];
    if (available.length === 0) {
      return el("p", {
        class: "muted",
        text: `The API allows no further transitions from ${order.status}.`,
      });
    }
    if (!session.canWrite) {
      return null;
    }
    const open = openForm !== null && openForm.orderId === order.id ? openForm.action : null;
    return [
      el(
        "div",
        { class: "card__actions" },
        available.map((action) =>
          el("button", {
            type: "button",
            text: action.label,
            "aria-expanded": action.field === undefined ? null : String(open === action),
            on: {
              click: () => {
                if (action.field === undefined) {
                  runAction(order, action, null);
                  return;
                }
                openForm = open === action ? null : { orderId: order.id, action };
                focusAfterPaint = order.id;
                paint();
              },
            },
          }),
        ),
      ),
      open === null ? null : renderActionForm(order, open),
    ];
  }

  function renderCard(order) {
    const message = flash.get(order.id);
    const card = el(
      "article",
      {
        class: "card",
        tabindex: "-1",
        "aria-label": `Order ${order.title}`,
      },
      [
          el("p", { class: "card__title", text: order.title }),
          el("div", { class: "card__meta" }, [
            badge(order.order_type),
            badge(order.status),
          ]),
          el("p", { class: "muted", text: `Asset: ${assetLabel(order, assetsById)}` }),
          el("p", { class: "muted", text: `Due ${order.due_date}` }),
          order.description ? el("p", { text: order.description }) : null,
          order.completion_notes
            ? el("p", { class: "muted", text: `Completion notes: ${order.completion_notes}` })
            : null,
        message ? renderMessage(message) : null,
        renderActions(order),
      ],
    );
    cardsById.set(order.id, card);
    return el("li", {}, [card]);
  }

  function paint() {
    const grouped = new Map(table.statuses.map((status) => [status, []]));
    for (const order of orders.values()) {
      if (!grouped.has(order.status)) {
        grouped.set(order.status, []);
      }
      grouped.get(order.status).push(order);
    }
    replace(
      container,
      [...grouped.entries()].map(([status, columnOrders]) => {
        const label = table.labels[status] ?? status;
        columnOrders.sort((left, right) => left.created_at.localeCompare(right.created_at));
        return el("section", { class: "column", "aria-labelledby": `column-${status}` }, [
          el("h3", { class: "column__heading", id: `column-${status}` }, [
            label,
            el("span", { class: "column__count", text: `${columnOrders.length}` }),
            badge(status),
          ]),
          columnOrders.length === 0
            ? emptyState("No orders in this state.")
            : el("ul", {}, columnOrders.map(renderCard)),
        ]);
      }),
    );
    if (focusAfterPaint !== null) {
      const card = container.querySelector(`#order-${CSS.escape(focusAfterPaint)}`);
      focusAfterPaint = null;
      if (card !== null) {
        card.focus();
      }
    }
  }

  replace(header, [
    el("p", { class: "muted", text: `${orderPage.total} maintenance orders, as counted by the API.` }),
    session.canWrite
      ? null
      : renderMessage({
          level: "info",
          heading: "This token may read but not write",
          hint:
            "Its role is not engineer or admin, so the API would refuse every workflow action with " +
            "403. No actions are offered rather than offering ones that cannot succeed.",
        }),
    session.me === null
      ? renderMessage({
          level: "warning",
          heading: "The token's identity is unknown",
          hint:
            "/me did not answer, so the page cannot tell whether this token may write. No actions " +
            "are offered.",
        })
      : null,
  ]);

  if (orders.size === 0) {
    return [header, emptyState("The API returned no maintenance orders.")];
  }
  paint();
  return [header, container];
}
