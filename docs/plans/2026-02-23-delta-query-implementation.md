# Delta Query Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add delta query support so the collector only fetches changed items on subsequent scans, reducing ~18h scans to minutes.

**Architecture:** First scan uses existing recursive walk then seeds a delta link per drive. Subsequent scans consume the delta link and only process changed items. Delta links are stored as `DeltaState` nodes in Neo4j. A periodic full rescan (configurable, default 7 days) catches any drift.

**Tech Stack:** Python 3.11, Neo4j 5.x, Microsoft Graph API v1.0, httpx, pytest with unittest.mock

---

### Task 1: Add CollectorConfig fields for delta scan settings

**Files:**
- Modify: `src/shared/config.py:27-32`
- Test: `tests/shared/test_config.py` (create if needed)

**Step 1: Add the two new fields to CollectorConfig**

```python
@dataclass(frozen=True)
class CollectorConfig:
    graph_api: GraphApiConfig = field(default_factory=GraphApiConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    delay_ms: int = field(
        default_factory=lambda: int(os.environ.get("DELAY_MS", "100"))
    )
    force_full_scan: bool = field(
        default_factory=lambda: os.environ.get("FORCE_FULL_SCAN", "").lower()
        in ("1", "true", "yes")
    )
    full_scan_interval_days: int = field(
        default_factory=lambda: int(
            os.environ.get("FULL_SCAN_INTERVAL_DAYS", "7")
        )
    )
```

**Step 2: Run existing tests to verify no regressions**

