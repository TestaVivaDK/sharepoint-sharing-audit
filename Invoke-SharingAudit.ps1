#Requires -Version 7.0

<#
.SYNOPSIS
    Audits sharing permissions across SharePoint Online and OneDrive for Business.

.DESCRIPTION
    Iterates over all users and SharePoint sites in a Microsoft 365 tenant,
    enumerates every sharing permission on every file and folder, and exports
    a CSV report for access cleanup.

.PARAMETER OutputPath
    Path for the CSV report. Defaults to ./SharingAudit_<timestamp>.csv

.PARAMETER DelayMs
    Milliseconds to pause between API calls. Default 100.

.PARAMETER SkipOneDrive
    Skip auditing OneDrive personal sites.

.PARAMETER SkipSharePoint
    Skip auditing SharePoint sites.

.PARAMETER UsersToAudit
    Array of UPNs to audit. If omitted, audits all licensed enabled users.

.EXAMPLE
    ./Invoke-SharingAudit.ps1
    ./Invoke-SharingAudit.ps1 -SkipSharePoint -UsersToAudit "jdoe@contoso.com","asmith@contoso.com"
#>

[CmdletBinding()]
param(
    [string]$OutputPath,

    [ValidateRange(0, 5000)]
    [int]$DelayMs = 100,

    [switch]$SkipOneDrive,

    [switch]$SkipSharePoint,

    [string[]]$UsersToAudit
)

Set-StrictMode -Version 1
$ErrorActionPreference = "Stop"

# --- Normalize UsersToAudit (handles comma-separated string from pwsh -File) ---
if ($UsersToAudit -and $UsersToAudit.Count -eq 1 -and $UsersToAudit[0] -match ',') {
    $UsersToAudit = $UsersToAudit[0] -split ',' | ForEach-Object { $_.Trim() }
}

# --- Default output directory and timestamp ---
$script:timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
if (-not $OutputPath) {
    $OutputPath = Join-Path $PSScriptRoot "SharingAudit_$($script:timestamp).csv"
}
$script:outputDir = Split-Path $OutputPath -Parent
if (-not $script:outputDir) { $script:outputDir = $PSScriptRoot }

# --- Temp file for partial results ---
$tempPath = "$OutputPath.tmp"

# --- Prerequisites check ---
$requiredModules = @(
    "Microsoft.Graph.Authentication",
    "Microsoft.Graph.Users",
    "Microsoft.Graph.Sites",
    "Microsoft.Graph.Files"
)

$missing = @()
foreach ($mod in $requiredModules) {
    if (-not (Get-Module -ListAvailable -Name $mod)) {
        $missing += $mod
    }
}

if ($missing.Count -gt 0) {
    Write-Error ("Missing required modules: $($missing -join ', '). " +
        "Install them with: Install-Module Microsoft.Graph -Scope CurrentUser")
    return
}

foreach ($mod in $requiredModules) {
    Import-Module $mod -ErrorAction Stop
}

Write-Host "All required modules loaded." -ForegroundColor Green

# --- Authenticate ---
$scopes = @(
    "User.Read.All",
    "Sites.Read.All",
    "Files.Read.All"
)

Write-Host "Connecting to Microsoft Graph (interactive sign-in)..." -ForegroundColor Cyan
Connect-MgGraph -Scopes $scopes -ErrorAction Stop
Write-Host "Authenticated successfully." -ForegroundColor Green

# --- Results collection ---
$script:results = [System.Collections.Generic.List[PSCustomObject]]::new()
$script:totalSharedItems = 0
$script:perUserResults = @{}
$script:perUserFiles = @()

# ============================================================
# Helper Functions
# ============================================================

function Invoke-WithRetry {
    <#
    .SYNOPSIS
        Executes a script block with retry on transient failures.
    #>
    param(
        [Parameter(Mandatory)]
        [scriptblock]$ScriptBlock,

        [int]$MaxRetries = 3,

        [int]$BaseDelayMs = 1000
    )

    for ($attempt = 1; $attempt -le ($MaxRetries + 1); $attempt++) {
        try {
            return & $ScriptBlock
        }
        catch {
            if ($attempt -gt $MaxRetries) {
                throw
            }
            $delay = $BaseDelayMs * [math]::Pow(2, $attempt - 1)
            Write-Warning "Attempt $attempt failed: $($_.Exception.Message). Retrying in ${delay}ms..."
            Start-Sleep -Milliseconds $delay
        }
    }
}

