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

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# --- Default output path ---
if (-not $OutputPath) {
    $timestamp = Get-Date -Format "yyyy-MM-dd_HHmmss"
    $OutputPath = Join-Path $PSScriptRoot "SharingAudit_$timestamp.csv"
}

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
