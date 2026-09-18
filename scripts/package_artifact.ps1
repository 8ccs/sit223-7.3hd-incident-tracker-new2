<#
.SYNOPSIS
  Build stage: produce a versioned, deployable artifact (a zip of the app)
  and a version manifest. No Docker is installed on this agent, so the
  artifact is a self-contained zip rather than an image; Deploy/Release
  extract it into an isolated venv per environment.

.PARAMETER Version
  Human-readable version, e.g. "1.0.0".

.PARAMETER GitCommit
  Short git commit SHA the artifact was built from.

.PARAMETER BuildNumber
  Jenkins BUILD_NUMBER.

.PARAMETER OutputDir
  Where to write the artifact. Defaults to ./dist.

.PARAMETER ArtifactStore
  Optional second location (a shared "artifact store" directory) that
  later stages read from, independent of the Jenkins workspace, so a
  workspace wipe cannot lose a released version.
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [Parameter(Mandatory = $true)][string]$GitCommit,
    [Parameter(Mandatory = $true)][string]$BuildNumber,
    [string]$OutputDir = "dist",
    [string]$ArtifactStore = "C:\devops-demo\artifacts"
)

$ErrorActionPreference = "Stop"

$fullVersion = "$Version+build.$BuildNumber.$GitCommit"
Write-Host "Packaging artifact version: $fullVersion"

if (Test-Path $OutputDir) { Remove-Item $OutputDir -Recurse -Force }
New-Item -ItemType Directory -Path $OutputDir | Out-Null

$stage = Join-Path $OutputDir "stage"
New-Item -ItemType Directory -Path $stage | Out-Null

Copy-Item -Recurse "app" (Join-Path $stage "app")
Copy-Item -Recurse "templates" (Join-Path $stage "templates")
Copy-Item -Recurse "static" (Join-Path $stage "static")
Copy-Item "requirements.txt" $stage

$manifest = @{
    version      = $Version
    full_version = $fullVersion
    git_commit   = $GitCommit
    build_number = $BuildNumber
    built_at_utc = (Get-Date).ToUniversalTime().ToString("o")
} | ConvertTo-Json

Set-Content -Path (Join-Path $stage "version.json") -Value $manifest -Encoding utf8

$zipName = "incident-tracker-$fullVersion.zip"
$zipPath = Join-Path $OutputDir $zipName
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zipPath

Write-Host "Artifact created: $zipPath"

New-Item -ItemType Directory -Path $ArtifactStore -Force | Out-Null
Copy-Item $zipPath (Join-Path $ArtifactStore $zipName) -Force
Write-Host "Artifact copied to artifact store: $ArtifactStore\$zipName"

# Emit machine-readable output for later Jenkinsfile stages.
$result = @{
    zip_path      = (Resolve-Path $zipPath).Path
    zip_name      = $zipName
    full_version  = $fullVersion
    store_path    = (Join-Path $ArtifactStore $zipName)
} | ConvertTo-Json
Set-Content -Path (Join-Path $OutputDir "build-result.json") -Value $result -Encoding utf8
Write-Host $result

# Plain-text companions so the Jenkinsfile can read values with a simple
# readFile() step, with no JSON-parsing plugin dependency.
# IMPORTANT: "-Encoding utf8" in Windows PowerShell 5.1 writes a UTF-8
# byte-order mark. Jenkins' readFile().trim() does NOT strip a BOM (Java's
# String.trim() does not treat U+FEFF as whitespace), so env.ARTIFACT_ZIP_PATH
# would silently gain an invisible leading character and Test-Path would
# then fail to find a file that visibly "looks" like it exists. ASCII
# encoding never adds a BOM, and every value written here (paths, commit
# hashes, version numbers) is always plain ASCII, so it is a safe fix.
Set-Content -Path (Join-Path $OutputDir "artifact-name.txt") -Value $zipName -Encoding ascii -NoNewline
Set-Content -Path (Join-Path $OutputDir "artifact-path.txt") -Value (Resolve-Path $zipPath).Path -Encoding ascii -NoNewline
Set-Content -Path (Join-Path $OutputDir "full-version.txt") -Value $fullVersion -Encoding ascii -NoNewline