function Get-SharingType {
    <#
    .SYNOPSIS
        Classifies a Graph permission object into a human-readable sharing type.
    #>
    param(
        [Parameter(Mandatory)]
        $Permission
    )

    if ($Permission.Link) {
        switch ($Permission.Link.Scope) {
            "anonymous"     { return "Link-Anyone" }
            "organization"  { return "Link-Organization" }
            "users"         { return "Link-SpecificPeople" }
            default         { return "Link-$($Permission.Link.Scope)" }
        }
    }
    elseif ($Permission.GrantedToV2.Group) {
        return "Group"
    }
    elseif ($Permission.GrantedToV2.User -or $Permission.GrantedTo.User) {
        return "User"
    }
    else {
        return "Unknown"
    }
}

function Get-SharedWithInfo {
    <#
    .SYNOPSIS
        Extracts who the item is shared with and classifies as Internal/External/Guest/Anonymous.
    #>
    param(
        [Parameter(Mandatory)]
        $Permission,

        [string]$TenantDomain
    )

    $sharedWith = ""
    $sharedWithType = "Unknown"

    if ($Permission.Link) {
        if ($Permission.Link.Scope -eq "anonymous") {
            $sharedWith = "Anyone with the link"
            $sharedWithType = "Anonymous"
        }
        elseif ($Permission.Link.Scope -eq "organization") {
            $sharedWith = "All organization members"
            $sharedWithType = "Internal"
        }
        elseif ($Permission.GrantedToIdentitiesV2) {
            $identities = @()
            foreach ($identity in $Permission.GrantedToIdentitiesV2) {
                if ($identity.User.Email) {
                    $identities += $identity.User.Email
                }
                elseif ($identity.User.DisplayName) {
                    $identities += $identity.User.DisplayName
                }
            }
            $sharedWith = $identities -join "; "
            # Check if any are external
            $hasExternal = $identities | Where-Object {
                $_ -and $TenantDomain -and ($_ -notlike "*@$TenantDomain")
            }
            $sharedWithType = if ($hasExternal) { "External" } else { "Internal" }
        }
        else {
            $sharedWith = "Specific people (details unavailable)"
            $sharedWithType = "Internal"
        }
    }
    elseif ($Permission.GrantedToV2.Group) {
        $sharedWith = $Permission.GrantedToV2.Group.DisplayName ?? "Unknown Group"
        $sharedWithType = "Internal"
    }
    elseif ($Permission.GrantedToV2.User) {
        $email = $Permission.GrantedToV2.User.Email
        $sharedWith = $email ?? ($Permission.GrantedToV2.User.DisplayName ?? "Unknown User")
        if ($email -and $TenantDomain -and ($email -notlike "*@$TenantDomain")) {
            $sharedWithType = "External"
        }
        else {
            $sharedWithType = "Internal"
        }
    }
    elseif ($Permission.GrantedTo.User) {
        $email = $Permission.GrantedTo.User.Email
        $sharedWith = $email ?? ($Permission.GrantedTo.User.DisplayName ?? "Unknown User")
        if ($email -and $TenantDomain -and ($email -notlike "*@$TenantDomain")) {
            $sharedWithType = "External"
        }
        else {
            $sharedWithType = "Internal"
        }
    }

    # Guest user detection
    if ($sharedWith -match "#EXT#") {
        $sharedWithType = "Guest"
    }

    return @{
        SharedWith     = $sharedWith
        SharedWithType = $sharedWithType
    }
}

function Get-PermissionRole {
    <#
    .SYNOPSIS
        Extracts the role (Read, Write, Owner) from a permission object.
    #>
    param(
        [Parameter(Mandatory)]
        $Permission
    )

    if ($Permission.Roles -contains "owner") { return "Owner" }
    if ($Permission.Roles -contains "write") { return "Write" }
    if ($Permission.Roles -contains "read")  { return "Read" }
    if ($Permission.Link.Type -eq "edit")    { return "Write" }
    if ($Permission.Link.Type -eq "view")    { return "Read" }
    return ($Permission.Roles -join ", ")
}

