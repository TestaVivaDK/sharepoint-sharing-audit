# Sharing Audit Pipeline — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a Python-based sharing audit pipeline with Neo4j graph database, deployed via Docker + Helm.

**Architecture:** Monorepo with shared library, collector service, and reporter service. Two Docker images. One Helm chart with Neo4j StatefulSet + two CronJobs.

**Tech Stack:** Python 3.12, Neo4j 5 Community, msgraph-sdk, neo4j (driver), Jinja2, Chromium headless, Docker, Helm 3.

---

## Project Structure

```
sharepoint-dashboard/
  src/
    shared/
      __init__.py
      config.py              # Environment-based configuration
      classify.py            # Sharing type, shared-with, risk classification
      neo4j_client.py        # Neo4j connection, schema init, MERGE helpers
    collector/
      __init__.py
      __main__.py            # Entry point: python -m collector
      graph_client.py        # Microsoft Graph API auth + helpers
      onedrive.py            # OneDrive user enumeration + drive walking
      sharepoint.py          # SharePoint site enumeration + drive walking
    reporter/
      __init__.py
      __main__.py            # Entry point: python -m reporter
      queries.py             # Neo4j read queries
      csv_export.py          # CSV report generation
      pdf_export.py          # HTML rendering + PDF conversion
      templates/
        report.html.j2       # Jinja2 template for PDF reports
  tests/
    conftest.py              # Shared fixtures (Neo4j test container, mocks)
    shared/
      test_classify.py
      test_neo4j_client.py
    collector/
      test_graph_client.py
      test_onedrive.py
      test_sharepoint.py
    reporter/
      test_queries.py
      test_csv_export.py
      test_pdf_export.py
  docker/
    Dockerfile.collector
    Dockerfile.reporter
  helm/
    sharing-audit/
      Chart.yaml
      values.yaml
      templates/
        neo4j-statefulset.yaml
        neo4j-service.yaml
        collector-cronjob.yaml
        reporter-cronjob.yaml
        secret.yaml
        pvc-reports.yaml
        configmap.yaml
  docker-compose.yml         # Local dev: Neo4j + collector + reporter
  pyproject.toml
  .env.example
```

---

### Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `src/shared/__init__.py`
- Create: `src/shared/config.py`
- Create: `src/collector/__init__.py`
- Create: `src/reporter/__init__.py`
- Create: `tests/conftest.py`
- Create: `.env.example`

**Step 1: Create pyproject.toml**

```toml
[project]
name = "sharing-audit"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "neo4j>=5.0,<6.0",
    "msgraph-sdk>=1.0,<2.0",
    "azure-identity>=1.15,<2.0",
    "jinja2>=3.1,<4.0",
    "python-dotenv>=1.0,<2.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "testcontainers[neo4j]>=4.0",
    "responses>=0.25",
]

[build-system]
requires = ["setuptools>=75.0"]
build-backend = "setuptools.backends._legacy:_Backend"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
```

**Step 2: Create .env.example**

```env
# Microsoft Graph API (app-only auth)
TENANT_ID=your-tenant-id
CLIENT_ID=your-client-id
CLIENT_SECRET=your-client-secret

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-neo4j-password

# Collector settings
DELAY_MS=100

# Reporter settings
SENSITIVE_FOLDERS=ledelse,ledelsen,datarum,løn
TENANT_DOMAIN=testaviva.dk
REPORT_OUTPUT_DIR=./reports
```

**Step 3: Create src/shared/config.py**

```python
"""Environment-based configuration for all services."""

import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class GraphApiConfig:
    tenant_id: str = field(default_factory=lambda: os.environ["TENANT_ID"])
    client_id: str = field(default_factory=lambda: os.environ["CLIENT_ID"])
    client_secret: str = field(default_factory=lambda: os.environ["CLIENT_SECRET"])


@dataclass(frozen=True)
class Neo4jConfig:
    uri: str = field(default_factory=lambda: os.environ.get("NEO4J_URI", "bolt://localhost:7687"))
    user: str = field(default_factory=lambda: os.environ.get("NEO4J_USER", "neo4j"))
    password: str = field(default_factory=lambda: os.environ["NEO4J_PASSWORD"])


@dataclass(frozen=True)
class CollectorConfig:
    graph_api: GraphApiConfig = field(default_factory=GraphApiConfig)
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    delay_ms: int = field(default_factory=lambda: int(os.environ.get("DELAY_MS", "100")))


@dataclass(frozen=True)
class ReporterConfig:
    neo4j: Neo4jConfig = field(default_factory=Neo4jConfig)
    sensitive_folders: list[str] = field(
        default_factory=lambda: os.environ.get("SENSITIVE_FOLDERS", "ledelse,ledelsen,datarum,løn").split(",")
    )
    tenant_domain: str = field(default_factory=lambda: os.environ.get("TENANT_DOMAIN", ""))
    output_dir: str = field(default_factory=lambda: os.environ.get("REPORT_OUTPUT_DIR", "./reports"))
```

**Step 4: Create empty __init__.py files**

```python
# src/shared/__init__.py — empty
# src/collector/__init__.py — empty
# src/reporter/__init__.py — empty
```

**Step 5: Create tests/conftest.py**

```python
"""Shared test fixtures."""

import pytest


@pytest.fixture
def tenant_domain():
    return "testaviva.dk"
```

**Step 6: Install dependencies and verify**

Run: `pip install -e ".[dev]"`
Expected: Install succeeds.

Run: `pytest --collect-only`
Expected: "no tests ran" (0 collected), no import errors.

**Step 7: Commit**

```bash
git add pyproject.toml .env.example src/ tests/conftest.py
git commit -m "feat: project scaffold with config and dependencies"
```

---

### Task 2: Classification Helpers

**Files:**
- Create: `src/shared/classify.py`
- Create: `tests/shared/__init__.py`
- Create: `tests/shared/test_classify.py`

**Step 1: Write tests for classification**

```python
# tests/shared/test_classify.py
"""Tests for sharing classification helpers."""

from shared.classify import get_sharing_type, get_shared_with_info, get_risk_level


class TestGetSharingType:
    def test_anonymous_link(self):
        perm = {"link": {"scope": "anonymous"}}
        assert get_sharing_type(perm) == "Link-Anyone"

    def test_organization_link(self):
        perm = {"link": {"scope": "organization"}}
        assert get_sharing_type(perm) == "Link-Organization"

    def test_specific_people_link(self):
        perm = {"link": {"scope": "users"}}
        assert get_sharing_type(perm) == "Link-SpecificPeople"

    def test_link_no_scope(self):
        perm = {"link": {}}
        assert get_sharing_type(perm) == "Link-SpecificPeople"

    def test_group_permission(self):
        perm = {"grantedToV2": {"group": {"displayName": "Marketing"}}}
        assert get_sharing_type(perm) == "Group"

    def test_user_permission(self):
        perm = {"grantedToV2": {"user": {"email": "a@test.dk"}}}
        assert get_sharing_type(perm) == "User"


class TestGetSharedWithInfo:
    def test_anonymous(self):
        perm = {"link": {"scope": "anonymous"}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with"] == "Anyone with the link"
        assert info["shared_with_type"] == "Anonymous"

    def test_organization(self):
        perm = {"link": {"scope": "organization"}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Internal"

    def test_external_email(self):
        perm = {"grantedToV2": {"user": {"email": "ext@gmail.com"}}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "External"

    def test_internal_email(self):
        perm = {"grantedToV2": {"user": {"email": "a@test.dk"}}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Internal"

    def test_display_name_only_is_internal(self):
        """Display names without @ should not be classified as external."""
        perm = {
            "link": {"scope": "users"},
            "grantedToIdentitiesV2": [{"user": {"displayName": "Algoritmen"}}],
        }
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Internal"

    def test_guest_ext_hash(self):
        perm = {"grantedToV2": {"user": {"email": "ext_gmail.com#EXT#@test.dk"}}}
        info = get_shared_with_info(perm, "test.dk")
        assert info["shared_with_type"] == "Guest"


class TestGetRiskLevel:
    def test_anonymous_is_high(self):
        assert get_risk_level("Link-Anyone", "Anonymous", "") == "HIGH"

    def test_external_is_high(self):
        assert get_risk_level("Link-SpecificPeople", "External", "") == "HIGH"

    def test_guest_is_high(self):
        assert get_risk_level("User", "Guest", "") == "HIGH"

    def test_sensitive_folder_is_high(self):
        assert get_risk_level("Link-SpecificPeople", "Internal", "/Documents/Ledelse/Budget.xlsx") == "HIGH"

    def test_sensitive_folder_løn(self):
        assert get_risk_level("Link-SpecificPeople", "Internal", "/Documents/Løn/salaries.xlsx") == "HIGH"

    def test_sensitive_folder_datarum(self):
        assert get_risk_level("User", "Internal", "/Datarum/contracts.pdf") == "HIGH"

    def test_org_wide_is_medium(self):
        assert get_risk_level("Link-Organization", "Internal", "") == "MEDIUM"

    def test_specific_internal_is_low(self):
        assert get_risk_level("Link-SpecificPeople", "Internal", "/Documents/report.xlsx") == "LOW"

    def test_user_internal_is_low(self):
        assert get_risk_level("User", "Internal", "/Documents/notes.docx") == "LOW"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/shared/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.classify'`

