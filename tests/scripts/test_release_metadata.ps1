<#
.SYNOPSIS
  Focused regression test for the rollback-history bug: recovering
  (redeploying) the SAME version must not overwrite the last DISTINCT
  known-good version recorded in previous_version.txt.

  Pure file-based test against scripts/lib/ReleaseMetadata.ps1 -- no real
  deployment, process, or port involved, so it runs fast and standalone.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File tests\scripts\test_release_metadata.ps1
#>
$ErrorActionPreference = "Stop"
$failures = 0

function Assert-Equal {
    param($Actual, $Expected, [string]$Message)
    if ($Actual -ne $Expected) {
        Write-Host "[FAIL] $Message -- expected '$Expected', got '$Actual'"
        $script:failures++
    } else {
        Write-Host "[PASS] $Message"
    }
}

function Assert-Null {
    param($Actual, [string]$Message)
    if ($null -ne $Actual -and $Actual -ne "") {
        Write-Host "[FAIL] $Message -- expected null/empty, got '$Actual'"
        $script:failures++
    } else {
        Write-Host "[PASS] $Message"
    }
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $repoRoot "scripts\lib\ReleaseMetadata.ps1")

$testRoot = Join-Path $env:TEMP "release-metadata-test-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $testRoot -Force | Out-Null

try {
    # --- Scenario 1: first-ever deploy (version A) ---
    Update-ReleaseMetadata -EnvRoot $testRoot -NewZipName "app-A.zip"
    Assert-Equal (Get-CurrentRecordedVersion -EnvRoot $testRoot) "app-A.zip" `
        "First deploy: current_version.txt records A"
    Assert-Null (Get-PreviousRecordedVersion -EnvRoot $testRoot) `
        "First deploy: no previous_version.txt yet (first-release limitation)"

    # --- Scenario 2: a genuinely new release (version B) ---
    Update-ReleaseMetadata -EnvRoot $testRoot -NewZipName "app-B.zip"
    Assert-Equal (Get-CurrentRecordedVersion -EnvRoot $testRoot) "app-B.zip" `
        "Release B: current_version.txt records B"
    Assert-Equal (Get-PreviousRecordedVersion -EnvRoot $testRoot) "app-A.zip" `
        "Release B: previous_version.txt now records A"

    # --- Scenario 3: THE BUG. Recover (redeploy) the SAME version B ---
    # scripts/recover_incident.ps1 does exactly this after an incident:
    # reads current_version.txt (B) and redeploys it unchanged. Before the
    # fix, this call would have copied current_version.txt (B) over
    # previous_version.txt, destroying the record of A.
    Update-ReleaseMetadata -EnvRoot $testRoot -NewZipName "app-B.zip"
    Assert-Equal (Get-CurrentRecordedVersion -EnvRoot $testRoot) "app-B.zip" `
        "Recover B (redeploy same version): current_version.txt still B"
    Assert-Equal (Get-PreviousRecordedVersion -EnvRoot $testRoot) "app-A.zip" `
        "REGRESSION CHECK: recovering B must NOT overwrite A in previous_version.txt"

    # --- Scenario 4: rollback to A (what scripts/rollback.ps1 triggers) ---
    # rollback.ps1 reads previous_version.txt (still correctly A after the
    # fix) and redeploys it via the same deploy.ps1 path, which calls this
    # same function.
    $rollbackTarget = Get-PreviousRecordedVersion -EnvRoot $testRoot
    Assert-Equal $rollbackTarget "app-A.zip" `
        "Rollback target read from previous_version.txt is A"
    Update-ReleaseMetadata -EnvRoot $testRoot -NewZipName $rollbackTarget
    Assert-Equal (Get-CurrentRecordedVersion -EnvRoot $testRoot) "app-A.zip" `
        "After rollback: current_version.txt is A again"
    Assert-Equal (Get-PreviousRecordedVersion -EnvRoot $testRoot) "app-B.zip" `
        "After rollback: previous_version.txt now records B (rollback of a rollback would return to B)"

} finally {
    Remove-Item $testRoot -Recurse -Force -ErrorAction SilentlyContinue
}

Write-Host ""
if ($failures -gt 0) {
    Write-Host "test_release_metadata.ps1: $failures assertion(s) FAILED"
    exit 1
}
Write-Host "test_release_metadata.ps1: all assertions PASSED"
exit 0