function Add-PermissionRecord {
    <#
    .SYNOPSIS
        Creates a result record and adds it to the results collection.
    #>
    param(
        [string]$Source,
        [string]$SiteName,
        [string]$SiteUrl,
        [string]$ItemPath,
        [string]$ItemType,
        $Permission,
        [string]$OwnerEmail,
        [string]$OwnerDisplayName,
        [string]$TenantDomain
    )

    $sharingType = Get-SharingType -Permission $Permission
    $sharedWithInfo = Get-SharedWithInfo -Permission $Permission -TenantDomain $TenantDomain
    $role = Get-PermissionRole -Permission $Permission

    # Skip "owner" permissions that are just the item owner themselves
    if ($role -eq "Owner" -and $sharedWithInfo.SharedWith -eq $OwnerEmail) {
        return
    }

    $record = [PSCustomObject]@{
        Source           = $Source
        SiteName         = $SiteName
        SiteUrl          = $SiteUrl
        ItemPath         = $ItemPath
        ItemType         = $ItemType
        SharingType      = $sharingType
        SharedWith       = $sharedWithInfo.SharedWith
        SharedWithType   = $sharedWithInfo.SharedWithType
        Role             = $role
        CreatedDateTime  = if ($Permission.PSObject.Properties['CreatedDateTime']) { $Permission.CreatedDateTime } else { "" }
        OwnerEmail       = $OwnerEmail
        OwnerDisplayName = $OwnerDisplayName
    }

    $script:results.Add($record)
    $script:totalSharedItems++
}

# ============================================================
# Drive Item Walker
# ============================================================

function Get-DriveItemPermissions {
    <#
    .SYNOPSIS
        Gets non-inherited sharing permissions for a drive item.
    #>
    param(
        [Parameter(Mandatory)]
        [string]$DriveId,

        [Parameter(Mandatory)]
        [string]$ItemId
    )

    try {
        $permissions = Invoke-WithRetry -ScriptBlock {
            Get-MgDriveItemPermission -DriveId $DriveId -DriveItemId $ItemId -All
        }

        # Filter out inherited permissions — we only want explicit shares
        # InheritedFrom is an empty object {} for non-inherited, populated for inherited
        $explicit = $permissions | Where-Object {
            -not $_.InheritedFrom.DriveId -and -not $_.InheritedFrom.Path
        }

        return $explicit
    }
    catch {
        Write-Warning "  Could not get permissions for item $ItemId in drive $DriveId : $($_.Exception.Message)"
        return @()
    }
}

