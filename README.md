# SharePoint & OneDrive Sharing Audit

A PowerShell script that audits all sharing permissions across your Microsoft 365 tenant's SharePoint sites and OneDrive accounts, producing per-user and combined CSV reports for access cleanup.

## Prerequisites

- **PowerShell 7.0+** ([Install guide](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell))
- **Microsoft Graph PowerShell SDK**

```powershell
Install-Module Microsoft.Graph -Scope CurrentUser
```

The script uses these submodules (installed automatically with the above):
- `Microsoft.Graph.Authentication`
- `Microsoft.Graph.Users`
- `Microsoft.Graph.Sites`
- `Microsoft.Graph.Files`

## Authentication

There are two ways to authenticate. **App-only is strongly recommended** for a full tenant audit.

### Option 1: App-only authentication (recommended)

App-only auth uses an Azure AD app registration with **application permissions**. This gives the script full read access to all users' OneDrive and SharePoint files, regardless of who runs the script.

Interactive sign-in (Option 2) can only see files the signed-in user already has access to, which means you will miss most sharing data for other users.

```powershell
# With client secret
./Invoke-SharingAudit.ps1 -TenantId "your-tenant-id" -ClientId "your-app-id" -ClientSecret "your-secret"

# With certificate
./Invoke-SharingAudit.ps1 -TenantId "your-tenant-id" -ClientId "your-app-id" -CertificateThumbprint "your-thumbprint"
```

#### How to set up the app registration

**Step 1: Create the app**

1. Go to [Azure Portal](https://portal.azure.com)
2. Navigate to **Microsoft Entra ID** (formerly Azure Active Directory) > **App registrations**
3. Click **New registration**
4. Fill in:
   - **Name**: `SharePoint Sharing Audit` (or any name you prefer)
   - **Supported account types**: "Accounts in this organizational directory only (Single tenant)"
   - **Redirect URI**: Leave blank
5. Click **Register**
6. On the overview page, copy and save:
   - **Application (client) ID** — this is your `-ClientId`
   - **Directory (tenant) ID** — this is your `-TenantId`

**Step 2: Create a client secret**

1. In your app registration, go to **Certificates & secrets**
2. Click **New client secret**
3. Set a description (e.g. "Sharing Audit") and expiry (e.g. 6 months)
4. Click **Add**
5. **Copy the secret value immediately** — you cannot retrieve it later. This is your `-ClientSecret`

Alternatively, use a certificate for more secure authentication:
1. Go to **Certificates & secrets** > **Certificates** > **Upload certificate**
2. Upload your `.cer` or `.pem` file
3. Note the **Thumbprint** — this is your `-CertificateThumbprint`

**Step 3: Grant API permissions**

1. In your app registration, go to **API permissions**
2. Click **Add a permission** > **Microsoft Graph** > **Application permissions**
3. Search for and add each of these permissions:

| Permission | Why it's needed |
|------------|-----------------|
| `User.Read.All` | Enumerate all users in the tenant |
| `Sites.Read.All` | Read all SharePoint sites and their document libraries |
| `Files.Read.All` | Read all users' OneDrive files and sharing permissions |

4. Click **Grant admin consent for [your organization]**
5. Verify all three permissions show a green checkmark under "Status"

These are all **read-only** permissions. The script never modifies, creates, or deletes any files or permissions.

**Step 4: Run the script**

```powershell
./Invoke-SharingAudit.ps1 `
    -TenantId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
    -ClientId "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" `
    -ClientSecret "your-secret-value"
```

### Option 2: Interactive sign-in (limited)

```powershell
./Invoke-SharingAudit.ps1
```

A browser window will open for sign-in. You must sign in with a Global Admin or SharePoint Admin account.

**Limitation:** Delegated auth can only see files the signed-in user has access to. Other users' unshared files will not appear in the report. This makes it unsuitable for a full tenant audit.

## Usage examples

### Audit all users in the tenant

```powershell
./Invoke-SharingAudit.ps1 -TenantId "..." -ClientId "..." -ClientSecret "..."
```

### Audit specific users only

```powershell
./Invoke-SharingAudit.ps1 -TenantId "..." -ClientId "..." -ClientSecret "..." `
    -UsersToAudit "jdoe@contoso.com","asmith@contoso.com","bwilson@contoso.com"
```

### Skip OneDrive or SharePoint

```powershell
# SharePoint sites only
./Invoke-SharingAudit.ps1 -TenantId "..." -ClientId "..." -ClientSecret "..." -SkipOneDrive

# OneDrive only
./Invoke-SharingAudit.ps1 -TenantId "..." -ClientId "..." -ClientSecret "..." -SkipSharePoint
```

### Custom output path

```powershell
./Invoke-SharingAudit.ps1 -TenantId "..." -ClientId "..." -ClientSecret "..." -OutputPath "C:\Reports\audit.csv"
```

### Adjust API throttling

Default is 100ms between API calls. Increase if you hit rate limits:

```powershell
./Invoke-SharingAudit.ps1 -TenantId "..." -ClientId "..." -ClientSecret "..." -DelayMs 200
```

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `-TenantId` | string | — | Azure AD tenant ID (for app-only auth) |
| `-ClientId` | string | — | App registration client ID (for app-only auth) |
| `-ClientSecret` | string | — | Client secret (use this or `-CertificateThumbprint`) |
| `-CertificateThumbprint` | string | — | Certificate thumbprint (use this or `-ClientSecret`) |
| `-OutputPath` | string | `./SharingAudit_<timestamp>.csv` | Path for the combined CSV report |
| `-DelayMs` | int | `100` | Milliseconds to pause between API calls (0-5000) |
| `-SkipOneDrive` | switch | off | Skip OneDrive personal sites |
| `-SkipSharePoint` | switch | off | Skip SharePoint sites |
| `-UsersToAudit` | string[] | all users | Specific user UPNs to audit |

## Output

### Per-user CSV files

Each user with shared items gets their own CSV file:

```
SharingAudit_jdoe@contoso.com_2026-02-17_183000.csv
SharingAudit_asmith@contoso.com_2026-02-17_183000.csv
```

A combined report with all users is also generated:

```
SharingAudit_2026-02-17_183000.csv
```

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

1. Run the audit with app-only auth
2. Open the per-user CSVs in Excel
3. **High priority**: Filter `SharingType` = `Link-Anyone` -- anonymous links accessible without authentication
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

## Security notes

- The script is **read-only** -- it never modifies or revokes any permissions
- Store client secrets securely (e.g. Azure Key Vault, environment variables). Do not commit them to source control
- The app registration only needs read permissions. Do not grant write permissions
- Consider using a certificate instead of a client secret for production use
- Set a short expiry on client secrets and rotate them regularly