**Step 3: Implement classify.py**

```python
# src/shared/classify.py
"""Classification helpers for sharing permissions."""

import re

SENSITIVE_PATTERN = re.compile(r"(?i)(ledelse[n]?|datarum|l[øo]n)(/|$)")


def get_sharing_type(permission: dict) -> str:
    """Classify a Graph API permission object into a sharing type string."""
    link = permission.get("link")
    if link:
        scope = link.get("scope", "")
        match scope:
            case "anonymous":
                return "Link-Anyone"
            case "organization":
                return "Link-Organization"
            case "users":
                return "Link-SpecificPeople"
            case _:
                return "Link-SpecificPeople"  # No scope = specific people

    granted = permission.get("grantedToV2", {})
    if granted.get("group"):
        return "Group"
    if granted.get("user"):
        return "User"

    # Legacy field
    if permission.get("grantedTo", {}).get("user"):
        return "User"

    return "Unknown"


def get_shared_with_info(permission: dict, tenant_domain: str) -> dict:
    """Extract who the item is shared with and classify audience type."""
    shared_with = ""
    shared_with_type = "Unknown"

    link = permission.get("link")
    if link:
        scope = link.get("scope", "")
        if scope == "anonymous":
            return {"shared_with": "Anyone with the link", "shared_with_type": "Anonymous"}
        if scope == "organization":
            return {"shared_with": "All organization members", "shared_with_type": "Internal"}

        # Specific people link — check identities
        identities_v2 = permission.get("grantedToIdentitiesV2", [])
        if identities_v2:
            names: list[str] = []
            emails: list[str] = []
            for identity in identities_v2:
                user = identity.get("user", {})
                email = user.get("email", "")
                display = user.get("displayName", "")
                if email:
                    names.append(email)
                    emails.append(email)
                elif display:
                    names.append(display)
            shared_with = "; ".join(names)

            has_guest = any("#EXT#" in e for e in emails)
            has_external = any(
                tenant_domain and not e.endswith(f"@{tenant_domain}")
                for e in emails
                if "#EXT#" not in e
            )
            if has_guest:
                shared_with_type = "Guest"
            elif has_external:
                shared_with_type = "External"
            else:
                shared_with_type = "Internal"
            return {"shared_with": shared_with, "shared_with_type": shared_with_type}

        return {"shared_with": "Specific people (details unavailable)", "shared_with_type": "Internal"}

    # Direct user/group grant
    granted = permission.get("grantedToV2", {})
    group = granted.get("group")
    if group:
        return {"shared_with": group.get("displayName", "Unknown Group"), "shared_with_type": "Internal"}

    user = granted.get("user") or permission.get("grantedTo", {}).get("user")
    if user:
        email = user.get("email", "")
        display = user.get("displayName", "Unknown User")
        shared_with = email or display

        if "#EXT#" in email:
            shared_with_type = "Guest"
        elif email and tenant_domain and not email.endswith(f"@{tenant_domain}"):
            shared_with_type = "External"
        else:
            shared_with_type = "Internal"
        return {"shared_with": shared_with, "shared_with_type": shared_with_type}

    return {"shared_with": shared_with, "shared_with_type": shared_with_type}


def get_risk_level(sharing_type: str, shared_with_type: str, item_path: str) -> str:
    """Assign HIGH/MEDIUM/LOW risk based on sharing type, audience, and file path."""
    if shared_with_type in ("Anonymous", "External", "Guest") or sharing_type == "Link-Anyone":
        return "HIGH"
    if SENSITIVE_PATTERN.search(item_path):
        return "HIGH"
    if sharing_type == "Link-Organization":
        return "MEDIUM"
    return "LOW"


def get_permission_role(permission: dict) -> str:
    """Extract role (Read, Write, Owner) from a permission object."""
    roles = permission.get("roles", [])
    if "owner" in roles:
        return "Owner"
    if "write" in roles:
        return "Write"
    if "read" in roles:
        return "Read"
    link = permission.get("link", {})
    if link.get("type") == "edit":
        return "Write"
    if link.get("type") == "view":
        return "Read"
    return ", ".join(roles) if roles else "Unknown"


def is_teams_chat_file(item_path: str) -> bool:
    """Check if an item path belongs to the Teams chat files folder."""
    return bool(re.search(r"Microsoft Teams[ -]chatfiler|Microsoft Teams Chat Files", item_path, re.IGNORECASE))
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/shared/test_classify.py -v`
Expected: All 15 tests PASS.

**Step 5: Commit**

```bash
git add src/shared/classify.py tests/shared/
git commit -m "feat: sharing classification helpers with full test coverage"
```

---

### Task 3: Neo4j Client — Schema & MERGE Helpers

**Files:**
- Create: `src/shared/neo4j_client.py`
- Create: `tests/shared/test_neo4j_client.py`

**Step 1: Write tests for Neo4j client**

```python
# tests/shared/test_neo4j_client.py
"""Tests for Neo4j client — requires Neo4j (use testcontainers or local instance)."""

import os
import pytest
from shared.neo4j_client import Neo4jClient

# Skip if no Neo4j available (CI can set NEO4J_TEST_URI)
NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7687")
NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "testpassword")

try:
    from neo4j import GraphDatabase
    _driver = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    _driver.verify_connectivity()
    _driver.close()
    NEO4J_AVAILABLE = True
except Exception:
    NEO4J_AVAILABLE = False

pytestmark = pytest.mark.skipif(not NEO4J_AVAILABLE, reason="Neo4j not available")


@pytest.fixture
def client():
    c = Neo4jClient(NEO4J_URI, "neo4j", NEO4J_PASSWORD)
    c.init_schema()
    yield c
    # Clean up after each test
    c.execute("MATCH (n) DETACH DELETE n")
    c.close()


class TestScanRun:
    def test_create_scan_run(self, client):
        run_id = client.create_scan_run()
        assert run_id is not None

    def test_complete_scan_run(self, client):
        run_id = client.create_scan_run()
        client.complete_scan_run(run_id)
        result = client.execute(
            "MATCH (r:ScanRun {runId: $runId}) RETURN r.status AS status",
            {"runId": run_id},
        )
        assert result[0]["status"] == "completed"


class TestMergeNodes:
    def test_merge_user(self, client):
        client.merge_user("a@test.dk", "Alice", "internal")
        result = client.execute("MATCH (u:User {email: 'a@test.dk'}) RETURN u.displayName AS name")
        assert result[0]["name"] == "Alice"

    def test_merge_user_idempotent(self, client):
        client.merge_user("a@test.dk", "Alice", "internal")
        client.merge_user("a@test.dk", "Alice Updated", "internal")
        result = client.execute("MATCH (u:User {email: 'a@test.dk'}) RETURN count(u) AS c")
        assert result[0]["c"] == 1

    def test_merge_site(self, client):
        client.merge_site("site-1", "Marketing", "https://example.com", "SharePoint")
        result = client.execute("MATCH (s:Site {siteId: 'site-1'}) RETURN s.name AS name")
        assert result[0]["name"] == "Marketing"

    def test_merge_file(self, client):
        client.merge_file("drive-1", "item-1", "/doc.xlsx", "https://example.com/doc.xlsx", "File")
        result = client.execute(
            "MATCH (f:File {driveId: 'drive-1', itemId: 'item-1'}) RETURN f.path AS path"
        )
        assert result[0]["path"] == "/doc.xlsx"


class TestRelationships:
    def test_merge_shared_with(self, client):
        client.merge_file("d1", "i1", "/doc.xlsx", "https://x.com/doc", "File")
        client.merge_user("ext@gmail.com", "External", "external")
        client.merge_shared_with(
            drive_id="d1", item_id="i1",
            user_email="ext@gmail.com",
            sharing_type="Link-SpecificPeople",
            shared_with_type="External",
            role="Read",
            risk_level="HIGH",
            created_date_time="2025-01-01T00:00:00Z",
            run_id="run-1",
        )
        result = client.execute("""
            MATCH (f:File)-[s:SHARED_WITH]->(u:User {email: 'ext@gmail.com'})
            RETURN s.riskLevel AS risk
        """)
        assert result[0]["risk"] == "HIGH"

    def test_merge_contains(self, client):
        client.merge_site("site-1", "Test", "https://x.com", "SharePoint")
        client.merge_file("d1", "i1", "/doc.xlsx", "https://x.com/doc", "File")
        client.merge_contains("site-1", "d1", "i1")
        result = client.execute("""
            MATCH (s:Site {siteId: 'site-1'})-[:CONTAINS]->(f:File)
            RETURN f.path AS path
        """)
        assert result[0]["path"] == "/doc.xlsx"

    def test_merge_owns(self, client):
        client.merge_user("a@test.dk", "Alice", "internal")
        client.merge_site("site-1", "Test", "https://x.com", "OneDrive")
        client.merge_owns("a@test.dk", "site-1")
        result = client.execute("""
            MATCH (u:User)-[:OWNS]->(s:Site)
            RETURN u.email AS email, s.siteId AS site
        """)
        assert result[0]["email"] == "a@test.dk"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/shared/test_neo4j_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.neo4j_client'`

