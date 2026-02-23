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
            "CREATE CONSTRAINT IF NOT EXISTS FOR (d:DeltaState) REQUIRE d.driveId IS UNIQUE",
        ]
        for c in constraints:
            self.execute(c)

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

    def merge_file(
        self, drive_id: str, item_id: str, path: str, web_url: str, file_type: str
    ):
        """Upsert a File node."""
        self.execute(
            """MERGE (f:File {driveId: $driveId, itemId: $itemId})
               SET f.path = $path, f.webUrl = $url, f.type = $type""",
            {
                "driveId": drive_id,
                "itemId": item_id,
                "path": path,
                "url": web_url,
                "type": file_type,
            },
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
        granted_by: str = "",
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
                   s.lastSeenRunId = $runId,
                   s.grantedBy = $grantedBy""",
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
                "grantedBy": granted_by,
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

    def merge_permission(
        self,
        site_id: str,
        drive_id: str,
        item_id: str,
        item_path: str,
        web_url: str,
        file_type: str,
        user_email: str,
        user_display_name: str,
        user_source: str,
        sharing_type: str,
        shared_with_type: str,
        role: str,
        risk_level: str,
        created_date_time: str,
        run_id: str,
        granted_by: str = "",
    ):
        """Upsert File, User, SHARED_WITH, CONTAINS, and FOUND in a single transaction."""
        self.execute(
            """
            MERGE (f:File {driveId: $driveId, itemId: $itemId})
            SET f.path = $path, f.webUrl = $webUrl, f.type = $fileType
            WITH f
            MERGE (u:User {email: $userEmail})
            SET u.displayName = $userName, u.source = $userSource
            WITH f, u
            MERGE (f)-[s:SHARED_WITH]->(u)
            SET s.sharingType = $sharingType,
                s.sharedWithType = $sharedWithType,
                s.role = $role,
                s.riskLevel = $riskLevel,
                s.createdDateTime = $created,
                s.lastSeenRunId = $runId,
                s.grantedBy = $grantedBy
            WITH f
            MATCH (site:Site {siteId: $siteId})
            MERGE (site)-[:CONTAINS]->(f)
            WITH f
            MATCH (r:ScanRun {runId: $runId})
            MERGE (r)-[:FOUND]->(f)
            """,
            {
                "driveId": drive_id,
                "itemId": item_id,
                "path": item_path,
                "webUrl": web_url,
                "fileType": file_type,
                "userEmail": user_email,
                "userName": user_display_name,
                "userSource": user_source,
                "sharingType": sharing_type,
                "sharedWithType": shared_with_type,
                "role": role,
                "riskLevel": risk_level,
                "created": created_date_time,
                "runId": run_id,
                "grantedBy": granted_by,
                "siteId": site_id,
            },
        )

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
        """Remove sharing relationships and mark a deleted file."""
        self.execute(
            """MATCH (f:File {driveId: $driveId, itemId: $itemId})
               OPTIONAL MATCH (f)-[s:SHARED_WITH]->()
               DELETE s
               SET f.deletedAt = datetime(), f.deletedByRunId = $runId""",
            {"driveId": drive_id, "itemId": item_id, "runId": run_id},
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
