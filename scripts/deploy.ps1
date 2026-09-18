<#
.SYNOPSIS
  Deploy (staging) / Release (production) stage.
  Extracts the SAME versioned artifact zip into an environment-isolated
  directory, provisions a venv, writes environment-specific config, stops
  any previously running process for that environment, starts the new
  one, and waits for /health to report ok (readiness check).

  Staging and production never share a directory, port, database file or
  process, so promoting to production cannot overwrite staging (or vice
  versa), and this script never touches any directory outside its own
  environment root.

.PARAMETER Environment
  "staging" or "production".

.PARAMETER ZipPath
  Path to the artifact zip produced by package_artifact.ps1. The SAME
  zip is used for both staging and production releases: Release never
  rebuilds the app.

  Notification destinations (the local alert inbox, and a real Slack
  channel once configured) are NOT set here -- they belong to the
  monitoring stack, which runs continuously and independently of any one
  deploy. See monitoring/alertmanager.yml and scripts/configure_notifications.ps1.
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("staging", "production")][string]$Environment,
    [Parameter(Mandatory = $true)][string]$ZipPath,
    [string]$Root = "C:\devops-demo"
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "lib\ReleaseMetadata.ps1")

$envRoot = Join-Path $Root $Environment
$releasesDir = Join-Path $envRoot "releases"
$currentLink = Join-Path $envRoot "current"
$lockFile = Join-Path $envRoot "deploy.lock"
$dataDir = Join-Path $envRoot "data"
$logsDir = Join-Path $envRoot "logs"
$port = if ($Environment -eq "staging") { 5001 } else { 5000 }

New-Item -ItemType Directory -Path $envRoot, $releasesDir, $dataDir, $logsDir -Force | Out-Null

# --- overlap protection -----------------------------------------------
if (Test-Path $lockFile) {
    $age = (Get-Date) - (Get-Item $lockFile).LastWriteTime
    if ($age.TotalMinutes -lt 15) {
        throw "Another deployment to '$Environment' appears to be in progress (lock file is $([int]$age.TotalMinutes) min old). Aborting to avoid overlapping deployments."
    }
    Write-Warning "Stale lock file found (age $([int]$age.TotalMinutes) min); removing it."
    Remove-Item $lockFile -Force
}
New-Item -ItemType File -Path $lockFile -Force | Out-Null