**Step 3: Implement neo4j_client.py**

```python
# src/shared/neo4j_client.py
"""Neo4j connection, schema initialization, and MERGE helpers."""

import uuid
from datetime import datetime, timezone

from neo4j import GraphDatabase


class Neo4jClient:
    def __init__(self, uri: str, user: str, password: str):
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._driver.verify_connectivity()

    def close(self):
        self._driver.close()

    def execute(self, query: str, params: dict | None = None) -> list[dict]:
        """Execute a Cypher query and return results as list of dicts."""
        with self._driver.session() as session:
            result = session.run(query, params or {})
            return [record.data() for record in result]

    def init_schema(self):
        """Create constraints and indexes for the graph schema."""
        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (u:User) REQUIRE u.email IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Site) REQUIRE s.siteId IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (r:ScanRun) REQUIRE r.runId IS UNIQUE",
            "CREATE INDEX IF NOT EXISTS FOR (f:File) ON (f.driveId, f.itemId)",
        ]
        for c in constraints:
            self.execute(c)

    def create_scan_run(self) -> str:
        """Create a new ScanRun node. Returns the runId."""
        run_id = str(uuid.uuid4())
        self.execute(
            "CREATE (r:ScanRun {runId: $runId, timestamp: $ts, status: 'running'})",
            {"runId": run_id, "ts": datetime.now(timezone.utc).isoformat()},
        )
        return run_id

    def complete_scan_run(self, run_id: str):
        """Mark a ScanRun as completed."""
        self.execute(
            "MATCH (r:ScanRun {runId: $runId}) SET r.status = 'completed'",
            {"runId": run_id},
        )

    def merge_user(self, email: str, display_name: str, source: str):
        """Upsert a User node."""
        self.execute(
            "MERGE (u:User {email: $email}) SET u.displayName = $name, u.source = $source",
            {"email": email, "name": display_name, "source": source},
        )

    def merge_site(self, site_id: str, name: str, web_url: str, source: str):
        """Upsert a Site node."""
        self.execute(
            "MERGE (s:Site {siteId: $siteId}) SET s.name = $name, s.webUrl = $url, s.source = $source",
            {"siteId": site_id, "name": name, "url": web_url, "source": source},
        )

    def merge_file(self, drive_id: str, item_id: str, path: str, web_url: str, file_type: str):
        """Upsert a File node."""
        self.execute(
            """MERGE (f:File {driveId: $driveId, itemId: $itemId})
               SET f.path = $path, f.webUrl = $url, f.type = $type""",
            {"driveId": drive_id, "itemId": item_id, "path": path, "url": web_url, "type": file_type},
        )

    def merge_shared_with(
        self,
        drive_id: str,
        item_id: str,
        user_email: str,
        sharing_type: str,
        shared_with_type: str,
        role: str,
        risk_level: str,
        created_date_time: str,
        run_id: str,
    ):
        """Upsert a SHARED_WITH relationship between a File and a User."""
        self.execute(
            """MATCH (f:File {driveId: $driveId, itemId: $itemId})
               MATCH (u:User {email: $email})
               MERGE (f)-[s:SHARED_WITH]->(u)
               SET s.sharingType = $sharingType,
                   s.sharedWithType = $sharedWithType,
                   s.role = $role,
                   s.riskLevel = $riskLevel,
                   s.createdDateTime = $created,
                   s.lastSeenRunId = $runId""",
            {
                "driveId": drive_id,
                "itemId": item_id,
                "email": user_email,
                "sharingType": sharing_type,
                "sharedWithType": shared_with_type,
                "role": role,
                "riskLevel": risk_level,
                "created": created_date_time,
                "runId": run_id,
            },
        )

    def merge_contains(self, site_id: str, drive_id: str, item_id: str):
        """Create CONTAINS relationship between Site and File."""
        self.execute(
            """MATCH (s:Site {siteId: $siteId})
               MATCH (f:File {driveId: $driveId, itemId: $itemId})
               MERGE (s)-[:CONTAINS]->(f)""",
            {"siteId": site_id, "driveId": drive_id, "itemId": item_id},
        )

    def merge_owns(self, user_email: str, site_id: str):
        """Create OWNS relationship between User and Site."""
        self.execute(
            """MATCH (u:User {email: $email})
               MATCH (s:Site {siteId: $siteId})
               MERGE (u)-[:OWNS]->(s)""",
            {"email": user_email, "siteId": site_id},
        )

    def mark_file_found(self, drive_id: str, item_id: str, run_id: str):
        """Link a File to a ScanRun via FOUND relationship."""
        self.execute(
            """MATCH (r:ScanRun {runId: $runId})
               MATCH (f:File {driveId: $driveId, itemId: $itemId})
               MERGE (r)-[:FOUND]->(f)""",
            {"runId": run_id, "driveId": drive_id, "itemId": item_id},
        )
```

**Step 4: Start Neo4j for testing and run tests**

Run: `docker run -d --name neo4j-test -p 7687:7687 -e NEO4J_AUTH=neo4j/testpassword neo4j:5-community`

Run: `NEO4J_TEST_URI=bolt://localhost:7687 NEO4J_TEST_PASSWORD=testpassword pytest tests/shared/test_neo4j_client.py -v`
Expected: All 9 tests PASS.

**Step 5: Commit**

```bash
git add src/shared/neo4j_client.py tests/shared/test_neo4j_client.py
git commit -m "feat: Neo4j client with schema init and MERGE helpers"
```

---

### Task 4: Graph API Client

**Files:**
- Create: `src/collector/graph_client.py`
- Create: `tests/collector/__init__.py`
- Create: `tests/collector/test_graph_client.py`

**Step 1: Write tests**