Run: `python -m pytest tests/ -x -q`
Expected: All pass (existing tests don't set these env vars, so defaults apply)

**Step 3: Commit**

```bash
git add src/shared/config.py
git commit -m "feat(collector): add delta scan config fields"
```

---

### Task 2: Add Neo4j DeltaState schema and CRUD methods

**Files:**
- Modify: `src/shared/neo4j_client.py:23-32` (init_schema), and add methods after line 207
- Test: `tests/shared/test_neo4j_delta.py` (create)

**Step 1: Write the failing tests**

Create `tests/shared/test_neo4j_delta.py`:

```python
"""Tests for Neo4j delta state operations."""

from unittest.mock import MagicMock
from shared.neo4j_client import Neo4jClient


class TestDeltaState:
    def test_save_delta_link(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock()

        client.save_delta_link("drive-1", "https://graph.microsoft.com/delta?token=abc")

        client.execute.assert_called_once()
        query = client.execute.call_args[0][0]
        params = client.execute.call_args[0][1]
        assert "MERGE" in query
        assert "DeltaState" in query
        assert params["driveId"] == "drive-1"
        assert params["deltaLink"] == "https://graph.microsoft.com/delta?token=abc"

    def test_get_delta_link_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(
            return_value=[{"deltaLink": "https://graph.microsoft.com/delta?token=abc"}]
        )

        result = client.get_delta_link("drive-1")
        assert result == "https://graph.microsoft.com/delta?token=abc"

    def test_get_delta_link_not_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[])

        result = client.get_delta_link("drive-1")
        assert result is None

    def test_remove_file_permissions(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock()

        client.remove_file_permissions("drive-1", "item-1", "run-1")

        client.execute.assert_called_once()
        query = client.execute.call_args[0][0]
        assert "SHARED_WITH" in query
        assert "DELETE" in query

    def test_get_last_full_scan_time_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(
            return_value=[{"timestamp": "2026-02-20T12:00:00+00:00"}]
        )

        result = client.get_last_full_scan_time()
        assert result == "2026-02-20T12:00:00+00:00"

    def test_get_last_full_scan_time_not_found(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[])

        result = client.get_last_full_scan_time()
        assert result is None

    def test_has_delta_links(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[{"count": 5}])

        assert client.has_delta_links() is True

    def test_has_no_delta_links(self):
        client = Neo4jClient.__new__(Neo4jClient)
        client.execute = MagicMock(return_value=[{"count": 0}])

        assert client.has_delta_links() is False
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/shared/test_neo4j_delta.py -v`
Expected: FAIL — methods don't exist yet

**Step 3: Add DeltaState constraint to init_schema**

In `src/shared/neo4j_client.py`, add to the `constraints` list in `init_schema()` (line 29):

```python
"CREATE CONSTRAINT IF NOT EXISTS FOR (d:DeltaState) REQUIRE d.driveId IS UNIQUE",
```

**Step 4: Add CRUD methods**

Append to `src/shared/neo4j_client.py` after `merge_permission()`:

```python
    def save_delta_link(self, drive_id: str, delta_link: str):
        """Store or update the delta link for a drive."""
        self.execute(
            """MERGE (d:DeltaState {driveId: $driveId})
               SET d.deltaLink = $deltaLink,
                   d.updatedAt = datetime()""",
            {"driveId": drive_id, "deltaLink": delta_link},
        )

    def get_delta_link(self, drive_id: str) -> str | None:
        """Get the stored delta link for a drive, or None."""
        result = self.execute(
            "MATCH (d:DeltaState {driveId: $driveId}) RETURN d.deltaLink AS deltaLink",
            {"driveId": drive_id},
        )
        return result[0]["deltaLink"] if result else None

    def remove_file_permissions(self, drive_id: str, item_id: str, run_id: str):
        """Remove all SHARED_WITH relationships for a deleted file."""
        self.execute(
            """MATCH (f:File {driveId: $driveId, itemId: $itemId})-[s:SHARED_WITH]->()
               DELETE s""",
            {"driveId": drive_id, "itemId": item_id},
        )

    def get_last_full_scan_time(self) -> str | None:
        """Get the timestamp of the most recent completed full scan."""
        result = self.execute("""
            MATCH (r:ScanRun {status: 'completed', scanType: 'full'})
            RETURN r.timestamp AS timestamp
            ORDER BY r.timestamp DESC
            LIMIT 1
        """)
        return result[0]["timestamp"] if result else None

    def has_delta_links(self) -> bool:
        """Check if any delta links are stored."""
        result = self.execute("MATCH (d:DeltaState) RETURN count(d) AS count")
        return result[0]["count"] > 0
```

**Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/shared/test_neo4j_delta.py -v`
Expected: All 8 tests PASS

**Step 6: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: All pass

**Step 7: Commit**

```bash
git add src/shared/neo4j_client.py tests/shared/test_neo4j_delta.py
git commit -m "feat(collector): add Neo4j DeltaState CRUD methods"
```

---

### Task 3: Add Graph API delta methods

**Files:**
- Modify: `src/collector/graph_client.py:131-149` (add after `get_item_permissions`)
- Test: `tests/collector/test_graph_client.py`

**Step 1: Write the failing tests**

Append to `tests/collector/test_graph_client.py`:

```python
class TestDeltaMethods:
    def test_seed_delta_link(self):
        """seed_delta_link calls delta?token=latest and returns deltaLink."""
        client = GraphClient.__new__(GraphClient)
        client._make_request = MagicMock(return_value={
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/drives/d1/root/delta?token=xyz",
            "value": [],
        })
        client.delay_ms = 0

        link = client.seed_delta_link("d1")

        assert link == "https://graph.microsoft.com/v1.0/drives/d1/root/delta?token=xyz"
        url = client._make_request.call_args[0][0]
        assert "delta" in url
        params = client._make_request.call_args[0][1]
        assert params["token"] == "latest"

    def test_get_drive_delta_single_page(self):
        """get_drive_delta returns items and new delta link."""
        client = GraphClient.__new__(GraphClient)
        client.delay_ms = 0
        client._make_request = MagicMock(return_value={
            "value": [
                {"id": "item-1", "name": "changed.docx",
                 "@microsoft.graph.sharedChanged": True},
            ],
            "@odata.deltaLink": "https://graph.microsoft.com/delta?token=new",
        })

        items, new_link = client.get_drive_delta(
            "https://graph.microsoft.com/delta?token=old"
        )

        assert len(items) == 1
        assert items[0]["id"] == "item-1"
        assert new_link == "https://graph.microsoft.com/delta?token=new"

    def test_get_drive_delta_paginates(self):
        """get_drive_delta follows nextLink then returns deltaLink."""
        client = GraphClient.__new__(GraphClient)
        client.delay_ms = 0
        client._make_request = MagicMock(side_effect=[
            {
                "value": [{"id": "item-1", "name": "a.txt"}],
                "@odata.nextLink": "https://graph.microsoft.com/delta?page=2",
            },
            {
                "value": [{"id": "item-2", "name": "b.txt"}],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=final",
            },
        ])

        items, new_link = client.get_drive_delta(
            "https://graph.microsoft.com/delta?token=old"
        )

        assert len(items) == 2
        assert new_link == "https://graph.microsoft.com/delta?token=final"
        assert client._make_request.call_count == 2
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/collector/test_graph_client.py::TestDeltaMethods -v`
Expected: FAIL — methods don't exist

**Step 3: Implement the methods**

Add to `src/collector/graph_client.py` before `throttle()`:

```python
    def seed_delta_link(self, drive_id: str) -> str:
        """Get initial delta link for a drive without enumerating items."""
        data = self._make_request(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root/delta",
            {"token": "latest"},
        )
        return data["@odata.deltaLink"]

    def get_drive_delta(self, delta_url: str) -> tuple[list[dict], str]:
        """Follow a delta link, return (changed_items, new_delta_link).

        Uses Prefer headers to track permission changes and deleted items.
        Paginates through @odata.nextLink, returns final @odata.deltaLink.
        """
        headers_extra = {
            "Prefer": "deltashowsharingchanges, deltashowremovedasdeleted, "
            "deltatraversepermissiongaps"
        }
        items: list[dict] = []
        url: str | None = delta_url
        delta_link = ""

        while url:
            data = self._make_request_with_headers(url, extra_headers=headers_extra)
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            if "@odata.deltaLink" in data:
                delta_link = data["@odata.deltaLink"]
            if url and self.delay_ms > 0:
                time.sleep(self.delay_ms / 1000)

        return items, delta_link
```

This requires a small helper. Add `_make_request_with_headers` to `GraphClient`:

```python
    def _make_request_with_headers(
        self, url: str, extra_headers: dict | None = None, params: dict | None = None
    ) -> dict:
        """Make a GET request with additional headers."""
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        if extra_headers:
            headers.update(extra_headers)
        for attempt in range(4):
            try:
                resp = httpx.get(url, headers=headers, params=params, timeout=30)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "5"))
                    logger.warning(f"Rate limited. Waiting {retry_after}s...")
                    time.sleep(retry_after)
                    continue
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 401:
                    self._token = None
                    continue
                if attempt < 3 and e.response.status_code >= 500:
                    time.sleep(2**attempt)
                    continue
                raise
        return {}
