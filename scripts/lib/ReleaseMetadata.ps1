<#
.SYNOPSIS
  Pure, side-effect-isolated helpers for an environment's release
  version-tracking files (current_version.txt / previous_version.txt).

  Extracted out of deploy.ps1 so the rollback-history logic can be
  regression-tested directly -- see tests/test_release_metadata.ps1 --
  without needing a real deployment, process, or port.
#>

function Get-CurrentRecordedVersion {
    param([Parameter(Mandatory = $true)][string]$EnvRoot)
    $versionFile = Join-Path $EnvRoot "current_version.txt"
    if (Test-Path $versionFile) { (Get-Content $versionFile -Raw).Trim() } else { $null }
}

function Get-PreviousRecordedVersion {
    param([Parameter(Mandatory = $true)][string]$EnvRoot)
    $previousVersionFile = Join-Path $EnvRoot "previous_version.txt"
    if (Test-Path $previousVersionFile) { (Get-Content $previousVersionFile -Raw).Trim() } else { $null }
}

function Update-ReleaseMetadata {
    <#
    .SYNOPSIS
      Records $NewZipName as the current release for $EnvRoot, rotating
      previous_version.txt ONLY when the recorded version is actually
      changing.

      Redeploying the SAME version -- which is exactly what
      scripts/recover_incident.ps1 does after an incident -- must not
      overwrite the last DISTINCT known-good version. That was the
      rollback-history bug: the old code copied current_version.txt over
      previous_version.txt unconditionally, so recovering version B
      replaced the saved version A with B, and a later rollback just
      redeployed B again instead of the real previous release.

      Caller contract: only call this AFTER the new deployment has
      passed its readiness check. A failed deployment must never call
      this function, so the release record still reflects the last
      known-good version.
    #>
    param(
        [Parameter(Mandatory = $true)][string]$EnvRoot,
        [Parameter(Mandatory = $true)][string]$NewZipName
    )
    $versionFile = Join-Path $EnvRoot "current_version.txt"
    $previousVersionFile = Join-Path $EnvRoot "previous_version.txt"
    $previouslyRecorded = Get-CurrentRecordedVersion -EnvRoot $EnvRoot

    if ($previouslyRecorded -and $previouslyRecorded -ne $NewZipName) {
        Set-Content -Path $previousVersionFile -Value $previouslyRecorded -Encoding ascii -NoNewline
    }
    Set-Content -Path $versionFile -Value $NewZipName -Encoding ascii -NoNewline
}