```python
# tests/collector/test_graph_client.py
"""Tests for Graph API client with mocked HTTP responses."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collector.graph_client import GraphClient


class TestGraphClient:
    def test_get_users(self):
        """Test user enumeration filters licensed enabled users."""
        mock_users = [
            {"id": "1", "displayName": "Alice", "userPrincipalName": "a@test.dk",
             "accountEnabled": True, "assignedLicenses": [{"skuId": "x"}]},
            {"id": "2", "displayName": "Disabled", "userPrincipalName": "d@test.dk",
             "accountEnabled": False, "assignedLicenses": [{"skuId": "x"}]},
            {"id": "3", "displayName": "Unlicensed", "userPrincipalName": "u@test.dk",
             "accountEnabled": True, "assignedLicenses": []},
        ]
        client = GraphClient.__new__(GraphClient)
        client._client = MagicMock()
        client._make_request = MagicMock(return_value={"value": mock_users})

        users = client.get_users()
        # Should filter to only licensed + enabled
        assert len(users) == 1
        assert users[0]["userPrincipalName"] == "a@test.dk"

    def test_get_drive_items_returns_children(self):
        """Test recursive drive item listing."""
        mock_children = {
            "value": [
                {"id": "item-1", "name": "doc.xlsx", "webUrl": "https://x.com/doc",
                 "file": {"mimeType": "application/xlsx"}},
                {"id": "item-2", "name": "Folder", "webUrl": "https://x.com/folder",
                 "folder": {"childCount": 0}},
            ]
        }
        client = GraphClient.__new__(GraphClient)
        client._make_request = MagicMock(return_value=mock_children)
        client.delay_ms = 0

        items = list(client.get_drive_children("drive-1", "root"))
        assert len(items) == 2
        assert items[0]["name"] == "doc.xlsx"

    def test_get_item_permissions_filters_inherited(self):
        """Test that inherited permissions are filtered out."""
        mock_perms = {
            "value": [
                {"id": "p1", "link": {"scope": "anonymous"}, "inheritedFrom": {}},
                {"id": "p2", "link": {"scope": "organization"},
                 "inheritedFrom": {"driveId": "d1", "path": "/root"}},
            ]
        }
        client = GraphClient.__new__(GraphClient)
        client._make_request = MagicMock(return_value=mock_perms)

        perms = client.get_item_permissions("drive-1", "item-1")
        # p2 has inheritedFrom with driveId, should be filtered
        assert len(perms) == 1
        assert perms[0]["id"] == "p1"
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/collector/test_graph_client.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement graph_client.py**

```python
# src/collector/graph_client.py
"""Microsoft Graph API client for collecting sharing data."""

import logging
import time

from azure.identity import ClientSecretCredential
from msgraph import GraphServiceClient
import httpx

logger = logging.getLogger(__name__)


class GraphClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, delay_ms: int = 100):
        self.delay_ms = delay_ms
        credential = ClientSecretCredential(tenant_id, client_id, client_secret)
        self._client = GraphServiceClient(credential)
        self._credential = credential
        self._token: str | None = None

    def _get_token(self) -> str:
        """Get or refresh the access token."""
        if not self._token:
            token = self._credential.get_token("https://graph.microsoft.com/.default")
            self._token = token.token
        return self._token

    def _make_request(self, url: str, params: dict | None = None) -> dict:
        """Make a GET request to the Graph API with retry logic."""
        headers = {"Authorization": f"Bearer {self._get_token()}"}
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
                if attempt < 3 and e.response.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        return {}

    def _make_paged_request(self, url: str, params: dict | None = None) -> list[dict]:
        """Follow @odata.nextLink pagination."""
        results = []
        while url:
            data = self._make_request(url, params)
            results.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            params = None  # nextLink includes params
            if self.delay_ms > 0:
                time.sleep(self.delay_ms / 1000)
        return results

    def get_tenant_domain(self) -> str:
        """Get the default verified domain for the tenant."""
        data = self._make_request("https://graph.microsoft.com/v1.0/organization")
        orgs = data.get("value", [])
        if orgs:
            for domain in orgs[0].get("verifiedDomains", []):
                if domain.get("isDefault"):
                    return domain["name"]
        return ""

    def get_users(self, upns: list[str] | None = None) -> list[dict]:
        """Get licensed, enabled users. If upns provided, fetch those specific users."""
        if upns:
            users = []
            for upn in upns:
                try:
                    data = self._make_request(f"https://graph.microsoft.com/v1.0/users/{upn}")
                    users.append(data)
                except Exception as e:
                    logger.warning(f"Could not find user {upn}: {e}")
            return users

        all_users = self._make_paged_request(
            "https://graph.microsoft.com/v1.0/users",
            {"$filter": "accountEnabled eq true", "$select": "id,displayName,userPrincipalName,accountEnabled,assignedLicenses"},
        )
        return [u for u in all_users if u.get("assignedLicenses")]

    def get_user_drive(self, user_id: str) -> dict | None:
        """Get a user's default OneDrive drive."""
        try:
            return self._make_request(f"https://graph.microsoft.com/v1.0/users/{user_id}/drive")
        except Exception as e:
            logger.warning(f"No OneDrive for user {user_id}: {e}")
            return None

    def get_all_sites(self) -> list[dict]:
        """Enumerate all SharePoint sites via getAllSites."""
        return self._make_paged_request(
            "https://graph.microsoft.com/v1.0/sites/getAllSites",
            {"$select": "id,displayName,webUrl", "$top": "1000"},
        )

    def get_site_drives(self, site_id: str) -> list[dict]:
        """Get all document libraries for a site."""
        return self._make_paged_request(
            f"https://graph.microsoft.com/v1.0/sites/{site_id}/drives",
        )

    def get_drive_children(self, drive_id: str, item_id: str) -> list[dict]:
        """Get children of a drive item."""
        data = self._make_request(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/children"
        )
        return data.get("value", [])

    def get_item_permissions(self, drive_id: str, item_id: str) -> list[dict]:
        """Get non-inherited permissions for a drive item."""
        data = self._make_request(
            f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{item_id}/permissions"
        )
        permissions = data.get("value", [])
        # Filter inherited permissions
        return [
            p for p in permissions
            if not (p.get("inheritedFrom", {}).get("driveId") or p.get("inheritedFrom", {}).get("path"))
        ]

    def throttle(self):
        """Pause between API calls."""
        if self.delay_ms > 0:
            time.sleep(self.delay_ms / 1000)
```

**Step 4: Run tests**

Run: `pytest tests/collector/test_graph_client.py -v`
Expected: All 3 tests PASS.

**Step 5: Commit**

```bash
git add src/collector/graph_client.py tests/collector/
git commit -m "feat: Graph API client with auth, pagination, and retry"
```

---

### Task 5: Collector — OneDrive & SharePoint Collection

**Files:**
- Create: `src/collector/onedrive.py`
- Create: `src/collector/sharepoint.py`
- Create: `src/collector/__main__.py`
- Create: `tests/collector/test_onedrive.py`

**Step 1: Write tests for OneDrive collector**

```python
# tests/collector/test_onedrive.py
"""Tests for OneDrive collection logic."""

from unittest.mock import MagicMock, call
from collector.onedrive import collect_onedrive_user


def make_mock_graph():
    mock = MagicMock()
    mock.get_user_drive.return_value = {
        "id": "drive-1", "webUrl": "https://x.com/drive"
    }
    mock.get_drive_children.return_value = [
        {"id": "item-1", "name": "doc.xlsx", "webUrl": "https://x.com/doc",
         "file": {"mimeType": "x"}, "folder": None},
    ]
    mock.get_item_permissions.return_value = [
        {"id": "p1", "link": {"scope": "organization"}, "roles": ["read"],
         "inheritedFrom": {}},
    ]
    mock.delay_ms = 0
    mock.throttle = MagicMock()
    return mock


class TestCollectOneDriveUser:
    def test_collects_permissions(self):
        graph = make_mock_graph()
        neo4j = MagicMock()
        user = {"id": "u1", "displayName": "Alice", "userPrincipalName": "a@test.dk"}

        count = collect_onedrive_user(graph, neo4j, user, "run-1", "test.dk")

        assert count == 1
        neo4j.merge_file.assert_called_once()
        neo4j.merge_shared_with.assert_called_once()

    def test_skips_user_without_drive(self):
        graph = MagicMock()
        graph.get_user_drive.return_value = None
        neo4j = MagicMock()
        user = {"id": "u1", "displayName": "Alice", "userPrincipalName": "a@test.dk"}

        count = collect_onedrive_user(graph, neo4j, user, "run-1", "test.dk")

        assert count == 0
        neo4j.merge_file.assert_not_called()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/collector/test_onedrive.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement onedrive.py**

