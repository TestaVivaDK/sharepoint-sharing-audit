# Sharing Audit Pipeline — Design

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:writing-plans to create the implementation plan from this design.

**Goal:** Replace the one-shot PowerShell audit script with a persistent data pipeline that collects all SharePoint/OneDrive sharing metadata into a Neo4j graph database, runs weekly via cron, and generates per-user CSV + PDF reports from the stored data.

**Architecture:** Two Python services (collector + reporter) with a Neo4j database, deployed as a Helm chart on Kubernetes. The collector runs weekly to upsert sharing data into the graph. The reporter runs after collection to generate reports. Either service can run independently.

**Tech Stack:** Python 3.12, Neo4j 5 Community, Microsoft Graph SDK, Jinja2, Chromium headless, Docker, Helm.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Helm Chart                         │
│                                                      │
│  ┌──────────────┐   ┌──────────┐   ┌─────────────┐ │
│  │  Collector    │──▶│  Neo4j   │◀──│  Reporter   │ │
│  │  (CronJob)   │   │  (DB)    │   │  (CronJob)  │ │
│  └──────┬───────┘   └──────────┘   └──────┬──────┘ │
│         │                                  │        │
│         ▼                                  ▼        │
│  Microsoft Graph API              CSV + PDF files   │
│                                   (PVC or S3)       │
└─────────────────────────────────────────────────────┘
```

**Three containers:**

1. **Collector** (Kubernetes CronJob, weekly) — Python service that calls Microsoft Graph API, upserts nodes and relationships into Neo4j.
2. **Neo4j** (StatefulSet with PVC) — Persistent graph database storing all sharing data.
3. **Reporter** (Kubernetes CronJob, runs after collector or on-demand) — Python service that queries Neo4j, generates per-user CSV + PDF reports.

**Separation of concerns:**

- Collector only writes to Neo4j — no report logic.
- Reporter only reads from Neo4j — no Graph API calls.
- Either can be run independently (e.g. re-generate reports without re-collecting).

---

## Neo4j Data Model

### Nodes

```
(:User {email, displayName, source})
```
A person — tenant member, external user, or guest. Also used for audience placeholders (anonymous, organization).

```
(:File {path, webUrl, type, driveId, itemId})
```
A file or folder in OneDrive or SharePoint.

```
(:Site {siteId, name, webUrl, source})
```
A SharePoint site or OneDrive root. `source` is "OneDrive" or "SharePoint".

```
(:ScanRun {runId, timestamp, status})
```
Tracks each collection run for history and change detection.

### Relationships

```
(:User)-[:OWNS]->(:Site)
```
User owns their OneDrive, or is a site admin.

```
(:Site)-[:CONTAINS]->(:File)
```
File lives on this site.

```
(:File)-[:SHARED_WITH {
    sharingType,       // Link-Anyone, Link-Organization, Link-SpecificPeople, User, Group
    sharedWithType,    // Internal, External, Guest, Anonymous
    role,              // Read, Write, Owner
    riskLevel,         // HIGH, MEDIUM, LOW
    createdDateTime,
    lastSeenRunId      // Links to ScanRun for change tracking
}]->(:User)
```
The core relationship. Every sharing permission is a `SHARED_WITH` edge.

```
(:ScanRun)-[:FOUND]->(:File)
```
Which files were seen in this scan (enables detecting removed shares).

### Special audience nodes

```
(:User {email: "anonymous", displayName: "Anyone with the link"})
(:User {email: "organization", displayName: "All organization members"})
```

This lets every sharing permission be a `SHARED_WITH` edge, including anonymous and org-wide links.

### Example queries

- **Everything shared externally:**
  `MATCH (f)-[s:SHARED_WITH]->(u:User) WHERE s.sharedWithType IN ['External','Guest','Anonymous'] RETURN f, s, u`
- **What changed since last week?**
  Compare `lastSeenRunId` across runs.
- **What does user X have access to?**
  `MATCH (u:User {email:'...'})<-[:SHARED_WITH]-(f) RETURN f`
- **Sensitive folders shared with anyone?**
  `MATCH (f:File)-[s:SHARED_WITH]->(u) WHERE f.path =~ '(?i).*(ledelse|løn|datarum).*' RETURN f, s, u`

---

## Collector Service

**Runtime:** Python 3.12, Kubernetes CronJob (weekly, e.g. `0 2 * * 0`).

**Dependencies:** `msgraph-sdk`, `neo4j` (Python driver).

**Auth:** App-only authentication (TenantId, ClientId, ClientSecret from Kubernetes Secret).

### Collection flow

1. Create `ScanRun` node with timestamp.
2. Enumerate users (`GET /users`, filter licensed + enabled).
3. For each user:
   - Get their OneDrive drive (`GET /users/{id}/drive`).
   - Recursively walk all items.
   - For each item with permissions:
     - `MERGE` the `File` node (upsert by driveId + itemId).
     - `MERGE` the `User`/audience node for each sharedWith.
     - `MERGE` the `SHARED_WITH` relationship (update properties).
     - Set `lastSeenRunId` on the relationship.
4. Enumerate SharePoint sites (`GET /sites/getAllSites` with pagination).
5. Filter out personal OneDrive sites (`-my.sharepoint.com`).
6. For each site:
   - Get all drives/document libraries.
   - Same recursive walk and permission collection as OneDrive.
7. Mark `ScanRun` as complete.

### Incremental behavior

- `MERGE` (upsert) means re-running doesn't duplicate data.
- `lastSeenRunId` on relationships lets us detect removed shares.
- New files/shares get created, existing ones get updated.

### Resilience

- Retry with exponential backoff on transient failures (429, 503).
- Configurable delay between API calls (default 100ms).
- Progress logged per user/site.
- If collector crashes mid-run, the `ScanRun` stays incomplete — reporter skips it.

---

## Reporter Service

**Runtime:** Python 3.12, Kubernetes CronJob (e.g. `0 6 * * 0`, or triggered manually).

**Dependencies:** `neo4j` (Python driver), `jinja2`, Chromium headless (bundled in Docker image).

### Report generation flow

1. Query Neo4j for latest completed `ScanRun`.
2. Get all `SHARED_WITH` relationships from that run.
3. Apply risk classification:
   - **HIGH:** Anonymous, External, Guest, or sensitive folders (ledelse/løn/datarum).
   - **MEDIUM:** Organization-wide links.
   - **LOW:** Specific internal people.
4. Group by owner (User who `OWNS` the Site).
5. For each owner:
   - Split into regular items vs Teams chat files.
   - Sort by risk level (HIGH first).
   - Generate per-user CSV (slim columns).
   - Generate per-user PDF (risk model intro + how-to + color-coded table).
6. Generate combined CSV + PDF (all users).
7. Generate combined Teams chat files CSV + PDF.
8. Write reports to output volume (PVC).

### CSV columns

`RiskLevel, Source, ItemPath, ItemWebUrl, SharingType, SharedWith, SharedWithType, Role, CreatedDateTime`

### PDF content

- Summary cards (HIGH / MEDIUM / LOW counts).
- Risk model explanation.
- Step-by-step how-to remove sharing.
- Color-coded table sorted by risk (red / yellow / green rows).

### External/guest user classification

- Only email addresses are checked against tenant domain (not display names).
- `#EXT#` in email = Guest.
- Email not matching `@{tenantDomain}` = External.
- No email (display name only) = assumed Internal.

---

## Helm Chart & Deployment

### Chart structure

```
helm/sharing-audit/
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
```

### Key values

```yaml
neo4j:
  image: neo4j:5-community
  storage: 10Gi
  auth:
    password: (from secret)

collector:
  schedule: "0 2 * * 0"
  image: sharing-audit-collector:latest
  delayMs: 100
  graphApi:
    tenantId: (from secret)
    clientId: (from secret)
    clientSecret: (from secret)

reporter:
  schedule: "0 6 * * 0"
  image: sharing-audit-reporter:latest
  sensitiveFolders: "ledelse,ledelsen,datarum,løn"

reports:
  storage: 5Gi
```

### Docker images

- `sharing-audit-collector` — Python + msgraph-sdk + neo4j driver.
- `sharing-audit-reporter` — Python + neo4j driver + jinja2 + chromium headless.

### Secrets

TenantId, ClientId, ClientSecret, Neo4j password stored in a Kubernetes Secret referenced by both CronJobs.
