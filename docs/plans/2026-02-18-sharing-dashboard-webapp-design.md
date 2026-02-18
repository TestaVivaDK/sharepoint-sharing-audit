# Sharing Dashboard Web App — Design

## Goal

Add a self-service web app where any tenant user can log in with Microsoft Entra, view their shared files (from pre-collected Neo4j data), and bulk-remove sharing via the Microsoft Graph API.

## Architecture

Monorepo: FastAPI backend (`src/webapp/`) serves the React SPA (`frontend/`) as static files. Single Docker container in production.

```
Browser (MSAL.js)          FastAPI                    External
  │                           │                           │
  ├──Entra login──────────────────────────────────────────►│ Microsoft Entra
  │◄──ID token + access token─────────────────────────────│
  │                           │                           │
  ├──POST /api/auth/login─────►│ validate ID token         │
  │◄──session cookie───────────│                           │
  │                           │                           │
  ├──GET /api/files────────────►│ query Neo4j (app-only)───►│ Neo4j
  │◄──shared files─────────────│◄──────────────────────────│
  │                           │                           │
  ├──POST /api/unshare─────────►│ DELETE permissions────────►│ Graph API
  │  (+ Graph token)          │◄──────────────────────────│
  │◄──result───────────────────│                           │
```

## Authentication

- **MSAL.js** (PKCE flow, SPA platform) authenticates the user in the browser
- Scopes: `User.Read`, `Files.ReadWrite.All`
- **ID token** sent to FastAPI `POST /api/auth/login`, validated (audience, issuer, signature via JWKS)
- FastAPI creates an **httpOnly session cookie** mapping to the user's email
- **Neo4j queries** use app-only credentials (existing `CLIENT_ID`/`CLIENT_SECRET`)
- **Unshare actions** use the user's delegated Graph token (acquired by MSAL.js, sent in request body)
- Azure AD app registration updated: add delegated `Files.ReadWrite.All`, add SPA redirect URI

## API Endpoints

```
GET  /api/auth/me              → current user info from session
POST /api/auth/login           → validate ID token, create session
POST /api/auth/logout          → clear session

GET  /api/files                → user's shared files from Neo4j
     ?risk_level=HIGH,MEDIUM
     ?source=OneDrive,SharePoint,Teams
     ?search=budget

POST /api/unshare              → bulk remove all sharing
     body: { file_ids: ["driveId:itemId", ...], graph_token: "..." }
     → DELETE each permission via Graph API
     → returns { succeeded: [...], failed: [...] }

GET  /api/stats                → summary counts for dashboard header
     → { total, high, medium, low, last_scan }
```

- `/api/files` filters Neo4j by authenticated user's email (via OWNS → Site → File)
- `/api/unshare` fetches current permissions from Graph API, DELETEs each non-inherited one
- All endpoints except `/api/auth/login` require a valid session

## Frontend

Single-page React app with:

- **Header**: app title, user avatar, logout
- **Summary cards**: HIGH / MEDIUM / LOW counts, last scan timestamp
- **Toolbar**: search, source filter, risk filter, "Remove Sharing" button (with selected count)
- **MUI DataGrid Pro**: checkbox selection, sorted by risk score descending

DataGrid columns: Score, Risk (badge), Source, Type, File/Folder Path, Open link, Sharing Type, Shared With, Audience

### Unshare Flow

1. User checks files in DataGrid
2. Clicks "Remove Sharing" (shows count)
3. Confirmation dialog: "Remove all sharing from N files? This cannot be undone."
4. Frontend acquires Graph token via `acquireTokenSilent`, calls `POST /api/unshare`
5. Progress indicator
6. Result toast (succeeded/failed counts)
7. TanStack Query invalidates file list

### Tech Stack

- React 18 + TypeScript + Vite
- MUI DataGrid Pro (licensed)
- MUI components + theme
- TanStack Query (data fetching, caching)
- @azure/msal-react + @azure/msal-browser

## Project Structure

```
src/webapp/              FastAPI backend
  __main__.py            uvicorn entry point
  app.py                 FastAPI app, CORS, static mount
  auth.py                token validation, session management
  routes_auth.py         /api/auth/* endpoints
  routes_files.py        /api/files, /api/stats
  routes_unshare.py      /api/unshare
  graph_api.py           delegated Graph API calls

frontend/                React app
  package.json
  tsconfig.json
  vite.config.ts
  src/
    main.tsx
    App.tsx
    auth/                MSAL config & provider
    api/                 TanStack Query hooks
    components/          DataGrid, summary cards, toolbar
```

## Deployment

Multi-stage Dockerfile:
1. `node:20-alpine` — build React app
2. `python:3.11-slim` — install FastAPI, copy built frontend to `/app/static`, run uvicorn

Docker Compose: new `webapp` service on port 8000, depends on `neo4j`.

## Dependencies

Backend: `fastapi`, `uvicorn`, `python-jose[cryptography]`, `httpx`
Frontend: `@azure/msal-browser`, `@azure/msal-react`, `@mui/x-data-grid-pro`, `@mui/material`, `@tanstack/react-query`, `vite`

## Azure AD Changes

- Add delegated permission: `Files.ReadWrite.All`
- Add SPA platform redirect URI: `http://localhost:8000` (+ production URL)
- Grant admin consent for the new permission