```python
# src/collector/onedrive.py
"""OneDrive collection: enumerate users, walk drives, collect permissions."""

import logging

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from shared.classify import (
    get_sharing_type, get_shared_with_info, get_risk_level,
    get_permission_role,
)

logger = logging.getLogger(__name__)


def _walk_drive_items(
    graph: GraphClient,
    neo4j: Neo4jClient,
    drive_id: str,
    parent_id: str,
    parent_path: str,
    site_id: str,
    owner_email: str,
    tenant_domain: str,
    run_id: str,
) -> int:
    """Recursively walk drive items, collect permissions, write to Neo4j. Returns count."""
    count = 0
    try:
        children = graph.get_drive_children(drive_id, parent_id)
    except Exception as e:
        logger.warning(f"Could not list children of {parent_path}: {e}")
        return 0

    for item in children:
        item_path = f"{parent_path}/{item['name']}" if parent_path else f"/{item['name']}"
        item_type = "Folder" if item.get("folder") else "File"
        web_url = item.get("webUrl", "")

        try:
            permissions = graph.get_item_permissions(drive_id, item["id"])
        except Exception as e:
            logger.warning(f"Could not get permissions for {item_path}: {e}")
            permissions = []

        for perm in permissions:
            sharing_type = get_sharing_type(perm)
            shared_info = get_shared_with_info(perm, tenant_domain)
            role = get_permission_role(perm)
            risk = get_risk_level(sharing_type, shared_info["shared_with_type"], item_path)

            # Skip owner's own "owner" permission
            if role == "Owner" and shared_info["shared_with"] == owner_email:
                continue

            # Determine the "shared with" email for the User node
            shared_email = shared_info["shared_with"]
            if shared_info["shared_with_type"] == "Anonymous":
                shared_email = "anonymous"
            elif sharing_type == "Link-Organization":
                shared_email = "organization"

            neo4j.merge_file(drive_id, item["id"], item_path, web_url, item_type)
            neo4j.merge_user(shared_email, shared_info["shared_with"], shared_info["shared_with_type"])
            neo4j.merge_shared_with(
                drive_id=drive_id, item_id=item["id"],
                user_email=shared_email,
                sharing_type=sharing_type,
                shared_with_type=shared_info["shared_with_type"],
                role=role, risk_level=risk,
                created_date_time=perm.get("createdDateTime", ""),
                run_id=run_id,
            )
            neo4j.merge_contains(site_id, drive_id, item["id"])
            neo4j.mark_file_found(drive_id, item["id"], run_id)
            count += 1

        # Recurse into folders
        if item.get("folder") and item["folder"].get("childCount", 0) > 0:
            count += _walk_drive_items(
                graph, neo4j, drive_id, item["id"], item_path,
                site_id, owner_email, tenant_domain, run_id,
            )

        graph.throttle()

    return count


def collect_onedrive_user(
    graph: GraphClient,
    neo4j: Neo4jClient,
    user: dict,
    run_id: str,
    tenant_domain: str,
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

    count = _walk_drive_items(
        graph, neo4j, drive_id, "root", "",
        site_id, upn, tenant_domain, run_id,
    )

    logger.info(f"OneDrive {display_name} ({upn}): {count} shared items")
    return count
```

**Step 4: Implement sharepoint.py**

```python
# src/collector/sharepoint.py
"""SharePoint collection: enumerate sites, walk drives, collect permissions."""

import logging

from collector.graph_client import GraphClient
from shared.neo4j_client import Neo4jClient
from collector.onedrive import _walk_drive_items

logger = logging.getLogger(__name__)


def collect_sharepoint_sites(
    graph: GraphClient,
    neo4j: Neo4jClient,
    run_id: str,
    tenant_domain: str,
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
                neo4j.merge_user(owner_email, owner["user"].get("displayName", ""), "internal")
                neo4j.merge_owns(owner_email, site_id)

            count = _walk_drive_items(
                graph, neo4j, drive_id, "root", "",
                site_id, owner_email, tenant_domain, run_id,
            )
            total += count

        logger.info(f"  {site_name}: done. Running total: {total}")

    return total
```

**Step 5: Implement __main__.py**

```python
# src/collector/__main__.py
"""Collector entry point: python -m collector"""

import logging
import sys

from shared.config import CollectorConfig
from shared.neo4j_client import Neo4jClient
from collector.graph_client import GraphClient
from collector.onedrive import collect_onedrive_user
from collector.sharepoint import collect_sharepoint_sites

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


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

    run_id = neo4j.create_scan_run()
    logger.info(f"Scan run: {run_id}")

    total = 0

    # OneDrive audit
    logger.info("=== Starting OneDrive Audit ===")
    users = graph.get_users()
    logger.info(f"Found {len(users)} users.")

    for i, user in enumerate(users, 1):
        upn = user.get("userPrincipalName", "?")
        logger.info(f"[{i}/{len(users)}] OneDrive: {user.get('displayName', '?')} ({upn})")
        count = collect_onedrive_user(graph, neo4j, user, run_id, tenant_domain)
        total += count

    # SharePoint audit
    logger.info("=== Starting SharePoint Audit ===")
    sp_count = collect_sharepoint_sites(graph, neo4j, run_id, tenant_domain)
    total += sp_count

    neo4j.complete_scan_run(run_id)
    logger.info(f"Collection complete. Total shared items: {total}")
    neo4j.close()


if __name__ == "__main__":
    main()
```

**Step 6: Run tests**

Run: `pytest tests/collector/test_onedrive.py -v`
Expected: All 2 tests PASS.

**Step 7: Commit**

```bash
git add src/collector/ tests/collector/
git commit -m "feat: collector service — OneDrive + SharePoint collection into Neo4j"
```

---

### Task 6: Reporter — Neo4j Queries

**Files:**
- Create: `src/reporter/queries.py`
- Create: `tests/reporter/__init__.py`
- Create: `tests/reporter/test_queries.py`

**Step 1: Write tests**

```python
# tests/reporter/test_queries.py
"""Tests for reporter Neo4j queries."""

import os
import pytest
from shared.neo4j_client import Neo4jClient
from reporter.queries import get_sharing_data, get_latest_completed_run

NEO4J_URI = os.environ.get("NEO4J_TEST_URI", "bolt://localhost:7687")
NEO4J_PASSWORD = os.environ.get("NEO4J_TEST_PASSWORD", "testpassword")

try:
    from neo4j import GraphDatabase
    _driver = GraphDatabase.driver(NEO4J_URI, auth=("neo4j", NEO4J_PASSWORD))
    _driver.verify_connectivity()
    _driver.close()
    NEO4J_AVAILABLE = True
except Exception:
    NEO4J_AVAILABLE = False

pytestmark = pytest.mark.skipif(not NEO4J_AVAILABLE, reason="Neo4j not available")


@pytest.fixture
def client():
    c = Neo4jClient(NEO4J_URI, "neo4j", NEO4J_PASSWORD)
    c.init_schema()
    # Seed test data
    run_id = c.create_scan_run()
    c.complete_scan_run(run_id)
    c.merge_user("a@test.dk", "Alice", "internal")
    c.merge_user("ext@gmail.com", "External", "external")
    c.merge_site("site-1", "Alice", "https://x.com", "OneDrive")
    c.merge_owns("a@test.dk", "site-1")
    c.merge_file("d1", "i1", "/doc.xlsx", "https://x.com/doc", "File")
    c.merge_contains("site-1", "d1", "i1")
    c.merge_shared_with("d1", "i1", "ext@gmail.com", "Link-SpecificPeople",
                        "External", "Read", "HIGH", "2025-01-01", run_id)
    c.mark_file_found("d1", "i1", run_id)
    yield c, run_id
    c.execute("MATCH (n) DETACH DELETE n")
    c.close()


class TestGetLatestRun:
    def test_returns_completed_run(self, client):
        c, run_id = client
        result = get_latest_completed_run(c)
        assert result == run_id


class TestGetSharingData:
    def test_returns_sharing_records(self, client):
        c, run_id = client
        records = get_sharing_data(c, run_id)
        assert len(records) == 1
        r = records[0]
        assert r["risk_level"] == "HIGH"
        assert r["shared_with"] == "ext@gmail.com"
        assert r["owner_email"] == "a@test.dk"
        assert r["source"] == "OneDrive"
```