```

**Note:** This duplicates `_make_request`. A cleaner approach: refactor `_make_request` to accept `extra_headers` as an optional param. This avoids duplication:

```python
    def _make_request(
        self, url: str, params: dict | None = None, extra_headers: dict | None = None
    ) -> dict:
        """Make a GET request to the Graph API with retry logic."""
        headers = {"Authorization": f"Bearer {self._get_token()}"}
        if extra_headers:
            headers.update(extra_headers)
        # ... rest unchanged ...
```

Then `get_drive_delta` calls `self._make_request(url, extra_headers=headers_extra)` directly. No new helper needed. Existing callers pass no `extra_headers` so nothing changes.

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/collector/test_graph_client.py -v`
Expected: All pass (existing + 3 new)

**Step 5: Commit**

```bash
git add src/collector/graph_client.py tests/collector/test_graph_client.py
git commit -m "feat(collector): add Graph API delta query methods"
```

---

### Task 4: Add ScanRun scanType property

**Files:**
- Modify: `src/shared/neo4j_client.py:34-48` (`create_scan_run` and `complete_scan_run`)
- Modify: `tests/webapp/test_queries.py` and `tests/webapp/test_routes_files.py` (update mocks if affected)

**Step 1: Update create_scan_run to accept scanType**

```python
    def create_scan_run(self, scan_type: str = "full") -> str:
        """Create a new ScanRun node. Returns the runId."""
        run_id = str(uuid.uuid4())
        self.execute(
            """CREATE (r:ScanRun {
                runId: $runId, timestamp: $ts,
                status: 'running', scanType: $scanType
            })""",
            {
                "runId": run_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "scanType": scan_type,
            },
        )
        return run_id
```

**Step 2: Run full test suite to check for regressions**

Run: `python -m pytest tests/ -x -q`
Expected: All pass (default `scan_type="full"` preserves existing behavior)

**Step 3: Commit**

```bash
git add src/shared/neo4j_client.py
git commit -m "feat(collector): add scanType property to ScanRun"
```

---

