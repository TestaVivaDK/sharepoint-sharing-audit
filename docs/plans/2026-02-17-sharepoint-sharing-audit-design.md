# SharePoint & OneDrive Sharing Audit Script — Design

## Purpose

A PowerShell script that iterates over all users and SharePoint sites in a Microsoft 365 tenant to enumerate every sharing permission on every file and folder. The output is a CSV report enabling administrators to review and clean up access.

## Approach

Microsoft Graph PowerShell SDK (`Microsoft.Graph`) with interactive delegated authentication. Single script, no module structure.

## Architecture & Flow

```
1. Prerequisites check (modules installed?)
2. Interactive auth via Connect-MgGraph
3. Enumerate all licensed, enabled users
4. For each user:
   a. Get their OneDrive drive
   b. Recursively walk all items
   c. For each item, get permissions
   d. Record shared items to results collection
5. Enumerate all SharePoint sites
6. For each site:
   a. Get all drives (document libraries)
   b. For each drive, recursively walk all items
   c. For each item, get permissions
   d. Record shared items to results collection
7. Export results to CSV
8. Print summary stats to console
```

## Authentication

Interactive sign-in via `Connect-MgGraph -Scopes`. Requires Global Admin or SharePoint Admin role.

### Required Scopes (Delegated, Read-Only)

- `User.Read.All` — enumerate all users
- `Sites.Read.All` — read all SharePoint sites and content
- `Files.Read.All` — read all OneDrive files and sharing permissions

## Graph API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /users` | List all licensed, enabled users |
| `GET /users/{id}/drive/root/children` | Recurse through OneDrive items |
| `GET /drives/{id}/items/{id}/permissions` | Get sharing permissions on an item |
| `GET /sites?search=*` | List all SharePoint sites |
| `GET /sites/{id}/drives` | List document libraries in a site |
| `GET /drives/{id}/root/children` | Recurse through library items |

## Data Model (CSV Output)

Each row = one sharing permission on one item.

| Column | Description |
|--------|-------------|
| `Source` | `OneDrive` or `SharePoint` |
| `SiteName` | Site title or user's display name |
| `SiteUrl` | URL of the site or OneDrive |
| `ItemPath` | Path to the file/folder within the drive |
| `ItemType` | `File` or `Folder` |
| `SharingType` | `User`, `Group`, `Link-Anyone`, `Link-Organization`, `Link-SpecificPeople` |
| `SharedWith` | Email, group name, or link description |
| `SharedWithType` | `Internal`, `External`, `Guest`, `Anonymous` |
| `Role` | `Read`, `Write`, `Owner` |
| `CreatedDateTime` | When the permission was created |
| `OwnerEmail` | The owner of the drive/site |
| `OwnerDisplayName` | Display name of the owner |

Output file: `SharingAudit_YYYY-MM-DD_HHmmss.csv`

## Script Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `-OutputPath` | string | `./SharingAudit_<timestamp>.csv` | Where to write the report |
| `-DelayMs` | int | `100` | Milliseconds between API calls |
| `-SkipOneDrive` | switch | `$false` | Skip OneDrive auditing |
| `-SkipSharePoint` | switch | `$false` | Skip SharePoint auditing |
| `-UsersToAudit` | string[] | all users | Audit specific users only (by UPN) |

## Error Handling & Resilience

- Graph SDK handles 429 retries automatically
- Configurable courtesy delay between API calls
- Users with no OneDrive provisioned: log warning, skip
- Inaccessible sites: log warning with URL, skip
- Network timeouts: retry 3x with exponential backoff, then log and continue
- Token refresh: handled automatically by SDK for interactive sessions
- Partial results flushed to temp file periodically to prevent data loss on crash

## Progress Tracking

- `Write-Progress` with two levels (outer: user/site count, inner: items in current drive)
- Running counter: `[142/650 users] [3,847 shared items found so far]`

## Console Summary

Printed at the end of the run:

- Total items with sharing
- Breakdown by sharing type
- Top 10 users by number of shared items
- Count of anonymous/anyone links

## Scale

Designed for medium tenants (100-1000 users, 20-100 sites). Expected run time under 1 hour.