**Step 2: Run tests to verify they fail**

Run: `NEO4J_TEST_PASSWORD=testpassword pytest tests/reporter/test_queries.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement queries.py**

```python
# src/reporter/queries.py
"""Neo4j read queries for report generation."""

from shared.neo4j_client import Neo4jClient


def get_latest_completed_run(client: Neo4jClient) -> str | None:
    """Get the runId of the most recent completed ScanRun."""
    result = client.execute("""
        MATCH (r:ScanRun {status: 'completed'})
        RETURN r.runId AS runId
        ORDER BY r.timestamp DESC
        LIMIT 1
    """)
    return result[0]["runId"] if result else None


def get_sharing_data(client: Neo4jClient, run_id: str) -> list[dict]:
    """Get all sharing records for a given scan run, enriched with owner/site info."""
    result = client.execute("""
        MATCH (f:File)-[s:SHARED_WITH {lastSeenRunId: $runId}]->(u:User)
        MATCH (site:Site)-[:CONTAINS]->(f)
        OPTIONAL MATCH (owner:User)-[:OWNS]->(site)
        RETURN
            s.riskLevel AS risk_level,
            site.source AS source,
            f.path AS item_path,
            f.webUrl AS item_web_url,
            s.sharingType AS sharing_type,
            u.email AS shared_with,
            u.displayName AS shared_with_name,
            s.sharedWithType AS shared_with_type,
            s.role AS role,
            s.createdDateTime AS created_date_time,
            owner.email AS owner_email,
            owner.displayName AS owner_display_name,
            site.name AS site_name
        ORDER BY
            CASE s.riskLevel WHEN 'HIGH' THEN 0 WHEN 'MEDIUM' THEN 1 ELSE 2 END,
            owner.email, f.path
    """, {"runId": run_id})
    return result
```

**Step 4: Run tests**

Run: `NEO4J_TEST_PASSWORD=testpassword pytest tests/reporter/test_queries.py -v`
Expected: All 2 tests PASS.

**Step 5: Commit**

```bash
git add src/reporter/ tests/reporter/
git commit -m "feat: reporter Neo4j queries — sharing data retrieval"
```

---

### Task 7: Reporter — CSV & PDF Generation

**Files:**
- Create: `src/reporter/csv_export.py`
- Create: `src/reporter/pdf_export.py`
- Create: `src/reporter/templates/report.html.j2`
- Create: `src/reporter/__main__.py`
- Create: `tests/reporter/test_csv_export.py`
- Create: `tests/reporter/test_pdf_export.py`

**Step 1: Write tests for CSV export**

```python
# tests/reporter/test_csv_export.py
"""Tests for CSV report generation."""

import csv
import io
from reporter.csv_export import generate_csv


def make_records():
    return [
        {"risk_level": "HIGH", "source": "OneDrive", "item_path": "/doc.xlsx",
         "item_web_url": "https://x.com/doc", "sharing_type": "Link-Anyone",
         "shared_with": "Anyone with the link", "shared_with_type": "Anonymous",
         "role": "Read", "created_date_time": "2025-01-01", "owner_email": "a@test.dk"},
        {"risk_level": "LOW", "source": "SharePoint", "item_path": "/report.pdf",
         "item_web_url": "https://x.com/report", "sharing_type": "User",
         "shared_with": "b@test.dk", "shared_with_type": "Internal",
         "role": "Write", "created_date_time": "2025-02-01", "owner_email": "a@test.dk"},
    ]


class TestGenerateCsv:
    def test_generates_correct_columns(self, tmp_path):
        path = tmp_path / "test.csv"
        generate_csv(make_records(), str(path))

        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 2
        assert rows[0]["RiskLevel"] == "HIGH"
        assert "ItemPath" in reader.fieldnames

    def test_sorted_by_risk(self, tmp_path):
        records = list(reversed(make_records()))  # LOW first
        path = tmp_path / "test.csv"
        generate_csv(records, str(path))

        with open(path) as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert rows[0]["RiskLevel"] == "HIGH"
        assert rows[1]["RiskLevel"] == "LOW"
```

**Step 2: Implement csv_export.py**

```python
# src/reporter/csv_export.py
"""CSV report generation from Neo4j sharing data."""

import csv

CSV_COLUMNS = [
    ("RiskLevel", "risk_level"),
    ("Source", "source"),
    ("ItemPath", "item_path"),
    ("ItemWebUrl", "item_web_url"),
    ("SharingType", "sharing_type"),
    ("SharedWith", "shared_with"),
    ("SharedWithType", "shared_with_type"),
    ("Role", "role"),
    ("CreatedDateTime", "created_date_time"),
]

RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def generate_csv(records: list[dict], output_path: str) -> str:
    """Generate a CSV report sorted by risk level. Returns the output path."""
    sorted_records = sorted(records, key=lambda r: RISK_ORDER.get(r.get("risk_level", "LOW"), 2))

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[col for col, _ in CSV_COLUMNS])
        writer.writeheader()
        for record in sorted_records:
            writer.writerow({col: record.get(key, "") for col, key in CSV_COLUMNS})

    return output_path
