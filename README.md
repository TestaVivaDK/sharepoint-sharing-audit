# SharePoint & OneDrive Sharing Audit

A PowerShell script that audits all sharing permissions across your Microsoft 365 tenant's SharePoint sites and OneDrive accounts, producing a CSV report for access cleanup.

## Prerequisites

- **PowerShell 7.0+** ([Install guide](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell))
- **Microsoft Graph PowerShell SDK**
- **Admin role**: Global Admin or SharePoint Admin on the target tenant

### Install the Graph modules

```powershell
Install-Module Microsoft.Graph -Scope CurrentUser
```

This installs all submodules. The script uses:
- `Microsoft.Graph.Authentication`
- `Microsoft.Graph.Users`
- `Microsoft.Graph.Sites`
- `Microsoft.Graph.Files`

## Usage

### Basic: Full audit of all users and sites

```powershell
./Invoke-SharingAudit.ps1
```

A browser window will open for interactive sign-in. Sign in with your admin account. The script will:

1. Enumerate all licensed, enabled users and walk their OneDrive files
2. Enumerate all SharePoint sites and walk their document libraries
3. Export results to `SharingAudit_YYYY-MM-DD_HHmmss.csv` in the script directory

### Audit specific users only

```powershell
./Invoke-SharingAudit.ps1 -UsersToAudit "jdoe@contoso.com","asmith@contoso.com"
```

### Skip OneDrive or SharePoint

```powershell
# SharePoint sites only
./Invoke-SharingAudit.ps1 -SkipOneDrive

# OneDrive only
./Invoke-SharingAudit.ps1 -SkipSharePoint
```

### Custom output path

```powershell
./Invoke-SharingAudit.ps1 -OutputPath "C:\Reports\audit.csv"
```

### Adjust API throttling

Default is 100ms between API calls. Increase if you hit rate limits, decrease if you want faster runs:

```powershell
./Invoke-SharingAudit.ps1 -DelayMs 200
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `-OutputPath` | string | `./SharingAudit_<timestamp>.csv` | Path for the CSV report |
| `-DelayMs` | int | `100` | Milliseconds to pause between API calls (0-5000) |
| `-SkipOneDrive` | switch | off | Skip OneDrive personal sites |
| `-SkipSharePoint` | switch | off | Skip SharePoint sites |
| `-UsersToAudit` | string[] | all users | Specific user UPNs to audit |

## Output

### CSV columns

Each row represents one sharing permission on one item. A file shared with 3 people produces 3 rows.

| Column | Example |
|--------|---------|
| `Source` | `OneDrive` or `SharePoint` |
| `SiteName` | `Jane Doe` or `Marketing Team / Documents` |
| `SiteUrl` | `https://contoso-my.sharepoint.com/personal/jdoe` |
| `ItemPath` | `/Documents/Budget 2026.xlsx` |
| `ItemType` | `File` or `Folder` |
| `SharingType` | `Link-Anyone`, `Link-Organization`, `Link-SpecificPeople`, `User`, `Group` |
| `SharedWith` | `vendor@gmail.com` or `Anyone with the link` |
| `SharedWithType` | `Internal`, `External`, `Guest`, `Anonymous` |
| `Role` | `Read`, `Write`, `Owner` |
| `CreatedDateTime` | `2025-08-14T10:30:00Z` |
| `OwnerEmail` | `jdoe@contoso.com` |
| `OwnerDisplayName` | `Jane Doe` |

### Console summary

After the CSV export, the script prints a summary:

```
====================================
       SHARING AUDIT SUMMARY
====================================

Total shared items: 1,247

Breakdown by Sharing Type:
  User                          834
  Link-Organization             203
  Link-SpecificPeople           112
  Group                          67
  Link-Anyone                    31

Breakdown by Shared-With Type:
  Internal                      987
  External                      198
  Guest                          43
  Anonymous                      19

** WARNING: 31 anonymous (Anyone) links found **
** 241 items shared externally (External + Guest) **

Top 10 Users by Shared Items:
  jdoe@contoso.com                           142
  asmith@contoso.com                          98
  ...
```

## Cleanup workflow

1. Run the audit
2. Open the CSV in Excel
3. **High priority**: Filter `SharingType` = `Link-Anyone` -- these are anonymous links accessible without authentication
4. **Medium priority**: Filter `SharedWithType` = `External` or `Guest` -- items shared outside the organization
5. **Review**: Filter by specific users or sites to check for over-sharing
6. Revoke or adjust permissions in SharePoint admin center or per-site

## Estimated run times

| Tenant size | Users | Sites | Estimated time |
|-------------|-------|-------|----------------|
| Small | < 100 | < 20 | 5-15 minutes |
| Medium | 100-1000 | 20-100 | 15-45 minutes |
| Large | 1000+ | 100+ | 1-4 hours |

## Error handling

- **No OneDrive provisioned**: Warns and skips the user
- **Inaccessible site**: Warns and skips the site
- **API rate limiting (429)**: Automatically retried by the Graph SDK
- **Network timeouts**: Retried 3 times with exponential backoff
- **Crash resilience**: Partial results are saved to a `.tmp` file every 25 users / 10 sites

## Required Graph API permissions

The script requests these **delegated** (read-only) scopes during interactive sign-in:

| Scope | Purpose |
|-------|---------|
| `User.Read.All` | Enumerate all users in the tenant |
| `Sites.Read.All` | Read all SharePoint sites and content |
| `Files.Read.All` | Read all OneDrive files and sharing permissions |
| `Organization.Read.All` | Detect tenant domain for internal/external classification |

The script never modifies or revokes any permissions. It is read-only.