### Task 5: Implement delta scan drive logic

**Files:**
- Create: `src/collector/delta.py`
- Test: `tests/collector/test_delta.py` (create)

**Step 1: Write the failing tests**

Create `tests/collector/test_delta.py`:

```python
"""Tests for delta scan logic."""

from unittest.mock import MagicMock, call
from collector.delta import delta_scan_drive


class TestDeltaScanDrive:
    def test_processes_shared_changed_item(self):
        """Items with sharedChanged get permissions re-fetched."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {"id": "item-1", "name": "doc.xlsx", "webUrl": "https://x.com/doc",
                 "parentReference": {"path": "/drive/root:/Folder"},
                 "file": {"mimeType": "x"},
                 "@microsoft.graph.sharedChanged": True},
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        graph.get_item_permissions.return_value = [
            {"id": "p1", "link": {"scope": "organization"}, "roles": ["read"],
             "inheritedFrom": {}},
        ]
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph, neo4j, "drive-1", "https://graph.microsoft.com/delta?token=old",
            "site-1", "owner@test.dk", "test.dk", "run-1",
        )

        assert count == 1
        graph.get_item_permissions.assert_called_once_with("drive-1", "item-1")
        neo4j.merge_permission.assert_called_once()

    def test_processes_deleted_item(self):
        """Items with deleted facet get permissions removed."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {"id": "item-1", "deleted": {"state": "deleted"}},
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph, neo4j, "drive-1", "https://graph.microsoft.com/delta?token=old",
            "site-1", "owner@test.dk", "test.dk", "run-1",
        )

        assert count == 0
        neo4j.remove_file_permissions.assert_called_once_with("drive-1", "item-1", "run-1")
        graph.get_item_permissions.assert_not_called()

    def test_skips_content_only_changes(self):
        """Items without sharedChanged or deleted skip permission fetch."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [
                {"id": "item-1", "name": "renamed.docx", "webUrl": "https://x.com/doc",
                 "parentReference": {"path": "/drive/root:/Folder"},
                 "file": {"mimeType": "x"}},
            ],
            "https://graph.microsoft.com/delta?token=new",
        )
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph, neo4j, "drive-1", "https://graph.microsoft.com/delta?token=old",
            "site-1", "owner@test.dk", "test.dk", "run-1",
        )

        assert count == 0
        graph.get_item_permissions.assert_not_called()
        neo4j.merge_permission.assert_not_called()
        # File metadata should still be updated
        neo4j.merge_file.assert_called_once()

    def test_returns_new_delta_link(self):
        """The function returns the new delta link."""
        graph = MagicMock()
        graph.get_drive_delta.return_value = (
            [],
            "https://graph.microsoft.com/delta?token=new",
        )
        neo4j = MagicMock()

        count = delta_scan_drive(
            graph, neo4j, "drive-1", "https://graph.microsoft.com/delta?token=old",
            "site-1", "owner@test.dk", "test.dk", "run-1",
        )

        assert count == 0
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/collector/test_delta.py -v`
Expected: FAIL — module doesn't exist

**Step 3: Implement delta_scan_drive**

Create `src/collector/delta.py`:

