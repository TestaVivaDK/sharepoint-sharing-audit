# SharePoint & OneDrive Sharing Audit

## Why This Exists

Organizations adopting AI copilots and agents that operate across SharePoint and OneDrive face a serious risk: **oversharing**. Most tenants have years of accumulated sharing permissions — anonymous links, org-wide links, external shares — that nobody remembers creating. When an AI agent indexes your document libraries, every one of those permissions becomes a potential path to knowledge that shouldn't be exposed.

The problem is scale. A single user might have hundreds of shared files. Multiply that across a tenant and you get tens of thousands of sharing permissions that no admin can realistically review by hand.

This tool automates that cleanup:

1. **Scan** — the collector walks every OneDrive and SharePoint site via the Graph API and maps all sharing permissions into a Neo4j graph
2. **Report** — the reporter generates PDF/CSV reports with risk scoring so admins can see the full picture
3. **Fix** — the self-service webapp lets each user log in, see the files *they* shared, and bulk-unshare with one click

The goal is to get your tenant to a clean sharing baseline before you turn on AI-powered search, copilots, or agents — and to keep it clean with regular scans.

## Architecture

```
┌─────────────┐      Microsoft        ┌────────────┐      ┌──────────┐
│  Collector  │──────Graph API───────▶│  Neo4j     │◀────│ Reporter │
│  (Python)   │    app-only auth      │  (graph    │      │ (Python) │
│             │    OneDrive + SP      │   DB)      │      │          │
└─────────────┘   permissions         └────────────┘      └─────┬────┘
                                        ▲       ▲               │
                                        │       │            ┌──┴──────────┐
                                        │  ┌────┴────────┐   │  PDF + CSV  │
                                        │  │  Webapp     │   │   reports   │
                                        │  │  FastAPI +  │   └─────────────┘
                                        │  │  React      │
                                        │  └─────────────┘
                                        │   delegated   ▲
                                        │   auth (MSAL) │
                                        │               │               
                                   ┌────┴───────────────────┐
                                   │  Apache2 reverse-proxy │
                                   │        Port 443        │
                                   │                        │
                                   └────────────────────────┘
```

- **Collector** — Walks OneDrive and SharePoint drives via Microsoft Graph API, collects all explicit (non-inherited) sharing permissions, and stores them as a graph in Neo4j. Tracks who granted each permission via `grantedBy`.
- **Reporter** — Queries Neo4j, deduplicates files, computes risk scores (0–100), and generates a combined PDF + CSV report for admins.
- **Webapp** — React SPA with FastAPI backend. Users log in with their Microsoft Entra account, see only the files *they* shared (via `grantedBy`), and can bulk-unshare via the Graph API using delegated permissions.
- **Neo4j** — Stores users, files, sites, and sharing relationships as a graph. Supports incremental collection with scan runs.
- **Apache2 reverse-proxy**: The Apache2 container acts as a secure reverse proxy (SSL/TLS termination) and routes traffic to webapp and neo4j admin interfaces.

## Prerequisites

- Python 3.11+
- Node.js 18+ (for the frontend)
- Docker (for Neo4j)
- An Azure AD app registration with Microsoft Graph API permissions

### Azure AD App Registration