function Get-DriveItemsRecursive {
    <#
    .SYNOPSIS
        Recursively walks a drive and processes sharing permissions for each item.
    #>
    param(
        [Parameter(Mandatory)]
        [string]$DriveId,

        [string]$ParentItemId = "root",

        [string]$ParentPath = "",

        [Parameter(Mandatory)]
        [string]$Source,

        [Parameter(Mandatory)]
        [string]$SiteName,

        [Parameter(Mandatory)]
        [string]$SiteUrl,

        [string]$OwnerEmail = "",

        [string]$OwnerDisplayName = "",

        [Parameter(Mandatory)]
        [string]$TenantDomain,

        [int]$DelayMs = 100
    )

    try {
        $children = Invoke-WithRetry -ScriptBlock {
            Get-MgDriveItemChild -DriveId $DriveId -DriveItemId $ParentItemId -All
        }
    }
    catch {
        Write-Warning "  Could not list children of $ParentPath in drive $DriveId : $($_.Exception.Message)"
        return
    }

    foreach ($item in $children) {
        $itemPath = if ($ParentPath) { "$ParentPath/$($item.Name)" } else { "/$($item.Name)" }
        $itemType = if ($item.Folder) { "Folder" } else { "File" }

        # Get permissions for this item
        $permissions = Get-DriveItemPermissions -DriveId $DriveId -ItemId $item.Id

        foreach ($perm in $permissions) {
            Add-PermissionRecord `
                -Source $Source `
                -SiteName $SiteName `
                -SiteUrl $SiteUrl `
                -ItemPath $itemPath `
                -ItemType $itemType `
                -Permission $perm `
                -OwnerEmail $OwnerEmail `
                -OwnerDisplayName $OwnerDisplayName `
                -TenantDomain $TenantDomain
        }

        # Recurse into folders
        if ($item.Folder -and $item.Folder.ChildCount -gt 0) {
            Get-DriveItemsRecursive `
                -DriveId $DriveId `
                -ParentItemId $item.Id `
                -ParentPath $itemPath `
                -Source $Source `
                -SiteName $SiteName `
                -SiteUrl $SiteUrl `
                -OwnerEmail $OwnerEmail `
                -OwnerDisplayName $OwnerDisplayName `
                -TenantDomain $TenantDomain `
                -DelayMs $DelayMs
        }

        if ($DelayMs -gt 0) {
            Start-Sleep -Milliseconds $DelayMs
        }
    }
}

try {

# ============================================================
# Tenant Domain Detection
# ============================================================

$tenantDomain = $null

# Try Get-MgOrganization first
try {
    $orgInfo = Get-MgOrganization -ErrorAction Stop
    $tenantDomain = ($orgInfo.VerifiedDomains | Where-Object { $_.IsDefault }).Name
}
catch {
    Write-Warning "Could not query organization info: $($_.Exception.Message)"
}

# Fallback: derive from the signed-in context or user UPNs
if (-not $tenantDomain) {
    $context = Get-MgContext
    if ($context.Account -match '@(.+)$') {
        $tenantDomain = $Matches[1]
    }
    elseif ($UsersToAudit -and $UsersToAudit[0] -match '@(.+)$') {
        $tenantDomain = $Matches[1]
    }
}

if ($tenantDomain) {
    Write-Host "Tenant domain: $tenantDomain" -ForegroundColor Cyan
}
else {
    Write-Warning "Could not detect tenant domain. External/internal classification may be inaccurate."
    $tenantDomain = ""
}

# ============================================================
# OneDrive Audit
# ============================================================

if (-not $SkipOneDrive) {
    Write-Host "`n=== Starting OneDrive Audit ===" -ForegroundColor Cyan

    # Get users
    if ($UsersToAudit) {
        $users = @()
        foreach ($upn in $UsersToAudit) {
            try {
                $users += Get-MgUser -UserId $upn -Property "Id,DisplayName,UserPrincipalName,AccountEnabled,AssignedLicenses"
            }
            catch {
                Write-Warning "Could not find user $upn — skipping. Error: $($_.Exception.Message)"
            }
        }
    }
    else {
        $users = Invoke-WithRetry -ScriptBlock {
            Get-MgUser -All -Property "Id,DisplayName,UserPrincipalName,AccountEnabled,AssignedLicenses" -Filter "accountEnabled eq true"
        }
        # Filter to only licensed users
        $users = $users | Where-Object { $_.AssignedLicenses.Count -gt 0 }
    }

    $userCount = ($users | Measure-Object).Count
    Write-Host "Found $userCount users to audit." -ForegroundColor Green

    $userIndex = 0
    foreach ($user in $users) {
        $userIndex++
        $upn = $user.UserPrincipalName
        $displayName = $user.DisplayName

        Write-Progress -Activity "Auditing OneDrive" `
            -Status "[$userIndex/$userCount] $displayName ($upn)" `
            -PercentComplete (($userIndex / $userCount) * 100) `
            -Id 1

        Write-Host "[$userIndex/$userCount] OneDrive: $displayName ($upn) [$($script:totalSharedItems) shared items found]"

        # Get user's default OneDrive (singular /drive endpoint, not /drives)
        try {
            $driveResponse = Invoke-WithRetry -ScriptBlock {
                Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/users/$($user.Id)/drive"
            }
            $driveId = $driveResponse.id
            $driveUrl = $driveResponse.webUrl ?? "N/A"
            if (-not $driveId) {
                Write-Warning "  No OneDrive for $upn — skipping."
                continue
            }
        }
        catch {
            Write-Warning "  No OneDrive for $upn — skipping."
            continue
        }

        $beforeCount = $script:results.Count

        Get-DriveItemsRecursive `
            -DriveId $driveId `
            -Source "OneDrive" `
            -SiteName $displayName `
            -SiteUrl $driveUrl `
            -OwnerEmail $upn `
            -OwnerDisplayName $displayName `
            -TenantDomain $tenantDomain `
            -DelayMs $DelayMs

        # Export per-user CSV
        $userItemCount = $script:results.Count - $beforeCount
        if ($userItemCount -gt 0) {
            $safeUpn = $upn -replace '[\\/:*?"<>|]', '_'
            $userCsvPath = Join-Path $script:outputDir "SharingAudit_${safeUpn}_$($script:timestamp).csv"
            $userItems = $script:results | Select-Object -Last $userItemCount
            $userItems | Export-Csv -Path $userCsvPath -NoTypeInformation -Encoding UTF8
            $script:perUserFiles += $userCsvPath
            Write-Host "  -> $userItemCount items exported to: $userCsvPath" -ForegroundColor Green
        }

        # Flush partial results periodically (every 25 users)
        if ($userIndex % 25 -eq 0 -and $script:results.Count -gt 0) {
            $script:results | Export-Csv -Path $tempPath -NoTypeInformation -Force
            Write-Host "  (Partial results saved: $($script:results.Count) records)" -ForegroundColor DarkGray
        }
    }

    Write-Progress -Activity "Auditing OneDrive" -Completed -Id 1
    Write-Host "OneDrive audit complete.`n" -ForegroundColor Green
}

# ============================================================
# SharePoint Audit
# ============================================================

if (-not $SkipSharePoint) {
    Write-Host "=== Starting SharePoint Audit ===" -ForegroundColor Cyan

    # Get all sites — try multiple approaches since Search may require admin consent
    $sites = @()

    # Approach 1: Search API
    try {
        $searchResults = Invoke-WithRetry -ScriptBlock {
            Get-MgSite -Search "*" -All -Property "Id,DisplayName,WebUrl"
        }
        if ($searchResults) { $sites = @($searchResults) }
    }
    catch {
        Write-Warning "  Site search failed: $($_.Exception.Message)"
    }

    # Approach 2: If search returned nothing, enumerate from root site
    if ($sites.Count -eq 0) {
        Write-Host "  Search returned 0 sites. Trying root site enumeration..." -ForegroundColor Yellow
        try {
            $rootSiteResponse = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/sites/root"
            $rootSiteId = $rootSiteResponse.id

            # Get subsites of root
            $subsitesResponse = Invoke-MgGraphRequest -Method GET -Uri "https://graph.microsoft.com/v1.0/sites/$rootSiteId/sites"
            $sites = @($subsitesResponse.value | ForEach-Object {
                [PSCustomObject]@{
                    Id          = $_.id
                    DisplayName = $_.displayName
                    WebUrl      = $_.webUrl
                }
            })

            # Also include the root site itself
            $sites += [PSCustomObject]@{
                Id          = $rootSiteResponse.id
                DisplayName = $rootSiteResponse.displayName
                WebUrl      = $rootSiteResponse.webUrl
            }

            # Handle pagination
            $nextLink = $subsitesResponse.'@odata.nextLink'
            while ($nextLink) {
                $page = Invoke-MgGraphRequest -Method GET -Uri $nextLink
                $sites += @($page.value | ForEach-Object {
                    [PSCustomObject]@{
                        Id          = $_.id
                        DisplayName = $_.displayName
                        WebUrl      = $_.webUrl
                    }
                })
                $nextLink = $page.'@odata.nextLink'
            }
        }
        catch {
            Write-Warning "  Root site enumeration failed: $($_.Exception.Message)"
        }
    }

    # Filter out personal OneDrive sites (already covered above) and system sites
    $sites = $sites | Where-Object {
        $_.WebUrl -and
        $_.WebUrl -notmatch "-my\.sharepoint\.com" -and
        $_.DisplayName -ne $null
    }

    $siteCount = ($sites | Measure-Object).Count
    Write-Host "Found $siteCount SharePoint sites to audit." -ForegroundColor Green

    $siteIndex = 0
    foreach ($site in $sites) {
        $siteIndex++
        $siteName = $site.DisplayName ?? $site.WebUrl
        $siteUrl = $site.WebUrl

        Write-Progress -Activity "Auditing SharePoint" `
            -Status "[$siteIndex/$siteCount] $siteName" `
            -PercentComplete (($siteIndex / $siteCount) * 100) `
            -Id 1

        Write-Host "[$siteIndex/$siteCount] SharePoint: $siteName [$($script:totalSharedItems) shared items found]"

        # Get all document libraries (drives) in the site
        try {
            $drives = Invoke-WithRetry -ScriptBlock {
                Get-MgSiteDrive -SiteId $site.Id -All
            }
        }
        catch {
            Write-Warning "  Could not access drives for site '$siteName' ($siteUrl) — skipping."
            continue
        }

        $driveIndex = 0
        $driveCount = ($drives | Measure-Object).Count

        foreach ($drive in $drives) {
            $driveIndex++
            $driveName = $drive.Name ?? "Unnamed Library"

            Write-Progress -Activity "Scanning library" `
                -Status "[$driveIndex/$driveCount] $driveName" `
                -PercentComplete (($driveIndex / $driveCount) * 100) `
                -ParentId 1 `
                -Id 2

            # Determine site owner (best effort)
            $ownerEmail = ""
            $ownerDisplayName = ""
            if ($drive.Owner.User) {
                $ownerEmail = $drive.Owner.User.Email ?? ""
                $ownerDisplayName = $drive.Owner.User.DisplayName ?? ""
            }
            elseif ($drive.Owner.Group) {
                $ownerDisplayName = $drive.Owner.Group.DisplayName ?? ""
            }

            Get-DriveItemsRecursive `
                -DriveId $drive.Id `
                -Source "SharePoint" `
                -SiteName "$siteName / $driveName" `
                -SiteUrl $siteUrl `
                -OwnerEmail $ownerEmail `
                -OwnerDisplayName $ownerDisplayName `
                -TenantDomain $tenantDomain `
                -DelayMs $DelayMs
        }

        Write-Progress -Activity "Scanning library" -Completed -Id 2

        # Flush partial results periodically (every 10 sites)
        if ($siteIndex % 10 -eq 0 -and $script:results.Count -gt 0) {
            $script:results | Export-Csv -Path $tempPath -NoTypeInformation -Force
            Write-Host "  (Partial results saved: $($script:results.Count) records)" -ForegroundColor DarkGray
        }
    }

    Write-Progress -Activity "Auditing SharePoint" -Completed -Id 1
    Write-Host "SharePoint audit complete.`n" -ForegroundColor Green
}

# ============================================================
# Export Results
# ============================================================

if ($script:results.Count -eq 0) {
    Write-Host "No shared items found. No report generated." -ForegroundColor Yellow
}
else {
    # Combined report
    $script:results | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8
    Write-Host "`nCombined report: $OutputPath ($($script:results.Count) records)" -ForegroundColor Green

    # List per-user reports
    if ($script:perUserFiles.Count -gt 0) {
        Write-Host "`nPer-user reports:" -ForegroundColor Green
        foreach ($f in $script:perUserFiles) {
            $count = (Import-Csv $f | Measure-Object).Count
            Write-Host "  $f ($count records)" -ForegroundColor Green
        }
    }
}

# Clean up temp file
if (Test-Path $tempPath) {
    Remove-Item $tempPath -Force
}

# ============================================================
# Console Summary
# ============================================================

if ($script:results.Count -gt 0) {
    Write-Host "`n====================================" -ForegroundColor Cyan
    Write-Host "       SHARING AUDIT SUMMARY        " -ForegroundColor Cyan
    Write-Host "====================================`n" -ForegroundColor Cyan

    # Total
    Write-Host "Total shared items: $($script:results.Count)" -ForegroundColor White

    # By sharing type
    Write-Host "`nBreakdown by Sharing Type:" -ForegroundColor Yellow
    $script:results | Group-Object SharingType | Sort-Object Count -Descending | ForEach-Object {
        Write-Host ("  {0,-25} {1,6}" -f $_.Name, $_.Count)
    }

    # By shared-with type
    Write-Host "`nBreakdown by Shared-With Type:" -ForegroundColor Yellow
    $script:results | Group-Object SharedWithType | Sort-Object Count -Descending | ForEach-Object {
        Write-Host ("  {0,-25} {1,6}" -f $_.Name, $_.Count)
    }

    # Anonymous links (high risk)
    $anonymousCount = ($script:results | Where-Object { $_.SharingType -eq "Link-Anyone" } | Measure-Object).Count
    if ($anonymousCount -gt 0) {
        Write-Host "`n** WARNING: $anonymousCount anonymous (Anyone) links found **" -ForegroundColor Red
    }

    # External sharing
    $externalCount = ($script:results | Where-Object { $_.SharedWithType -in @("External", "Guest") } | Measure-Object).Count
    if ($externalCount -gt 0) {
        Write-Host "** $externalCount items shared externally (External + Guest) **" -ForegroundColor Yellow
    }

    # Top 10 sharers
    Write-Host "`nTop 10 Users by Shared Items:" -ForegroundColor Yellow
    $script:results | Group-Object OwnerEmail | Sort-Object Count -Descending | Select-Object -First 10 | ForEach-Object {
        $name = if ($_.Name) { $_.Name } else { "(unknown owner)" }
        Write-Host ("  {0,-40} {1,6}" -f $name, $_.Count)
    }

    # By source
    Write-Host "`nBy Source:" -ForegroundColor Yellow
    $script:results | Group-Object Source | ForEach-Object {
        Write-Host ("  {0,-25} {1,6}" -f $_.Name, $_.Count)
    }

    Write-Host "`n====================================" -ForegroundColor Cyan
}

}
finally {
    # Disconnect — runs even if an error occurs mid-script
    try { Disconnect-MgGraph | Out-Null } catch {}
    Write-Host "`nDisconnected from Microsoft Graph. Done." -ForegroundColor Green
}