```python
"""Delta scan: process only changed items for a drive."""

import logging

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from shared.classify import (
    get_sharing_type,
    get_shared_with_info,
    get_risk_level,
    get_permission_role,
    get_granted_by,
)

logger = logging.getLogger(__name__)


def _item_path_from_delta(item: dict) -> str:
    """Extract file path from a delta response item.

    Delta items have parentReference.path like '/drive/root:/Folder/Sub'
    and a name field. Combine them to get the relative path.
    """
    parent_ref = item.get("parentReference", {})
    parent_path = parent_ref.get("path", "")
    # Strip the /drive/root: prefix
    if ":/" in parent_path:
        parent_path = parent_path.split(":/", 1)[1]
    elif parent_path.endswith(":"):
        parent_path = ""
    else:
        parent_path = ""
    name = item.get("name", "")
    if parent_path:
        return f"/{parent_path}/{name}"
    return f"/{name}"


def delta_scan_drive(
    graph: GraphClient,
    neo4j: Neo4jClient,
    drive_id: str,
    delta_link: str,
    site_id: str,
    owner_email: str,
    tenant_domain: str,
    run_id: str,
) -> int:
    """Process delta changes for a single drive. Returns count of shared items found."""
    items, new_delta_link = graph.get_drive_delta(delta_link)
    logger.info(f"  Delta returned {len(items)} changed items")

    count = 0
    for item in items:
        item_id = item["id"]

        # Handle deleted items
        if item.get("deleted"):
            neo4j.remove_file_permissions(drive_id, item_id, run_id)
            continue

        item_path = _item_path_from_delta(item)
        item_type = "Folder" if item.get("folder") else "File"
        web_url = item.get("webUrl", "")

        # Content-only change: just update file metadata
        if not item.get("@microsoft.graph.sharedChanged"):
            neo4j.merge_file(drive_id, item_id, item_path, web_url, item_type)
            continue

        # Permission change: re-fetch and re-merge
        try:
            permissions = graph.get_item_permissions(drive_id, item_id)
        except Exception as e:
            logger.warning(f"Could not get permissions for {item_path}: {e}")
            permissions = []

        for perm in permissions:
            sharing_type = get_sharing_type(perm)
            shared_info = get_shared_with_info(perm, tenant_domain)
            role = get_permission_role(perm)
            granted_by = get_granted_by(perm) or owner_email
            risk = get_risk_level(
                sharing_type, shared_info["shared_with_type"], item_path
            )

            if role == "Owner" and shared_info["shared_with"] == owner_email:
                continue

            shared_email = shared_info["shared_with"]
            if shared_info["shared_with_type"] == "Anonymous":
                shared_email = "anonymous"
            elif sharing_type == "Link-Organization":
                shared_email = "organization"

            neo4j.merge_permission(
                site_id=site_id,
                drive_id=drive_id,
                item_id=item_id,
                item_path=item_path,
                web_url=web_url,
                file_type=item_type,
                user_email=shared_email,
                user_display_name=shared_info["shared_with"],
                user_source=shared_info["shared_with_type"],
                sharing_type=sharing_type,
                shared_with_type=shared_info["shared_with_type"],
                role=role,
                risk_level=risk,
                created_date_time=perm.get("createdDateTime", ""),
                run_id=run_id,
                granted_by=granted_by,
            )
            count += 1

    # Save the new delta link for next scan
    neo4j.save_delta_link(drive_id, new_delta_link)
    return count
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/collector/test_delta.py -v`
Expected: All 4 tests PASS

**Step 5: Commit**

```bash
git add src/collector/delta.py tests/collector/test_delta.py
git commit -m "feat(collector): add delta scan drive logic"
```

---

### Task 6: Wire delta scan into the main orchestrator

**Files:**
- Modify: `src/collector/__main__.py`
- Modify: `src/collector/onedrive.py` (add delta link seeding after full walk)
- Modify: `src/collector/sharepoint.py` (add delta link seeding after full walk)

**Step 1: Update `__main__.py` to determine scan mode**

Replace the current `main()` function:

