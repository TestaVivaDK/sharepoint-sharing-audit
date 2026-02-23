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
    c.merge_shared_with(
        "d1",
        "i1",
        "ext@gmail.com",
        "Link-SpecificPeople",
        "External",
        "Read",
        "HIGH",
        "2025-01-01",
        run_id,
    )
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