try {
    if (-not (Test-Path $ZipPath)) { throw "Artifact not found: $ZipPath" }
    $zipName = Split-Path $ZipPath -Leaf
    $versionDir = Join-Path $releasesDir ([System.IO.Path]::GetFileNameWithoutExtension($zipName))

    Write-Host "Deploying $zipName to $Environment (port $port)"

    if (Test-Path $versionDir) { Remove-Item $versionDir -Recurse -Force }
    New-Item -ItemType Directory -Path $versionDir | Out-Null
    Expand-Archive -Path $ZipPath -DestinationPath $versionDir -Force

    # --- venv (created once per environment, reused across releases) ---
    $venvDir = Join-Path $envRoot "venv"
    if (-not (Test-Path $venvDir)) {
        Write-Host "Creating venv for $Environment"
        # "python" is not on PATH for every caller (notably the Jenkins
        # LocalSystem service account, which has no user-level PATH
        # entries), so fall back to the known per-user install if the
        # bare command can't be resolved.
        $systemPython = (Get-Command python -ErrorAction SilentlyContinue).Source
        if (-not $systemPython) {
            $systemPython = "C:\Users\auwal\AppData\Local\Programs\Python\Python311\python.exe"
        }
        & $systemPython -m venv $venvDir
    }
    $pip = Join-Path $venvDir "Scripts\pip.exe"
    $pythonExe = Join-Path $venvDir "Scripts\python.exe"
    & $pip install --quiet --disable-pip-version-check -r (Join-Path $versionDir "requirements.txt")

    # --- environment config (all non-secret; this app has no secrets of ---
    # --- its own -- notification credentials live in the monitoring ---
    # --- stack's own config, not here; see monitoring/alertmanager.yml) ---
    $manifest = Get-Content (Join-Path $versionDir "version.json") | ConvertFrom-Json
    $envFile = Join-Path $envRoot "current.env"
    @"
APP_ENV=$Environment
PORT=$port
DB_PATH=$dataDir\incidents.db
APP_VERSION=$($manifest.full_version)
GIT_COMMIT=$($manifest.git_commit)
"@ | Set-Content -Path $envFile -Encoding utf8

    # --- stop previous process for this environment, if any ---
    $pidFile = Join-Path $envRoot "app.pid"
    if (Test-Path $pidFile) {
        $oldPid = Get-Content $pidFile
        Stop-Process -Id $oldPid -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
    # Safety net: also free the port if something else is bound to it.
    $existing = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $existing) {
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 500
    $stillListening = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($stillListening) {
        # Most likely cause: the previous process was started by Jenkins
        # (LocalSystem) and this caller does not have permission to stop a
        # SYSTEM-owned process. An elevated ("Run as Administrator")
        # window can; Jenkins itself always can, since it IS SYSTEM.
        throw "Port $port is still in use by pid $($stillListening[0].OwningProcess) and it could not be stopped. If that process was deployed by Jenkins, it runs as SYSTEM -- re-run this from an elevated PowerShell window (Run as Administrator), or redeploy via Jenkins itself."
    }

    # --- start new process ---
    $env:APP_ENV = $Environment
    $env:PORT = "$port"
    $env:DB_PATH = "$dataDir\incidents.db"
    $env:APP_VERSION = $manifest.full_version
    $env:GIT_COMMIT = $manifest.git_commit

    $waitress = Join-Path $venvDir "Scripts\waitress-serve.exe"
    # Bound to localhost only: Jenkins, Prometheus, and the browser all run
    # on this same machine for the local demo, so there is no need to
    # expose the app on every network interface (see app/app.py B104 note).
    $arguments = @("--listen=127.0.0.1:$port", "--call", "app.app:create_app")

    # Started via WMI (Win32_Process.Create), NOT Start-Process, and NOT
    # through cmd.exe. Two separate problems ruled out every other option
    # actually tried against the real Jenkins pipeline, not just guessed:
    #
    # 1) .NET's Process.Start only sets bInheritHandles=TRUE when at least
    #    one standard stream is redirected, which then inherits EVERY
    #    inheritable handle open in the caller, not just the redirected
    #    ones. When the caller is itself piped/captured (a Jenkins
    #    pipeline step, or Python's subprocess.run with captured output),
    #    the long-lived server keeps an inherited copy of the CALLER's
    #    own output pipe open forever, so the caller hangs waiting for
    #    end-of-pipe that never comes. Redirecting 2 streams hung a real
    #    build; so did redirecting all 3. Redirecting NONE avoids this.
    #
    # 2) Separately, Jenkins kills every process left over once a build
    #    finishes (measured: gone ~17-30s after "Finished: SUCCESS"),
    #    which defeats the entire point of Deploy/Release. This is NOT
    #    the classic env-var-based ProcessTreeKiller -- setting
    #    BUILD_ID=dontKillMe on the child (Jenkins' own documented
    #    exemption for that mechanism) was tried and made no difference,
    #    so this Jenkins/durable-task combination is using a Windows Job
    #    Object instead: it kills every process still assigned to the
    #    job when the job handle closes, regardless of environment
    #    variables. A process Start-Process creates stays in that job.
    #
    # Win32_Process.Create goes through the WMI provider host
    # (WmiPrvSE.exe), a completely separate process tree with no job or
    # handle relationship to this script or to Jenkins, so it dodges
    # both problems at once -- but only when called directly on the exe,
    # with no redirection and no cmd.exe wrapper (a cmd.exe shim used
    # earlier, purely to get file redirection, reintroduced a hang of
    # its own outside any console session). The trade-off, same as
    # before: waitress's own request/error logging is not captured to a
    # file. The health/readiness check, smoke tests, and Prometheus
    # /metrics scrape are the primary evidence for this project instead
    # (see README troubleshooting section for how to run the app in a
    # foreground terminal for ad-hoc debugging).
    $quotedArgs = ($arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' '
    $commandLine = '"{0}" {1}' -f $waitress, $quotedArgs

    # Win32_Process.Create does NOT inherit this script's $env: variables
    # the way Start-Process does -- the app must be told its environment
    # explicitly via ProcessStartupInformation, or it starts with an
    # empty/default environment (wrong port, wrong DB path, etc).
    [string[]]$envVars = @(
        "APP_ENV=$($env:APP_ENV)",
        "PORT=$($env:PORT)",
        "DB_PATH=$($env:DB_PATH)",
        "APP_VERSION=$($env:APP_VERSION)",
        "GIT_COMMIT=$($env:GIT_COMMIT)",
        "PATH=$($env:PATH)",
        "SystemRoot=$($env:SystemRoot)"
    )
    $startupInfo = New-CimInstance -ClassName Win32_ProcessStartup -Namespace "root/cimv2" -ClientOnly -Property @{
        EnvironmentVariables = $envVars
        ShowWindow           = [uint16]0
    }
    $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine            = $commandLine
        CurrentDirectory       = $versionDir
        ProcessStartupInformation = $startupInfo
    }
    if ($result.ReturnValue -ne 0) {
        throw "Failed to start $Environment process via WMI (Win32_Process.Create returned $($result.ReturnValue))"
    }
    $newPid = $result.ProcessId
    Set-Content -Path $pidFile -Value $newPid -Encoding ascii

    # --- readiness check: poll /health until it reports ok ---
    $healthUrl = "http://localhost:$port/health"
    $ready = $false
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep -Seconds 1
        try {
            $resp = Invoke-RestMethod -Uri $healthUrl -TimeoutSec 3
            if ($resp.status -eq "ok") { $ready = $true; break }
        } catch { }
    }
    if (-not $ready) {
        # Deliberately do NOT touch current_version.txt / previous_version.txt
        # here: a failed deployment must never overwrite the last known-good
        # release record. Whatever was there before this attempt is still
        # accurate -- scripts/rollback.ps1 can still be used against it.
        throw "Deployment to $Environment failed readiness check at $healthUrl after 30s (pid $newPid). Check whether the process is still running (Get-Process -Id $newPid) and whether anything else is bound to port $port. current_version.txt / previous_version.txt were left untouched."
    }

    # --- record release metadata, now that health is confirmed ---
    # See scripts/lib/ReleaseMetadata.ps1 for why this only happens here,
    # after readiness, and only rotates previous_version.txt when the
    # version actually changed. tests/test_release_metadata.ps1 tests
    # this decision directly.
    Update-ReleaseMetadata -EnvRoot $envRoot -NewZipName $zipName

    Write-Host "$Environment is ready: $healthUrl -> ok (pid $newPid, version $($manifest.full_version))"
} finally {
    Remove-Item $lockFile -Force -ErrorAction SilentlyContinue
}