```

**Step 3: Create the Jinja2 PDF template**

```html+jinja
{# src/reporter/templates/report.html.j2 #}
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>{{ title | e }}</title>
<style>
    @page { size: A4 landscape; margin: 15mm; }
    body { font-family: 'Segoe UI', Arial, sans-serif; color: #1a1a1a; line-height: 1.5; font-size: 11px; }
    h1 { color: #1a3c6e; border-bottom: 3px solid #1a3c6e; padding-bottom: 8px; font-size: 22px; }
    h2 { color: #2c5aa0; margin-top: 20px; font-size: 15px; }
    .subtitle { color: #555; font-size: 13px; margin-top: -10px; }
    .summary-box { display: flex; gap: 15px; margin: 15px 0; }
    .summary-card { padding: 10px 18px; border-radius: 6px; color: #fff; font-weight: bold; font-size: 13px; }
    .summary-card.high { background: #dc3545; }
    .summary-card.medium { background: #f0ad4e; color: #333; }
    .summary-card.low { background: #5cb85c; }
    .intro { background: #f4f7fb; padding: 12px 16px; border-left: 4px solid #1a3c6e; margin: 15px 0; }
    .howto { background: #fff8e6; padding: 12px 16px; border-left: 4px solid #f0ad4e; margin: 15px 0; }
    .howto ol { margin: 6px 0; padding-left: 20px; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 10px; }
    th { background: #1a3c6e; color: #fff; padding: 7px 8px; text-align: left; }
    td { padding: 5px 8px; border-bottom: 1px solid #ddd; word-break: break-word; }
    tr.high { background: #fce4e4; }
    tr.medium { background: #fff8e6; }
    tr.low { background: #eaf6ea; }
    .badge { padding: 2px 8px; border-radius: 3px; color: #fff; font-weight: bold; font-size: 9px; }
    .badge.high { background: #dc3545; }
    .badge.medium { background: #f0ad4e; color: #333; }
    .badge.low { background: #5cb85c; }
    .filepath { font-size: 9px; max-width: 280px; overflow: hidden; text-overflow: ellipsis; }
    a { color: #2c5aa0; }
    .footer { margin-top: 20px; font-size: 9px; color: #888; border-top: 1px solid #ddd; padding-top: 8px; }
</style>
</head>
<body>

<h1>{{ title | e }}</h1>
{% if user_label %}<p class="subtitle">User: <strong>{{ user_label | e }}</strong></p>{% endif %}
<p class="subtitle">Generated: {{ generated_at }} | Total items: {{ records | length }}</p>

<div class="summary-box">
    <div class="summary-card high">HIGH: {{ high_count }}</div>
    <div class="summary-card medium">MEDIUM: {{ medium_count }}</div>
    <div class="summary-card low">LOW: {{ low_count }}</div>
</div>

<h2>Risk Model</h2>
<div class="intro">
    <p>Each shared item is assigned a risk level based on who can access it:</p>
    <ul>
        <li><strong style="color:#dc3545;">HIGH</strong> &mdash; Anonymous links, external/guest sharing, or files in sensitive folders (ledelse/l&oslash;n/datarum).</li>
        <li><strong style="color:#d4a017;">MEDIUM</strong> &mdash; Organization-wide links accessible to all employees.</li>
        <li><strong style="color:#5cb85c;">LOW</strong> &mdash; Shared with specific named people inside the organization.</li>
    </ul>
</div>

<h2>How to Remove or Change Sharing</h2>
<div class="howto">
    <ol>
        <li>Click the <strong>Open file</strong> link in the table below.</li>
        <li>Click the <strong>Share</strong> button in the top-right corner.</li>
        <li>Click <strong>Manage Access</strong>.</li>
        <li>Remove links or people as needed:
            <ul>
                <li><strong>Remove a link:</strong> click the &ldquo;X&rdquo; next to it.</li>
                <li><strong>Remove a person:</strong> click their name, then &ldquo;Stop sharing&rdquo;.</li>
                <li><strong>Restrict org-wide:</strong> delete the org link, create a new specific-people link.</li>
            </ul>
        </li>
        <li>Start from <strong style="color:#dc3545;">HIGH</strong> risk items at the top.</li>
    </ol>
</div>

<h2>Shared Items</h2>
<table>
    <thead>
        <tr>
            <th style="width:60px;">Risk</th>
            <th>File Path</th>
            <th style="width:70px;">Link</th>
            <th style="width:110px;">Sharing Type</th>
            <th>Shared With</th>
            <th style="width:80px;">Audience</th>
        </tr>
    </thead>
    <tbody>
    {% for r in records %}
        <tr class="{{ r.risk_level | lower }}">
            <td><span class="badge {{ r.risk_level | lower }}">{{ r.risk_level }}</span></td>
            <td class="filepath">{{ r.item_path | e }}</td>
            <td>{% if r.item_web_url %}<a href="{{ r.item_web_url | e }}">Open file</a>{% else %}-{% endif %}</td>
            <td>{{ r.sharing_type | e }}</td>
            <td>{{ r.shared_with | e }}</td>
            <td>{{ r.shared_with_type | e }}</td>
        </tr>
    {% endfor %}
    </tbody>
</table>

<div class="footer">
    Report generated by Sharing Audit Pipeline. Work through items top to bottom.
</div>

</body>
</html>
```

**Step 4: Implement pdf_export.py**

```python
# src/reporter/pdf_export.py
"""PDF report generation using Jinja2 + Chromium headless."""

import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"
RISK_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def generate_pdf(records: list[dict], output_path: str, title: str = "Sharing Audit Report", user_label: str = "") -> str:
    """Generate a styled PDF report. Falls back to HTML if no PDF converter available. Returns output path."""
    sorted_records = sorted(records, key=lambda r: RISK_ORDER.get(r.get("risk_level", "LOW"), 2))

    high_count = sum(1 for r in records if r.get("risk_level") == "HIGH")
    medium_count = sum(1 for r in records if r.get("risk_level") == "MEDIUM")
    low_count = sum(1 for r in records if r.get("risk_level") == "LOW")

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report.html.j2")

    html = template.render(
        title=title,
        user_label=user_label,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        records=sorted_records,
        high_count=high_count,
        medium_count=medium_count,
        low_count=low_count,
    )

    html_path = f"{output_path}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Try chromium headless
    chromium = _find_chromium()
    if chromium:
        try:
            subprocess.run(
                [chromium, "--headless", "--disable-gpu", "--no-sandbox",
                 f"--print-to-pdf={output_path}", "--no-pdf-header-footer", html_path],
                capture_output=True, timeout=60,
            )
            if os.path.exists(output_path):
                os.remove(html_path)
                return output_path
        except Exception as e:
            logger.warning(f"Chromium PDF failed: {e}")

    # Fallback: return HTML
    fallback = output_path.replace(".pdf", ".html")
    if html_path != fallback:
        os.rename(html_path, fallback)
    logger.warning(f"No PDF converter. Saved as HTML: {fallback}")
    return fallback


def _find_chromium() -> str | None:
    for cmd in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        if shutil.which(cmd):
            return cmd
    return None
```

**Step 5: Implement reporter __main__.py**

```python
# src/reporter/__main__.py
"""Reporter entry point: python -m reporter"""

import logging
import os
import re
from datetime import datetime, timezone

from shared.config import ReporterConfig
from shared.neo4j_client import Neo4jClient
from shared.classify import is_teams_chat_file
from reporter.queries import get_latest_completed_run, get_sharing_data
from reporter.csv_export import generate_csv
from reporter.pdf_export import generate_pdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    config = ReporterConfig()
    os.makedirs(config.output_dir, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")

    logger.info("Connecting to Neo4j...")
    neo4j = Neo4jClient(config.neo4j.uri, config.neo4j.user, config.neo4j.password)

    run_id = get_latest_completed_run(neo4j)
    if not run_id:
        logger.error("No completed scan run found. Run the collector first.")
        neo4j.close()
        return

    logger.info(f"Generating reports for scan run: {run_id}")
    all_records = get_sharing_data(neo4j, run_id)
    logger.info(f"Total sharing records: {len(all_records)}")

    if not all_records:
        logger.info("No shared items found.")
        neo4j.close()
        return

    # Split Teams chat files
    regular = [r for r in all_records if not is_teams_chat_file(r.get("item_path", ""))]
    teams = [r for r in all_records if is_teams_chat_file(r.get("item_path", ""))]

    # Combined reports
    csv_path = os.path.join(config.output_dir, f"SharingAudit_{timestamp}.csv")
    generate_csv(regular, csv_path)
    logger.info(f"Combined CSV: {csv_path} ({len(regular)} records)")

    pdf_path = os.path.join(config.output_dir, f"SharingAudit_{timestamp}.pdf")
    result = generate_pdf(regular, pdf_path)
    logger.info(f"Combined PDF: {result}")

    if teams:
        teams_csv = os.path.join(config.output_dir, f"SharingAudit_{timestamp}_TeamsChatFiles.csv")
        generate_csv(teams, teams_csv)
        logger.info(f"Teams CSV: {teams_csv} ({len(teams)} records)")

        teams_pdf = os.path.join(config.output_dir, f"SharingAudit_{timestamp}_TeamsChatFiles.pdf")
        result = generate_pdf(teams, teams_pdf, title="Teams Chat Files — Sharing Audit")
        logger.info(f"Teams PDF: {result}")

    # Per-owner reports
    owners = {}
    for r in all_records:
        owner = r.get("owner_email") or "(unknown)"
        owners.setdefault(owner, []).append(r)

    for owner, records in owners.items():
        safe_owner = re.sub(r'[\\/:*?"<>|]', '_', owner)
        owner_regular = [r for r in records if not is_teams_chat_file(r.get("item_path", ""))]
        owner_teams = [r for r in records if is_teams_chat_file(r.get("item_path", ""))]
        display = records[0].get("owner_display_name", owner)

        if owner_regular:
            path = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_{timestamp}.csv")
            generate_csv(owner_regular, path)
            pdf_p = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_{timestamp}.pdf")
            generate_pdf(owner_regular, pdf_p, user_label=f"{display} ({owner})")
            logger.info(f"  {owner}: {len(owner_regular)} items + PDF")

        if owner_teams:
            path = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_TeamsChatFiles_{timestamp}.csv")
            generate_csv(owner_teams, path)
            pdf_p = os.path.join(config.output_dir, f"SharingAudit_{safe_owner}_TeamsChatFiles_{timestamp}.pdf")
            generate_pdf(owner_teams, pdf_p, title="Teams Chat Files", user_label=f"{display} ({owner})")
            logger.info(f"  {owner}: {len(owner_teams)} Teams chat files + PDF")

    logger.info("Report generation complete.")
    neo4j.close()


if __name__ == "__main__":
    main()
```

**Step 6: Run CSV tests**

Run: `pytest tests/reporter/test_csv_export.py -v`
Expected: All 2 tests PASS.

**Step 7: Commit**

```bash
git add src/reporter/ tests/reporter/
git commit -m "feat: reporter service — CSV + PDF generation from Neo4j data"
```

---

### Task 8: Docker Images

**Files:**
- Create: `docker/Dockerfile.collector`
- Create: `docker/Dockerfile.reporter`
- Create: `docker-compose.yml`

**Step 1: Create collector Dockerfile**

```dockerfile
# docker/Dockerfile.collector
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

CMD ["python", "-m", "collector"]
```

**Step 2: Create reporter Dockerfile**

```dockerfile
# docker/Dockerfile.reporter
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir .

ENV CHROMIUM_PATH=/usr/bin/chromium

CMD ["python", "-m", "reporter"]
```

**Step 3: Create docker-compose.yml for local dev/testing**

```yaml
# docker-compose.yml
services:
  neo4j:
    image: neo4j:5-community
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD:-changeme}
    ports:
      - "7474:7474"
      - "7687:7687"
    volumes:
      - neo4j-data:/data

  collector:
    build:
      context: .
      dockerfile: docker/Dockerfile.collector
    environment:
      TENANT_ID: ${TENANT_ID}
      CLIENT_ID: ${CLIENT_ID}
      CLIENT_SECRET: ${CLIENT_SECRET}
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: ${NEO4J_PASSWORD:-changeme}
      DELAY_MS: ${DELAY_MS:-100}
    depends_on:
      - neo4j

  reporter:
    build:
      context: .
      dockerfile: docker/Dockerfile.reporter
    environment:
      NEO4J_URI: bolt://neo4j:7687
      NEO4J_USER: neo4j
      NEO4J_PASSWORD: ${NEO4J_PASSWORD:-changeme}
      TENANT_DOMAIN: ${TENANT_DOMAIN:-testaviva.dk}
      SENSITIVE_FOLDERS: ${SENSITIVE_FOLDERS:-ledelse,ledelsen,datarum,løn}
      REPORT_OUTPUT_DIR: /reports
    volumes:
      - ./reports:/reports
    depends_on:
      - neo4j

volumes:
  neo4j-data:
```

**Step 4: Build and verify**

Run: `docker compose build`
Expected: Both images build successfully.

Run: `docker compose up neo4j -d && sleep 10 && docker compose run --rm collector`
Expected: Collector starts, connects to Neo4j, attempts Graph API auth (will fail without real creds in .env, but confirms the wiring works).

**Step 5: Commit**

```bash
git add docker/ docker-compose.yml
git commit -m "feat: Docker images and docker-compose for local dev"
```

---

### Task 9: Helm Chart

**Files:**
- Create: `helm/sharing-audit/Chart.yaml`
- Create: `helm/sharing-audit/values.yaml`
- Create: `helm/sharing-audit/templates/neo4j-statefulset.yaml`
- Create: `helm/sharing-audit/templates/neo4j-service.yaml`
- Create: `helm/sharing-audit/templates/collector-cronjob.yaml`
- Create: `helm/sharing-audit/templates/reporter-cronjob.yaml`
- Create: `helm/sharing-audit/templates/secret.yaml`
- Create: `helm/sharing-audit/templates/pvc-reports.yaml`
- Create: `helm/sharing-audit/templates/configmap.yaml`

**Step 1: Chart.yaml**

```yaml
apiVersion: v2
name: sharing-audit
description: SharePoint & OneDrive sharing audit pipeline
version: 0.1.0
appVersion: "0.1.0"
```

**Step 2: values.yaml**

```yaml
neo4j:
  image: neo4j:5-community
  storage: 10Gi
  storageClass: ""
  resources:
    requests:
      memory: 1Gi
      cpu: 500m
    limits:
      memory: 2Gi

collector:
  image: sharing-audit-collector:latest
  imagePullPolicy: IfNotPresent
  schedule: "0 2 * * 0"
  delayMs: 100
  resources:
    requests:
      memory: 256Mi
      cpu: 100m
    limits:
      memory: 512Mi

reporter:
  image: sharing-audit-reporter:latest
  imagePullPolicy: IfNotPresent
  schedule: "0 6 * * 0"
  sensitiveFolders: "ledelse,ledelsen,datarum,løn"
  tenantDomain: ""
  resources:
    requests:
      memory: 512Mi
      cpu: 200m
    limits:
      memory: 1Gi

reports:
  storage: 5Gi
  storageClass: ""

secrets:
  tenantId: ""
  clientId: ""
  clientSecret: ""
  neo4jPassword: "changeme"
```

**Step 3: Create all template files**

See the design doc for the full list. Each template follows standard Helm patterns with `{{ .Values.x }}` references and `{{ .Release.Name }}` prefixes.

Key templates:
- `neo4j-statefulset.yaml` — StatefulSet with PVC for data persistence
- `neo4j-service.yaml` — ClusterIP service on port 7687
- `collector-cronjob.yaml` — CronJob referencing secret + configmap
- `reporter-cronjob.yaml` — CronJob with reports PVC mount
- `secret.yaml` — Base64-encoded credentials
- `pvc-reports.yaml` — PVC for report output
- `configmap.yaml` — Non-secret config (delayMs, sensitiveFolders, tenantDomain)

**Step 4: Validate Helm chart**

Run: `helm lint helm/sharing-audit/`
Expected: "1 chart(s) linted, 0 chart(s) failed"

Run: `helm template test helm/sharing-audit/ --set secrets.tenantId=test --set secrets.clientId=test --set secrets.clientSecret=test`
Expected: Valid YAML output with all resources rendered.

**Step 5: Commit**

```bash
git add helm/
git commit -m "feat: Helm chart for Kubernetes deployment"
```

---

### Task 10: Integration Test with Docker Compose

**Step 1: Create .env from .env.example with real credentials**

```bash
cp .env.example .env
# Edit .env with real values:
# TENANT_ID=d4ebe44b-...
# CLIENT_ID=6886a8ec-...
# CLIENT_SECRET=qHH8Q~...
# NEO4J_PASSWORD=changeme
# TENANT_DOMAIN=testaviva.dk
```

**Step 2: Start Neo4j**

Run: `docker compose up neo4j -d`
Wait: `docker compose logs neo4j | grep "Started."`

**Step 3: Run collector (single user test)**

Run: `docker compose run --rm -e USERS_TO_AUDIT=mlu@testaviva.dk collector`
Expected: Collector connects, collects mlu's OneDrive + SharePoint data, writes to Neo4j.

**Step 4: Verify data in Neo4j**

Run: `docker compose exec neo4j cypher-shell -u neo4j -p changeme "MATCH (n) RETURN labels(n) AS type, count(n) AS count"`
Expected: Shows counts for User, File, Site, ScanRun nodes.

Run: `docker compose exec neo4j cypher-shell -u neo4j -p changeme "MATCH ()-[r:SHARED_WITH]->() RETURN count(r) AS shares"`
Expected: Shows number of SHARED_WITH relationships > 0.

**Step 5: Run reporter**

Run: `docker compose run --rm reporter`
Expected: Reporter generates CSV + PDF files in `./reports/` directory.

**Step 6: Verify reports**

Run: `ls -la reports/`
Expected: CSV and PDF files for mlu + combined reports.

Run: `head -5 reports/SharingAudit_*.csv`
Expected: Correct columns, sorted by risk level.

**Step 7: Commit**

```bash
git add .env.example
git commit -m "feat: integration test setup with docker-compose"
```

---

## Summary

| Task | What it builds | Key files |
|------|---------------|-----------|
| 1 | Project scaffold | `pyproject.toml`, `src/shared/config.py` |
| 2 | Classification helpers | `src/shared/classify.py` |
| 3 | Neo4j client | `src/shared/neo4j_client.py` |
| 4 | Graph API client | `src/collector/graph_client.py` |
| 5 | Collector service | `src/collector/onedrive.py`, `sharepoint.py`, `__main__.py` |
| 6 | Reporter queries | `src/reporter/queries.py` |
| 7 | Reporter CSV + PDF | `src/reporter/csv_export.py`, `pdf_export.py`, `templates/` |
| 8 | Docker images | `docker/Dockerfile.*`, `docker-compose.yml` |
| 9 | Helm chart | `helm/sharing-audit/` |
| 10 | Integration test | End-to-end with docker-compose |
