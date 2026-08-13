# 4. Sites and locations

[← Roles and permissions](03-roles-and-permissions.md) · [Manual index](README.md) · [Next: Assets →](05-assets.md)

Locations are the physical skeleton of the record. Every asset hangs from one,
and the site at the top of each branch is what write permissions are scoped by.

## The hierarchy

Exactly four levels, in a fixed order:

```
site
 └── building
      └── floor
           └── room
```

| Kind | Parent it requires |
|------|--------------------|
| `site` | none — a site must not have a parent |
| `building` | a `site` |
| `floor` | a `building` |
| `room` | a `floor` |

The rule is strict adjacency. You cannot put a room directly under a building
to skip an unnamed floor, and you cannot nest a site inside a site to model a
campus of campuses. If your estate does not fit, the honest answer is that
version 0.2.0 will not model it; see
[Known limitations](19-known-limitations.md).

**Assets, however, may hang from any level.** In the demo campus the standby
generator sits on the building, not in a room, because that is where it is. The
[asset tree](15-web-interface.md#asset-tree) shows it there.

### Codes and names

Each location has a short `code` and a longer `name`. Codes must be unique
*among siblings* — two different floors may each have a room coded `L1-ELEC` as
long as they have different parents — and the top level (sites) is treated as
one sibling group.

The demo campus:

| Kind | Code | Name |
|------|------|------|
| site | `CAMP-A` | Camellia Demo Campus |
| building | `BLDG-C` | Building C - Data Hall Block |
| floor | `L1` | Level 1 |
| floor | `L2` | Level 2 |
| room | `L1-ELEC` | Electrical Room 1A |
| room | `L1-DH1` | Data Hall 1 |
| room | `L2-MECH` | Mechanical Plant Room |

## Viewing the hierarchy

**In the web interface:** the Asset tree view draws the whole hierarchy with
assets filed under each node and a count on every branch. It is the only place
in the product that shows the structure as a structure.

**Over the API:**

```
$ curl -H "Authorization: Bearer $TOKEN" "$API/locations?limit=200"
```

Filters:

| Parameter | Effect |
|-----------|--------|
| `kind` | Only `site`, `building`, `floor`, or `room` |
| `parent_id` | Only the direct children of one location |

`parent_id` returns *direct* children only; there is no "everything under this
node" query. To walk a subtree you follow `parent_id` yourself, one level at a
time — which is what the asset tree view does in the browser.

A single location:

```
$ curl -H "Authorization: Bearer $TOKEN" $API/locations/bd42685d-70f0-459f-87dc-367c2e865b8d
{
  "id": "bd42685d-70f0-459f-87dc-367c2e865b8d",
  "kind": "floor",
  "code": "L1",
  "name": "Level 1",
  "parent_id": "04b7a3aa-8688-4e3e-a672-d67eb4fea7f4",
  "created_at": "2026-08-13T18:17:49.531195Z",
  "updated_at": "2026-08-13T18:17:49.531200Z"
}
```

## Creating a location

**API only.** There is no way to add a location from the web interface.

1. Find the id of the parent it belongs under (skip this for a site).
2. Choose a `code` unique among that parent's existing children.
3. `POST` it.

```
$ curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"kind":"room","code":"L1-BATT","name":"Battery Room 1B",
         "parent_id":"bd42685d-70f0-459f-87dc-367c2e865b8d"}' \
    $API/locations
{
  "id": "5b369b10-b966-470e-8ebc-73cfac474d86",
  "kind": "room",
  "code": "L1-BATT",
  "name": "Battery Room 1B",
  "parent_id": "bd42685d-70f0-459f-87dc-367c2e865b8d",
  "created_at": "2026-08-13T18:28:34.095828Z",
  "updated_at": "2026-08-13T18:28:34.095834Z"
}
```

| Field | Rules |
|-------|-------|
| `kind` | Required: `site`, `building`, `floor`, or `room` |
| `code` | Required, 1–64 characters, unique among siblings |
| `name` | Required, 1–255 characters |
| `parent_id` | Required for everything except a site; must be omitted or null for a site |

**Permission:** creating a site requires an installation-wide grant, because it
changes the set of sites everyone else's grants are measured against. Creating
anything below a site requires a grant covering that site.

### The four refusals

```
A room under a site:
{"status":422,"error_code":"location.invalid_parent_kind",
 "detail":"A room must be created under a floor; 'CAMP-A' is a site."}

A floor with no parent:
{"status":422,"error_code":"location.parent_required",
 "detail":"A floor requires a building parent."}

A site with a parent:
{"status":422,"error_code":"location.invalid_parent_kind",
 "detail":"A site must not have a parent location."}

A code already used by a sibling:
{"status":409,"error_code":"location.duplicate_code",
 "detail":"Location code 'L1-ELEC' already exists under the same parent."}
```

## Renaming a location

`PATCH` changes `code` and `name`, and nothing else:

```
$ curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -d '{"name":"Battery Room 1B (UPS 1)"}' $API/locations/5b369b10-...
```

**A location cannot be moved.** There is no way to change `parent_id`, so a
floor cannot be reassigned to another building and a room cannot be moved to
another floor. If the structure was recorded wrongly, the route is to create the
correct location, move the assets to it (assets *can* be moved — see
[Assets](05-assets.md#moving-an-asset)), and delete the wrong one.

Changing a `code` re-checks uniqueness among the same siblings.

## Deleting a location

```
$ curl -X DELETE -H "Authorization: Bearer $TOKEN" $API/locations/5b369b10-...
(204 No Content)
```

Deletion is the one genuinely destructive operation in the product, and it is
hedged with three guards. A location cannot be deleted while anything still
depends on it:

```
Child locations below it:
{"status":409,"error_code":"location.has_children",
 "detail":"Location 'L1' still has child locations."}

Assets assigned to it:
{"status":409,"error_code":"location.has_assets",
 "detail":"Location 'L1-ELEC' still has assets assigned to it."}

Site grants pointing at it:
{"status":409,"error_code":"location.has_site_grants",
 "detail":"Location 'TEMP-X' is still referenced by site grants; delete those grants first."}
```

So a subtree must be dismantled from the leaves upwards. Deleting a site
requires an installation-wide grant *and* that every grant referencing it be
removed first.

The deletion itself is audited — the entry records the location as it was in
its `before` payload — but the location row is gone. Everything else in this
system is retired, cancelled, or deactivated rather than deleted; locations are
the exception.

## How sites drive permissions

The site at the top of a branch is not just a label. It is the unit of write
authority:

- An asset records its site when it is created, and re-records it if it is
  moved to a location under a different site.
- Orders, permits, incidents, tests, and punch items inherit the site of the
  asset or location they concern.
- A write is allowed only if you hold a grant on that site, or an
  installation-wide grant.

This is why creating and deleting sites is restricted to installation-wide
holders, and why a site cannot be deleted while grants still name it. See
[Roles and permissions](03-roles-and-permissions.md#site-grants-where-you-may-write).

---

[← Roles and permissions](03-roles-and-permissions.md) · [Manual index](README.md) · [Next: Assets →](05-assets.md)