1. Go to [Azure Portal](https://portal.azure.com) > **Microsoft Entra ID** > **App registrations** > **New registration**
2. Name it (e.g. "Sharing Audit"), single-tenant
3. Under **Authentication** > **Platform configurations**, add a **Single-page application** redirect URI:
   - Development: `http://localhost:5173`
   - Production: your webapp URL
4. Under **Certificates & secrets**, create a client secret
5. Under **API permissions**, add these permissions for Microsoft Graph:

**Application permissions** (for the collector and reporter — admin-consented):

| Permission | Purpose |
|------------|---------|
| `User.Read.All` | Enumerate all users |
| `Sites.Read.All` | Read all SharePoint sites and document libraries |
| `Files.Read.All` | Read all OneDrive files and sharing permissions |
| `Sites.FullControl.All`| Allows the app to have full control of all site collections without a signed in user. **Required to properly process permissions changes in delta scans.** If this permission is not granted, rely only on full scans (`FORCE_FULL_SCAN=true`) and purge neo4j database before each full scan |

**Delegated permissions** (for the webapp — user-consented):

| Permission | Purpose |
|------------|---------|
| `User.Read` | Read the signed-in user's profile |
| `Files.ReadWrite.All` | Remove sharing permissions on the user's files |

6. Click **Grant admin consent** for the application permissions

## Quick Start

### 1. Start Neo4j

```bash
docker compose up -d neo4j
```

### 2. Configure environment

```bash
cp .env.example .env
# Edit .env with your Azure AD app credentials and Neo4j password
```

### 3. Install dependencies

```bash
pip install -e .
```

### 4. Run the collector

```bash
PYTHONPATH=src python -m collector
```

### 5. Run the reporter

```bash
PYTHONPATH=src python -m reporter
```

### 6. Run the webapp

```bash
# Build the frontend
cd frontend && npm install && npm run build && cd ..

# Start the server
PYTHONPATH=src uvicorn webapp.app:create_app --factory --host 0.0.0.0 --port 8000
```

For development with hot reload:

```bash
# Terminal 1: Frontend dev server
cd frontend && npm run dev

# Terminal 2: Backend
PYTHONPATH=src uvicorn webapp.app:create_app --factory --reload --port 8000
```

The frontend dev server (Vite) proxies `/api` requests to the backend on port 8000.

## Environment Variables

### Collector

| Variable | Default | Description |
|----------|---------|-------------|
| `TENANT_ID` | required | Azure AD tenant ID |
| `CLIENT_ID` | required | App registration client ID |
| `CLIENT_SECRET` | required | Client secret |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | required | Neo4j password |
| `DELAY_MS` | `100` | Milliseconds between API calls |
| `USERS_TO_AUDIT` | all users | Comma-separated UPNs to audit (e.g. `user@domain.com`) |
| `SKIP_ONEDRIVE` | `false` | Set to `true` to skip OneDrive drives |
| `SKIP_SHAREPOINT` | `false` | Set to `true` to skip SharePoint sites |
| `IGNORE_SHAREPOINT_GROUPS` | `false` | Set to `true` to skip SharePoint groups and to collect only Microsoft Entra groups |
| `FORCE_FULL_SCAN`| `false` | Set to `true` to force full scan and ignore delta scans |
| `FULL_SCAN_INTERVAL_DAYS` | `7` | Number of days between forced full scans |
| `LOG_LEVEL` | `INFO` | Collector log level |

### Reporter

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | required | Neo4j password |
| `TENANT_DOMAIN` | — | Your tenant domain (e.g. `contoso.com`) for internal/external classification |
| `REPORT_OUTPUT_DIR` | `./reports` | Directory for generated reports |
| `CUSTOM_NEO4J_WHERE_FILTER` | `Optional variable` | Custom NEO4J WHERE clause to select only a subset of data to be included in the reports.  |

### Webapp

| Variable | Default | Description |
|----------|---------|-------------|
| `TENANT_ID` | required | Azure AD tenant ID |
| `CLIENT_ID` | required | App registration client ID (same as collector) |
| `CLIENT_SECRET` | required | Client secret (for token validation) |
| `NEO4J_URI` | `bolt://localhost:7687` | Neo4j connection URI |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | required | Neo4j password |
| `TENANT_DOMAIN` | — | Your tenant domain |

The frontend also needs `VITE_CLIENT_ID` and `VITE_TENANT_ID` at build time (set in `frontend/.env` or passed as build args in Docker).

## Docker Compose

Run the full pipeline with Docker:

```bash
docker compose up neo4j -d         # Start Neo4j
docker compose run collector       # Run collection
docker compose run reporter        # Generate reports
docker compose up webapp -d        # Start the webapp on port 8000
docker compose up -d apache2          # Start the apache2 reverse proxy listening on port 80 and 443
```

Reports are saved to the `./reports/` directory.

### Apache2 Reverse Proxy

The Apache2 container acts as a secure reverse proxy (SSL/TLS termination) and routes traffic to multiple backend services. It enables:

- **HTTPS encryption** for the webapp and Neo4j services
- **Name-based virtual hosting** with separate FQDNs for each service
- **WebSocket proxying** for Neo4j Bolt protocol connections
- **IP-based access restrictions** for sensitive Neo4j interfaces

#### Architecture

The Apache2 container exposes three virtual hosts on port 443 (HTTPS):

| Virtual Host | Backend | Purpose | Env Var |
|--------------|---------|---------|---------|
| `WEBAPP_FQDN` | `webapp:8000` | Main sharing audit web application | `WEBAPP_FQDN` |
| `NEO4J_BROWSER_FQDN` | `neo4j:7474/browser` | Neo4j Browser UI (admin interface) | `NEO4J_BROWSER_FQDN` |
| `NEO4J_BOLT_FQDN` | `neo4j:7687` | Neo4j Bolt protocol (WebSocket) | `NEO4J_BOLT_FQDN` |

#### Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `WEBAPP_FQDN` | Yes | Fully qualified domain name for the webapp | `audit.contoso.com` |
| `NEO4J_BROWSER_FQDN` | Yes | FQDN for Neo4j Browser UI | `neo4j-browser.contoso.com` |
| `NEO4J_BOLT_FQDN` | Yes | FQDN for Neo4j Bolt protocol (used by Neo4j Browser) | `neo4j-bolt.contoso.com` |
| `NEO4J_ADMIN_RESTRICTED_IP` | Yes | IP address or CIDR range allowed to access Neo4j admin interfaces | `203.0.113.42` or `203.0.113.0/24` |

#### SSL Certificates

The container requires self-signed or CA-signed SSL certificates:

```bash
# Generate self-signed certificates (for testing only)
openssl req -x509 -newkey rsa:2048 -keyout apache2/server.key -out apache2/server.crt -days 365 -nodes

# For production, obtain certificates from a trusted CA
# and place them in apache2/server.crt and apache2/server.key
```

Certificates are mounted into the container at:
- `/usr/local/apache2/conf/server.crt` — certificate file
- `/usr/local/apache2/conf/server.key` — private key

#### Entrypoint Behavior

The container's entrypoint script (`apache2/entrypoint.sh`) performs **environment variable substitution** at startup:

1. Replaces `__WEBAPP_FQDN__` with the value of `WEBAPP_FQDN`
2. Replaces `__NEO4J_BROWSER_FQDN__` with the value of `NEO4J_BROWSER_FQDN`
3. Replaces `__NEO4J_BOLT_FQDN__` with the value of `NEO4J_BOLT_FQDN`
4. Replaces `__NEO4J_ADMIN_RESTRICTED_IP__` with the value of `NEO4J_ADMIN_RESTRICTED_IP`
5. Starts Apache2 in foreground mode

This allows the configuration to be dynamically adapted at runtime without rebuilding the container.

#### Security

- **IP Restriction** — Access to Neo4j Browser and Bolt proxy is restricted to the IP(s) specified in `NEO4J_ADMIN_RESTRICTED_IP`
- **TLS/SSL** — All traffic uses TLS 1.2+ (SSLv3 and TLS 1.0/1.1 are disabled)
- **Cipher Suites** — HIGH and MEDIUM strength ciphers only; weak ciphers (MD5, RC4, 3DES) are disabled
- **Proxy Security** — `ProxyRequests Off` prevents open proxy exploitation

#### Usage Example

```bash
# Set environment variables in .env
export WEBAPP_FQDN=audit.contoso.com
export NEO4J_BROWSER_FQDN=neo4j-browser.contoso.com
export NEO4J_BOLT_FQDN=neo4j-bolt.contoso.com
export NEO4J_ADMIN_RESTRICTED_IP=203.0.113.42

# Generate self-signed certificates
openssl req -x509 -newkey rsa:2048 -keyout apache2/server.key -out apache2/server.crt -days 365 -nodes

# Start the stack
docker compose up neo4j webapp apache2
```

Once running, access:
- **Webapp** — `https://audit.contoso.com`
- **Neo4j Browser** — `https://neo4j-browser.contoso.com/browser` (restricted to `203.0.113.42`)
- **Neo4j Bolt** — `wss://neo4j-bolt.contoso.com/` (for Cypher queries via Neo4j Browser)


## Output

The reporter generates one combined report containing all shared items across all users and SharePoint sites:

- **PDF** — `SharingAudit_<timestamp>.pdf` — styled report with risk scores, sorted highest risk first
- **CSV** — `SharingAudit_<timestamp>.csv` — same data in spreadsheet format

### Risk Score (0–100)

Each shared item receives a numerical risk score calculated from six weighted factors:

| Factor | Max Points | Description |
|--------|-----------|-------------|
| **Audience scope** | 30 | Anonymous link (30), External/guest (25), Org-wide (15), Internal (5) |
| **Recipient count** | 15 | 20+ people (15), 6–19 (10), 2–5 (5), 1 person (2) |
| **Sensitive content** | 20 | File/folder name contains sensitive keywords (løn, personale, kontrakt, CPR, fortrolig, budget, GDPR, etc.) |
| **File type** | 15 | Spreadsheets/documents/PDF (15), other files (8), images/media (3) |
| **Permission level** | 10 | Edit/write access (10), read-only (3) |
| **Asset type** | 10 | Shared folder (10), single file (3) |

Score ranges: **70–100** Critical | **50–69** High | **25–49** Medium | **0–24** Low

### Risk Level

In addition to the numerical score, each item has a categorical risk level:

| Level | Criteria |
|-------|----------|
| **HIGH** | Anonymous links, external/guest sharing, or files in sensitive folders |
| **MEDIUM** | Organization-wide links accessible to all employees |
| **LOW** | Shared with specific named internal people |

### Deduplication

Files are deduplicated in the report — each file appears once with all sharing details consolidated. If a file is shared with 5 different people, it shows as one row with all recipients listed. The risk score and level reflect the worst-case sharing for that file.

### Source Column

Each item shows its source: **OneDrive**, **SharePoint**, or **Teams** (Teams chat files stored in OneDrive are automatically tagged).

## Webapp

The web app provides a self-service dashboard where each user sees only the files they personally shared and can revoke those permissions.

### Features

- **Microsoft Entra login** — MSAL.js PKCE flow, single-tenant
- **File list with risk scoring** — MUI DataGrid Pro with sorting, filtering, and search
- **Filter by risk level and source** — quick-filter chips for HIGH/MEDIUM/LOW and OneDrive/SharePoint/Teams
- **Bulk unshare** — select files and remove all direct sharing permissions via delegated Graph API calls
- **Risk score ranking** — files sorted by numerical risk score (highest first)

### How It Works

1. User logs in with their Microsoft Entra account (MSAL.js PKCE)
2. The backend validates the ID token and creates an httpOnly cookie session
3. The backend queries Neo4j for `SHARED_WITH` relationships where `grantedBy` matches the user's email — this ensures users only see files they personally shared, not site-level group permissions
4. To unshare, the frontend acquires a delegated Graph API token (`Files.ReadWrite.All`) and sends it to the backend, which removes all non-inherited permissions from the selected files

### Tech Stack

- **Backend:** FastAPI, python-jose (JWT validation), httpx
- **Frontend:** React 19, TypeScript, MUI DataGrid Pro, TanStack Query, MSAL React
- **Auth:** Microsoft Entra ID tokens validated against JWKS, in-memory session store

## Sensitive Keywords

The pipeline flags files and folders containing these Danish keywords as sensitive (contributing +20 to the risk score and triggering HIGH risk level):

løn, ledelse, direktion, bestyrelse, datarum, personale, ansættelse, opsigelse, fratrædelse, regnskab, budget, økonomi, faktura, kontrakt, fortrolig, hemmelig, persondata, CPR, personfølsom, sundhed, syge, GDPR, pension, ferie, revision, inkasso, gæld, erstatning, disciplinær, advarsel, klage

These are matched case-insensitively against the full file path (both folder names and file names).

## Data Model (Neo4j)

### Data Model

```
(:User)-[:OWNS]->(:Site)-[:CONTAINS]->(:File)
(:File)-[:SHARED_WITH {riskLevel, sharingType, role, grantedBy, ...}]->(:User)
(:File)-[:SHARED_WITH {riskLevel, sharingType, role, grantedBy, ...}]->(:Group)->[:CONTAINS*0..]->(:User)
(:ScanRun)-[:FOUND]->(:File)
```

- **User** — id, email, displayName, source
- **Group** — id, displayName, source
- **Site** — OneDrive or SharePoint site (siteId, name, webUrl, source)
- **File** — driveId, itemId, path, webUrl, type (File/Folder)
- **SHARED_WITH** — sharing relationship: sharingType, sharedWithType, role, riskLevel, createdDateTime, grantedBy, lastSeenRunId
- **ScanRun** — collection run with runId, timestamp, and status

The `grantedBy` field on `SHARED_WITH` stores the email of the user who created the sharing permission (extracted from Graph API's `grantedByV2`). This is used by the webapp to show each user only the files they personally shared.

### Advanced Neo4j queries

Find all files shared with external users :

```cypher
MATCH path = (f:File)-[:SHARED_WITH]->()-[:CONTAINS*0..]->(u:User {source:"External"})
RETURN path
```

Find all files shared with external users and exclude a specific domain:

```cypher
MATCH path = (f:File)-[:SHARED_WITH]->()-[:CONTAINS*0..]->(u:User
 {source:"External"})
WITH path, f, collect(u) AS users
WHERE NONE(x IN users WHERE x.email CONTAINS "@specific-domain-excluded.com")
RETURN path
```

Find all files shared with more than 20 users:

```cypher
MATCH path = (f:File)-[r:SHARED_WITH]->()-[:CONTAINS*0..]->(u:User)
WITH f, count(r) AS n, collect(u.email) AS users, collect(path) as paths
WHERE n > 20
RETURN f.path, users, n, paths
ORDER BY n DESC
```

## Helm Chart (Kubernetes)

A Helm chart is included at `helm/sharing-audit/`.

```bash
helm install sharing-audit helm/sharing-audit/ \
  --set secrets.tenantId=YOUR_TENANT_ID \
  --set secrets.clientId=YOUR_CLIENT_ID \
  --set secrets.clientSecret=YOUR_SECRET \
  --set secrets.neo4jPassword=YOUR_NEO4J_PASSWORD
```

### Using External Secrets (SealedSecrets, ExternalSecrets, etc.)

If you manage secrets externally, create a Secret with these keys and reference it:

| Key | Description |
|-----|-------------|
| `tenant-id` | Azure AD tenant ID |
| `client-id` | App registration client ID |
| `client-secret` | Client secret |
| `neo4j-password` | Neo4j password |
| `neo4j-auth` | Neo4j auth string, format: `neo4j/<password>` |

```yaml
secrets:
  existingSecret: "my-sealed-secret"
```

When `existingSecret` is set, the chart skips creating its own Secret and all pods reference the provided one.

## Security Notes

- The **collector and reporter are read-only** — they never modify any files or permissions
- The **webapp can remove sharing permissions** — it uses delegated auth (`Files.ReadWrite.All`) with the logged-in user's token, so it can only modify files the user has access to
- Store credentials in `.env` (excluded from git via `.gitignore`)
- Never commit `.env` files — only `.env.example` (with placeholders) is tracked
- Use `secrets.existingSecret` in Kubernetes to avoid storing secrets in Helm values
- Use a short-lived client secret and rotate regularly
- Sessions are stored in-memory (not persisted across restarts) with httpOnly cookies
- ID tokens are validated against Microsoft's JWKS endpoint with 24-hour cache TTL
