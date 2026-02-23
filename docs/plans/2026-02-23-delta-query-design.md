# Delta Query Support for Collector

## Problem

The collector does a full recursive walk of every drive on every scan. For a mid-size tenant (~500 users, ~150k files), this takes ~18 hours and millions of Graph API calls. Subsequent scans repeat all that work even when few permissions changed.

## Solution

Use Microsoft Graph's delta query API to fetch only items that changed since the last scan. First scan runs the existing full walk, then seeds a delta link per drive. Subsequent scans consume the delta link and only process changed items.

## Data Model

New Neo4j node to persist delta state between scans:

```
(DeltaState)
  driveId [UNIQUE]         -- identifies which drive
  deltaLink: string        -- @odata.deltaLink URL from Graph API
  updatedAt: ISO 8601      -- when this link was last refreshed
```

New property on `ScanRun`:

```
scanType: "full" | "delta"
```

## Scan Flow

```
main()
  |-- Create ScanRun
  |-- Determine scan mode:
  |     FORCE_FULL_SCAN=true          --> full
  |     Last full scan > 7 days       --> full
  |     No delta links stored yet     --> full
  |     Otherwise                     --> delta
  |
  |-- For each drive (OneDrive + SharePoint):
  |     If delta mode AND delta link exists:
  |       _delta_scan_drive()
  |         1. GET deltaLink with Prefer headers
  |         2. For each changed item:
  |            - deleted facet    --> remove SHARED_WITH + FOUND rels
  |            - sharedChanged    --> re-fetch permissions, re-merge
  |            - content only     --> update File metadata, skip perms
  |         3. Save new deltaLink
  |     Else (full scan):
  |       _walk_drive_items()          (existing, unchanged)
  |       Seed delta: GET /drives/{id}/root/delta?token=latest
  |       Save deltaLink
  |
  |-- Mark ScanRun completed (scanType = full|delta)
```

## Graph Client Changes

Two new methods:

### `get_drive_delta(delta_url) -> (items, new_delta_link)`

Follows the stored delta link URL with these Prefer headers:
- `deltashowsharingchanges` -- include items where permissions changed
- `deltashowremovedasdeleted` -- show removed items as deleted
- `deltatraversepermissiongaps` -- traverse permission boundaries

Paginates through `@odata.nextLink`, returns final `@odata.deltaLink`.

### `seed_delta_link(drive_id) -> delta_link`

Calls `GET /drives/{drive_id}/root/delta?token=latest` which returns a delta link representing the current state without enumerating items. Used after a full scan to establish the baseline.

## Neo4j Client Changes

Three new methods:

- `save_delta_link(drive_id, delta_link)` -- MERGE DeltaState node
- `get_delta_link(drive_id) -> str | None` -- fetch stored link
- `remove_file_permissions(drive_id, item_id, run_id)` -- delete SHARED_WITH and FOUND rels for a deleted item

Schema init adds: `CREATE CONSTRAINT IF NOT EXISTS FOR (d:DeltaState) REQUIRE d.driveId IS UNIQUE`

## Delta Item Processing

For each item in the delta response:

| Condition | Action |
|-----------|--------|
| `item.deleted` present | Remove SHARED_WITH rels, remove FOUND rel |
| `@microsoft.graph.sharedChanged: true` | Re-fetch permissions via `get_item_permissions()`, re-merge |
| Neither (content-only change) | Update File node metadata (path, webUrl), skip permission fetch |

## Configuration

| Env var | Default | Description |
|---------|---------|-------------|
| `FORCE_FULL_SCAN` | `false` | Force full scan ignoring delta links |
| `FULL_SCAN_INTERVAL_DAYS` | `7` | Days between forced full scans |

## What Doesn't Change

- `_walk_drive_items()` -- untouched, used for full scans
- `merge_permission()` -- same atomic merge for both modes
- `classify.py` -- risk scoring unchanged
- Webapp/frontend -- no changes needed

## Estimated Impact

| Metric | Full scan | Delta scan |
|--------|-----------|------------|
| API calls per drive | 2 * item_count | changed_items + 1 (delta call) |
| Typical run time | ~18 hours | Minutes (if few changes) |
| Forced full rescan | Every run | Every 7 days (configurable) |
