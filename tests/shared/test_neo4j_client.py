"""Tests for Neo4j client — requires Neo4j (use testcontainers or local instance)."""

import os
import pytest
from shared.neo4j_client import Neo4jClient

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
        result = client.execute(
            "MATCH (u:User {email: 'a@test.dk'}) RETURN u.displayName AS name"
        )
        assert result[0]["name"] == "Alice"

    def test_merge_user_idempotent(self, client):
        client.merge_user("a@test.dk", "Alice", "internal")
        client.merge_user("a@test.dk", "Alice Updated", "internal")
        result = client.execute(
            "MATCH (u:User {email: 'a@test.dk'}) RETURN count(u) AS c"
        )
        assert result[0]["c"] == 1

    def test_merge_site(self, client):
        client.merge_site("site-1", "Marketing", "https://example.com", "SharePoint")
        result = client.execute(
            "MATCH (s:Site {siteId: 'site-1'}) RETURN s.name AS name"
        )
        assert result[0]["name"] == "Marketing"

    def test_merge_file(self, client):
        client.merge_file(
            "drive-1", "item-1", "/doc.xlsx", "https://example.com/doc.xlsx", "File"
        )
        result = client.execute(
            "MATCH (f:File {driveId: 'drive-1', itemId: 'item-1'}) RETURN f.path AS path"
        )
        assert result[0]["path"] == "/doc.xlsx"


class TestRelationships:
    def test_merge_shared_with(self, client):
        client.merge_file("d1", "i1", "/doc.xlsx", "https://x.com/doc", "File")
        client.merge_user("ext@gmail.com", "External", "external")
        client.merge_shared_with(
            drive_id="d1",
            item_id="i1",
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