```python
"""Collector entry point: python -m collector"""

import logging
import os
from datetime import datetime, timezone, timedelta

from shared.config import CollectorConfig
from shared.neo4j_client import Neo4jClient
from collector.graph_client import GraphClient
from collector.onedrive import collect_onedrive_user
from collector.sharepoint import collect_sharepoint_sites

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _should_full_scan(config: CollectorConfig, neo4j: Neo4jClient) -> bool:
    """Determine if a full scan is needed."""
    if config.force_full_scan:
        logger.info("FORCE_FULL_SCAN is set — running full scan.")
        return True

    if not neo4j.has_delta_links():
        logger.info("No delta links stored — running full scan.")
        return True

    last_full = neo4j.get_last_full_scan_time()
    if not last_full:
        logger.info("No prior full scan found — running full scan.")
        return True

    last_full_dt = datetime.fromisoformat(last_full)
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.full_scan_interval_days)
    if last_full_dt < cutoff:
        logger.info(
            f"Last full scan was {last_full} (>{config.full_scan_interval_days}d ago) "
            "— running full scan."
        )
        return True

    logger.info(f"Last full scan was {last_full} — running delta scan.")
    return False


def main():
    config = CollectorConfig()

    logger.info("Connecting to Neo4j...")
    neo4j = Neo4jClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)
    neo4j.init_schema()

    logger.info("Connecting to Microsoft Graph (app-only)...")
    graph = GraphClient(
        config.graph_api.tenant_id,
        config.graph_api.client_id,
        config.graph_api.client_secret,
        delay_ms=config.delay_ms,
    )

    tenant_domain = graph.get_tenant_domain()
    logger.info(f"Tenant domain: {tenant_domain}")

    is_full = _should_full_scan(config, neo4j)
    scan_type = "full" if is_full else "delta"
    run_id = neo4j.create_scan_run(scan_type)
    logger.info(f"Scan run: {run_id} (type={scan_type})")

    total = 0

    try:
        # OneDrive audit
        logger.info("=== Starting OneDrive Audit ===")
        users = graph.get_users()
        logger.info(f"Found {len(users)} users.")

        users_filter = os.environ.get("USERS_TO_AUDIT", "")
        if users_filter:
            filter_upns = [u.strip() for u in users_filter.split(",")]
            users = [u for u in users if u.get("userPrincipalName") in filter_upns]
            logger.info(f"Filtered to {len(users)} users: {filter_upns}")

        for i, user in enumerate(users, 1):
            upn = user.get("userPrincipalName", "?")
            logger.info(
                f"[{i}/{len(users)}] OneDrive: {user.get('displayName', '?')} ({upn})"
            )
            count = collect_onedrive_user(
                graph, neo4j, user, run_id, tenant_domain, is_full
            )
            total += count

        # SharePoint audit
        if os.environ.get("SKIP_SHAREPOINT", "").lower() not in ("1", "true", "yes"):
            logger.info("=== Starting SharePoint Audit ===")
            sp_count = collect_sharepoint_sites(
                graph, neo4j, run_id, tenant_domain, is_full
            )
            total += sp_count
        else:
            logger.info("Skipping SharePoint audit (SKIP_SHAREPOINT is set)")

        neo4j.complete_scan_run(run_id)
        logger.info(f"Collection complete. Total shared items: {total}")
    except Exception:
        logger.exception("Collection failed — marking scan run as failed")
        neo4j.execute(
            "MATCH (r:ScanRun {runId: $runId}) SET r.status = 'failed'",
            {"runId": run_id},
        )
        raise
    finally:
        neo4j.close()


if __name__ == "__main__":
    main()
```

**Step 2: Update `collect_onedrive_user` to accept `is_full` and use delta**

In `src/collector/onedrive.py`, update the signature and add delta path:

```python
from collector.delta import delta_scan_drive

# ... existing imports ...

def collect_onedrive_user(
    graph: GraphClient,
    neo4j: Neo4jClient,
    user: dict,
    run_id: str,
    tenant_domain: str,
    is_full: bool = True,
) -> int:
    """Collect all sharing permissions for one user's OneDrive. Returns item count."""
    upn = user["userPrincipalName"]
    display_name = user["displayName"]
    user_id = user["id"]

    drive = graph.get_user_drive(user_id)
    if not drive:
        logger.warning(f"No OneDrive for {upn} — skipping.")
        return 0

    drive_id = drive["id"]
    site_id = f"onedrive-{user_id}"

    neo4j.merge_user(upn, display_name, "internal")
    neo4j.merge_site(site_id, display_name, drive.get("webUrl", ""), "OneDrive")
    neo4j.merge_owns(upn, site_id)

    if is_full:
        count = _walk_drive_items(
            graph, neo4j, drive_id, "root", "",
            site_id, upn, tenant_domain, run_id,
        )
        # Seed delta link for next scan
        try:
            link = graph.seed_delta_link(drive_id)
            neo4j.save_delta_link(drive_id, link)
        except Exception as e:
            logger.warning(f"Could not seed delta link for {upn}: {e}")
    else:
        delta_link = neo4j.get_delta_link(drive_id)
        if delta_link:
            count = delta_scan_drive(
                graph, neo4j, drive_id, delta_link,
                site_id, upn, tenant_domain, run_id,
            )
        else:
            logger.info(f"  No delta link for {upn} — falling back to full walk")
            count = _walk_drive_items(
                graph, neo4j, drive_id, "root", "",
                site_id, upn, tenant_domain, run_id,
            )
            try:
                link = graph.seed_delta_link(drive_id)
                neo4j.save_delta_link(drive_id, link)
            except Exception as e:
                logger.warning(f"Could not seed delta link for {upn}: {e}")

    logger.info(f"OneDrive {display_name} ({upn}): {count} shared items")
    return count
```

