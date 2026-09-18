<#
.SYNOPSIS
  Rollback: redeploy the previous known-good artifact for an environment
  WITHOUT rebuilding anything. Reads previous_version.txt (written by
  deploy.ps1 before it overwrote current_version.txt) and reuses the
  already-extracted release folder if present, otherwise re-expands the
  zip from the artifact store.

.PARAMETER Environment
  "staging" or "production".
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("staging", "production")][string]$Environment,
    [string]$Root = "C:\devops-demo",
    [string]$ArtifactStore = "C:\devops-demo\artifacts"
)

$ErrorActionPreference = "Stop"

$envRoot = Join-Path $Root $Environment
$previousFile = Join-Path $envRoot "previous_version.txt"

if (-not (Test-Path $previousFile)) {
    throw "No previous_version.txt for $Environment. This is the first release to this environment, so there is nothing to roll back to yet."
}

$previousZipName = Get-Content $previousFile
$zipPath = Join-Path $ArtifactStore $previousZipName
if (-not (Test-Path $zipPath)) {
    throw "Previous artifact '$previousZipName' is no longer in the artifact store ($ArtifactStore). Cannot roll back."
}

Write-Host "Rolling $Environment back to $previousZipName"
& "$PSScriptRoot\deploy.ps1" -Environment $Environment -ZipPath $zipPath -Root $Root

Write-Host "Rollback complete: $Environment is now running $previousZipName"
