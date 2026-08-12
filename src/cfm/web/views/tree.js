/* Asset tree: the location hierarchy with the assets recorded against it.
 *
 * The tree is built from `parent_id` on the locations the API returns, and
 * assets are filed under their own `location_id` - which may be any level of
 * the hierarchy, not only a room, so a generator attached to a building shows
 * up on the building.
 *
 * Expandable nodes are `<details>`/`<summary>` rather than a hand-rolled ARIA
 * tree: `<details>` is already keyboard-operable and announced correctly by
 * screen readers, which a partial `role="tree"` implementation would not be.
 */

import { append, badge, el, emptyState, fieldList, formatInstant, replace } from "../dom.js";

const KIND_ORDER = ["site", "building", "floor", "room"];

function byCode(left, right) {
  return left.code.localeCompare(right.code);
}

function byTag(left, right) {
  return left.tag.localeCompare(right.tag);
}

function locationLabel(location) {
  return `${location.code} - ${location.name}`;
}

function renderAssetDetail(asset, locationsById) {
  const panel = document.getElementById("asset-detail");
  if (asset === null) {
    replace(panel, [
      el("h3", { text: "Asset detail" }),
      el("p", { class: "empty", text: "Select an asset in the tree to see its record." }),
    ]);
    return;
  }
  const location = locationsById.get(asset.location_id) ?? null;
  replace(panel, [
    el("h3", { text: `${asset.tag} - ${asset.name}` }),
    fieldList([
      ["Status", badge(asset.status)],
      ["Criticality", badge(asset.criticality)],
      ["Type", asset.asset_type],
      ["Site", asset.site_id],
      ["Location", location === null ? asset.location_id : locationLabel(location)],
      ["Location kind", location === null ? null : badge(location.kind)],
      ["Asset id", asset.id],
      ["Created", formatInstant(asset.created_at)],
      ["Updated", formatInstant(asset.updated_at)],
    ]),
  ]);
}

function renderAssetItem(asset, locationsById, buttons) {
  const button = el("button", {
    type: "button",
    class: "asset-button",
    "aria-pressed": "false",
    on: {
      click: () => {
        for (const other of buttons) {
          other.setAttribute("aria-pressed", String(other === button));
        }
        renderAssetDetail(asset, locationsById);
      },
    },
  });
  append(button, [
    el("span", { class: "node-name", text: asset.tag }),
    ` ${asset.name} `,
    badge(asset.status),
    " ",
    badge(asset.criticality),
  ]);
  buttons.push(button);
  return el("li", {}, [button]);
}

function renderNode(location, context) {
  const { childrenByParent, assetsByLocation, locationsById, buttons, counter } = context;
  counter.locations += 1;
  const children = (childrenByParent.get(location.id) ?? []).slice().sort(byCode);
  const assets = (assetsByLocation.get(location.id) ?? []).slice().sort(byTag);
  counter.assets += assets.length;

  const summary = el("summary", {}, [
    badge(location.kind),
    " ",
    el("span", { class: "node-name", text: location.code }),
    ` ${location.name} `,
    el("span", {
      class: "muted",
      text: assets.length === 1 ? "(1 asset)" : `(${assets.length} assets)`,
    }),
  ]);

  const body = el("ul", {});
  for (const asset of assets) {
    body.append(renderAssetItem(asset, locationsById, buttons));
  }
  for (const child of children) {
    body.append(renderNode(child, context));
  }
  if (assets.length === 0 && children.length === 0) {
    body.append(el("li", {}, [emptyState("Nothing recorded under this location.")]));
  }

  return el("li", {}, [el("details", { open: true }, [summary, body])]);
}

export async function renderTree({ session }) {
  const [locationPage, assetPage] = await Promise.all([
    session.client.listAll("/locations"),
    session.client.listAll("/assets"),
  ]);

  const locations = locationPage.items;
  const assets = assetPage.items;
  const locationsById = new Map(locations.map((location) => [location.id, location]));
  const childrenByParent = new Map();
  for (const location of locations) {
    const key = location.parent_id;
    if (!childrenByParent.has(key)) {
      childrenByParent.set(key, []);
    }
    childrenByParent.get(key).push(location);
  }
  const assetsByLocation = new Map();
  for (const asset of assets) {
    if (!assetsByLocation.has(asset.location_id)) {
      assetsByLocation.set(asset.location_id, []);
    }
    assetsByLocation.get(asset.location_id).push(asset);
  }

  if (locations.length === 0) {
    renderAssetDetail(null, locationsById);
    return emptyState("The API returned no locations. Seed the demo dataset or create a site.");
  }

  const roots = (childrenByParent.get(null) ?? [])
    .slice()
    .sort((left, right) => KIND_ORDER.indexOf(left.kind) - KIND_ORDER.indexOf(right.kind) || byCode(left, right));

  const buttons = [];
  const counter = { locations: 0, assets: 0 };
  const context = { childrenByParent, assetsByLocation, locationsById, buttons, counter };
  const list = el("ul", {});
  for (const root of roots) {
    list.append(renderNode(root, context));
  }

  renderAssetDetail(null, locationsById);

  const unreached = locations.length - counter.locations;
  const unplaced = assets.length - counter.assets;
  return [
    el("p", { class: "muted" }, [
      `${locationPage.total} locations and ${assetPage.total} assets, as counted by the API.`,
    ]),
    list,
    unreached > 0
      ? el("p", { class: "message level-warning" }, [
          `${unreached} location(s) the API returned are not shown: their parent is not among the ` +
            "loaded locations, so the page cannot place them without inventing a position.",
        ])
      : null,
    unplaced > 0
      ? el("p", { class: "message level-warning" }, [
          `${unplaced} asset(s) the API returned are not shown: their location is not among the ` +
            "loaded locations.",
        ])
      : null,
  ];
}