**Step 3: Update `collect_sharepoint_sites` similarly**

In `src/collector/sharepoint.py`, add the `is_full` parameter and delta path:

```python
from collector.delta import delta_scan_drive

# ... existing imports ...

def collect_sharepoint_sites(
    graph: GraphClient,
    neo4j: Neo4jClient,
    run_id: str,
    tenant_domain: str,
    is_full: bool = True,
) -> int:
    """Collect all sharing permissions across SharePoint sites. Returns total item count."""
    sites = graph.get_all_sites()

    # Filter out personal OneDrive sites
    sites = [s for s in sites if "-my.sharepoint.com" not in (s.get("webUrl") or "")]
    # Filter out sites without display names
    sites = [s for s in sites if s.get("displayName")]

    logger.info(f"Found {len(sites)} SharePoint sites to audit.")
    total = 0

    for i, site in enumerate(sites, 1):
        site_id = site["id"]
        site_name = site.get("displayName", site.get("webUrl", "Unknown"))
        site_url = site.get("webUrl", "")

        logger.info(f"[{i}/{len(sites)}] SharePoint: {site_name}")

        neo4j.merge_site(site_id, site_name, site_url, "SharePoint")

        try:
            drives = graph.get_site_drives(site_id)
        except Exception as e:
            logger.warning(f"Could not access drives for site {site_name}: {e}")
            continue

        for drive in drives:
            drive_id = drive["id"]

            # Determine owner (best effort)
            owner_email = ""
            owner = drive.get("owner", {})
            if owner.get("user", {}).get("email"):
                owner_email = owner["user"]["email"]
                neo4j.merge_user(
                    owner_email, owner["user"].get("displayName", ""), "internal"
                )
                neo4j.merge_owns(owner_email, site_id)

            if is_full:
                count = _walk_drive_items(
                    graph, neo4j, drive_id, "root", "",
                    site_id, owner_email, tenant_domain, run_id,
                )
                try:
                    link = graph.seed_delta_link(drive_id)
                    neo4j.save_delta_link(drive_id, link)
                except Exception as e:
                    logger.warning(
                        f"Could not seed delta link for drive {drive_id}: {e}"
                    )
            else:
                delta_link = neo4j.get_delta_link(drive_id)
                if delta_link:
                    count = delta_scan_drive(
                        graph, neo4j, drive_id, delta_link,
                        site_id, owner_email, tenant_domain, run_id,
                    )
                else:
                    logger.info(f"  No delta link for drive {drive_id} — full walk")
                    count = _walk_drive_items(
                        graph, neo4j, drive_id, "root", "",
                        site_id, owner_email, tenant_domain, run_id,
                    )
                    try:
                        link = graph.seed_delta_link(drive_id)
                        neo4j.save_delta_link(drive_id, link)
                    except Exception as e:
                        logger.warning(
                            f"Could not seed delta link for drive {drive_id}: {e}"
                        )

            total += count

        logger.info(f"  {site_name}: done. Running total: {total}")

    return total
```

**Step 4: Update existing tests for new `is_full` parameter**

In `tests/collector/test_onedrive.py`, update calls:

The existing tests call `collect_onedrive_user(graph, neo4j, user, "run-1", "test.dk")` — these still work because `is_full=True` is the default. No changes needed.

**Step 5: Run full test suite**

Run: `python -m pytest tests/ -x -q`
Expected: All pass

**Step 6: Lint and format**

Run: `python -m ruff check src/ && python -m ruff format src/`

**Step 7: Commit**

```bash
git add src/collector/__main__.py src/collector/onedrive.py src/collector/sharepoint.py
git commit -m "feat(collector): wire delta scan into orchestrator"
```

---

### Task 7: Final integration test and cleanup

**Step 1: Run full test suite**

Run: `python -m pytest tests/ -x -v`
Expected: All pass

**Step 2: Lint and format everything**

Run: `python -m ruff check src/ tests/ && python -m ruff format src/ tests/`

**Step 3: Commit any formatting changes**

```bash
git add -A
git commit -m "style: apply ruff formatting to delta query changes"
```

(Skip if nothing changed)

**Step 4: Final commit with all files**

Verify with `git log --oneline -7` that the commit history looks clean.
