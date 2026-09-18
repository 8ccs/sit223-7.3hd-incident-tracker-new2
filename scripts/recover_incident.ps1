<#
.SYNOPSIS
  Recovery half of the incident simulation: redeploys the environment's
  CURRENT version (no rebuild, no version change) so the running process
  comes back on the same port with the same data. Restores Prometheus's
  `up` metric to 1 and lets the firing alert resolve.
#>
param(
    [string]$Root = "C:\devops-demo",
    [string]$Environment = "production",
    [string]$ArtifactStore = "C:\devops-demo\artifacts"
)

$ErrorActionPreference = "Stop"
$envRoot = Join-Path $Root $Environment
$versionFile = Join-Path $envRoot "current_version.txt"

if (-not (Test-Path $versionFile)) {
    throw "No current_version.txt for $Environment. Nothing to recover -- deploy/release first."
}

$zipName = Get-Content $versionFile
$zipPath = Join-Path $ArtifactStore $zipName
if (-not (Test-Path $zipPath)) {
    throw "Artifact '$zipName' not found in $ArtifactStore."
}

Write-Host "Recovering $Environment by restarting current version $zipName"
& "$PSScriptRoot\deploy.ps1" -Environment $Environment -ZipPath $zipPath -Root $Root
Write-Host "Recovered. Prometheus will mark the '$Environment' target up again after its next scrape."
