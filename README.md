# Incident Tracker -- SIT223/SIT753 7.3HD Jenkins DevOps Pipeline

A small incident-tracking web app (Flask + SQLite) used as the subject of a
seven-stage Jenkins pipeline: **Build, Test, Code Quality, Security, Deploy,
Release, Monitoring**.

## 1. Project

CRUD incident tracker with:
- REST API (`/api/incidents`) with create/read/update/delete, status and
  severity filtering, free-text search, and a business rule (an incident
  must be `resolved` or `closed` before it can be deleted).
- A small server-rendered dashboard (`/`) with a filter form.
- `/health` (readiness/liveness) and `/metrics` (Prometheus format)
  endpoints.
- Persistent storage in SQLite, one database file per environment.

**Stack:** Python 3.11, Flask, `prometheus-client`, SQLite (stdlib
`sqlite3`), Waitress (production WSGI server for Windows), pytest,
flake8 + pylint + radon (code quality), Bandit + pip-audit (security),
Prometheus + Alertmanager (monitoring), Jenkins (native Windows service,
declarative pipeline).

### Why no Docker

This machine did not have Docker Desktop installed, and installing it
would need admin rights, a large download, and a restart. Rather than
block the whole pipeline on that, the Build stage produces a versioned
**zip artifact** instead of a container image (the brief explicitly
allows "a JAR file, Docker image, or any other artefact"). Deploy and
Release extract that same zip into isolated per-environment folders with
their own venv, port, database file, and process -- the same separation a
container would give, without needing a container runtime. If Docker is
installed later, the Dockerfile-equivalent step would just replace
`scripts/package_artifact.ps1`; nothing else in the pipeline shape would
need to change.

### Why the deployed app is started via WMI, not Start-Process

Two real, measured problems on this Jenkins/Windows combination shaped
`scripts/deploy.ps1`, in order:

1. **Redirecting any output stream hung a real build.** .NET's
   `Process.Start` sets `bInheritHandles=TRUE` whenever any standard
   stream is redirected, which inherits *every* inheritable handle open
   in the caller -- not just the redirected ones. When the caller is
   itself piped (a Jenkins pipeline step, or Python's
   `subprocess.run(capture_output=True)`), the long-lived server keeps an
   inherited copy of the caller's own output pipe open forever, so the
   caller waits forever for end-of-pipe. This hung Jenkins builds twice
   (2 streams redirected, then all 3 including stdin) before the fix:
   redirect nothing at all. The trade-off is that `waitress`'s own
   request/error log is not captured to a file; `/health`, the smoke
   tests, and Prometheus `/metrics` are the evidence instead.
2. **Jenkins killed the deployed process minutes -- or even seconds --
   after the build finished.** Measured directly: production was gone
   ~17-30s after "Finished: SUCCESS", with nothing in the console log
   explaining why. Setting `BUILD_ID=dontKillMe` (Jenkins' own documented
   exemption for its classic env-var-checking ProcessTreeKiller) made no
   difference, which points at a Windows Job Object instead: everything
   still assigned to the build's job gets killed when the job handle
   closes, regardless of environment variables. The fix: launch via
   `Win32_Process.Create` (WMI), which goes through the WMI provider host
   (`WmiPrvSE.exe`) -- a separate process tree with no job or handle
   relationship to Jenkins at all. Verified across three separate builds
   that the app is still running and healthy minutes after the pipeline
   finished.

Both fixes and their evidence are in the comment above the
`Win32_Process.Create` call in `scripts/deploy.ps1`, and in the git
history (`git log --oneline`) for anyone who wants the blow-by-blow.

## 2. Repository layout

```
app/                Flask application (factory in app.py, routes in routes.py)
templates/, static/ Dashboard HTML/CSS
tests/unit/          Pure-logic unit tests (validation, SQLite store)
tests/integration/   Flask test-client API tests (incl. failure cases)
scripts/              PowerShell + Python automation used by the Jenkinsfile
monitoring/           Prometheus, Alertmanager, and the local alert-inbox app
config/                Example (non-secret) environment files
Jenkinsfile
requirements.txt       Runtime dependencies (shipped in the artifact)
requirements-dev.txt    + test/quality/security tooling (CI only)
```

## 3. Prerequisites (this machine: Windows 11, PowerShell 5.1 + Git Bash)

Already present and used as-is:
- **Python 3.11** (`python --version`)
- **Git** (`git --version`)
- **Jenkins 2.568.2**, running as a Windows service on `http://localhost:8080`
  (`Get-Service Jenkins`), with security enabled.
- **GitHub CLI** (`gh`), already authenticated.

Installed for this project (see section 5):
- **Prometheus** and **Alertmanager** (native Windows binaries, no admin
  rights needed -- unzipped into `C:\devops-demo\tools`).

Not installed / not used: Docker Desktop, SonarQube. Code quality uses
flake8 + pylint + radon (all pure Python, no server to run) as a
justified equivalent to SonarQube for this project's size -- see the
"Code Quality" section below for why.

## 4. First-time setup

From a fresh clone:

```powershell
git clone https://github.com/8ccs/sit223-7.3hd-incident-tracker-new2.git incident-tracker
cd incident-tracker

python -m venv .venv
.\.venv\Scripts\pip.exe install --disable-pip-version-check -r requirements-dev.txt

# Run the test suite locally
.\.venv\Scripts\pytest.exe tests\unit tests\integration --cov=app --cov-report=term-missing

# Run the app locally (dev server, NOT what Jenkins deploys)
$env:APP_ENV="dev"; $env:PORT="5000"; $env:DB_PATH="data\dev.db"
.\.venv\Scripts\python.exe -m app.app
# then open http://127.0.0.1:5000
```

Everything above only touches the git working copy. Nothing is installed
system-wide by these commands.

## 5. Monitoring stack (Prometheus + Alertmanager + local alert inbox)

These run continuously as **local infrastructure**, independent of any
one Jenkins build (the same way a real Prometheus server would). Jenkins'
Monitoring stage *verifies and exercises* them; it does not start them
from scratch on every run.

```powershell
# One-time download (Windows amd64 binaries, official releases):
New-Item -ItemType Directory -Force -Path C:\devops-demo\tools | Out-Null
Invoke-WebRequest https://github.com/prometheus/prometheus/releases/download/v3.14.0/prometheus-3.14.0.windows-amd64.zip -OutFile C:\devops-demo\tools\prometheus.zip
Invoke-WebRequest https://github.com/prometheus/alertmanager/releases/download/v0.34.1/alertmanager-0.34.1.windows-amd64.zip -OutFile C:\devops-demo\tools\alertmanager.zip
Expand-Archive C:\devops-demo\tools\prometheus.zip -DestinationPath C:\devops-demo\tools -Force
Expand-Archive C:\devops-demo\tools\alertmanager.zip -DestinationPath C:\devops-demo\tools -Force

# Start the local alert inbox (dev stand-in for a real Slack/Teams webhook --
# see section "Real team notifications" below to point Alertmanager at a
# real channel in ADDITION to this one)
.\.venv\Scripts\pip.exe install flask
Start-Process -FilePath .\.venv\Scripts\python.exe -ArgumentList "monitoring\webhook_receiver.py" -WindowStyle Hidden

# Start Alertmanager (run from monitoring\ so its relative api_url_file
# path for the Slack secret, and the local alert inbox, both resolve)
Start-Process -FilePath C:\devops-demo\tools\alertmanager-0.34.1.windows-amd64\alertmanager.exe `
  -ArgumentList "--config.file=$PWD\monitoring\alertmanager.yml" -WorkingDirectory "$PWD\monitoring" -WindowStyle Hidden

# Start Prometheus (run from the repo so its relative rule_files path resolves)
Start-Process -FilePath C:\devops-demo\tools\prometheus-3.14.0.windows-amd64\prometheus.exe `
  -ArgumentList "--config.file=$PWD\monitoring\prometheus.yml" -WorkingDirectory "$PWD\monitoring" -WindowStyle Hidden
```

Check they are up:
- Prometheus UI: http://localhost:9090 (Status > Targets should show
  `incident-tracker-production` and `incident-tracker-staging`; they read
  `down` until something is deployed to those ports -- see section 6).
- Alertmanager UI: http://localhost:9093
- Local alert inbox: http://localhost:9099/alerts

### 5.1 Real team notifications (Slack)

`monitoring/alertmanager.yml`'s one receiver sends every notification to
**both** destinations at once:

- the local alert inbox above (kept permanently -- it is what
  `scripts/verify_alert_path.py` and the Jenkins Monitoring stage check
  on every run, so there is always a fast, offline way to prove delivery
  actually happened);
- a real Slack channel, via Alertmanager's own native `slack_configs`
  receiver type. This is *not* a generic webhook pointed at a Slack URL
  -- Slack's Incoming Webhooks expect Slack's own JSON payload shape,
  which only `slack_configs` builds correctly (see
  https://prometheus.io/docs/alerting/latest/configuration/#slack_config).

**Status: configured and verified.** A real Slack Incoming Webhook is
connected to the `#incident-tracker-alerts` channel. A controlled
incident-and-recovery run confirmed both a firing and a resolved
message actually arrived in that channel (not just the local inbox):
Alertmanager's own `/metrics` endpoint recorded exactly 2
`alertmanager_notifications_total{integration="slack"}` sends with 0
new failures immediately after the webhook was configured, and the two
messages (red "firing" colour, then green "resolved" colour) were
confirmed visible in the channel directly. The webhook URL itself was
never displayed, logged, or committed at any point in that process.

The webhook URL is a secret and is **never committed**. To connect (or
reconnect, e.g. on a different machine) a real Slack channel you
control:

1. Create a Slack **Incoming Webhook** for a channel you have permission
   to post to: https://api.slack.com/messaging/webhooks (needs a Slack
   workspace).
2. Run:
   ```powershell
   .\scripts\configure_notifications.ps1 -SlackWebhookUrl "https://hooks.slack.com/services/T000/B000/XXXX"
   ```
   This writes the URL to `monitoring/secrets/slack_webhook_url.txt`
   (gitignored -- see `monitoring/secrets/slack_webhook_url.example` for
   the tracked placeholder) and reloads the running Alertmanager.
3. Verify both destinations receive both a firing and a resolved
   message: `.\.venv\Scripts\python.exe scripts\verify_alert_path.py`,
   then check the Slack channel (the script itself only asserts against
   the local inbox, on purpose -- see the comment at the top of that
   file).

While `monitoring/secrets/slack_webhook_url.txt` holds a placeholder
(e.g. on a fresh clone before step 2), Alertmanager treats every
configured integration in a receiver independently, so the Slack
attempt fails (logged in Alertmanager's own console output; check
`C:\devops-demo\logs\alertmanager.err.log`) without affecting the local
inbox delivery at all -- that is what made it safe to develop and test
this pipeline before a real channel was connected.

## 6. Jenkins setup

### 6.1 Plugins

Jenkins already has git, github, github-branch-source, workflow-aggregator
(Pipeline), junit, credentials, and timestamper installed. No extra
plugin is required for this pipeline: coverage/quality/security reports
are archived as workspace artifacts rather than rendered through a
dedicated plugin, to keep the plugin footprint (and thing that can break)
small. If you want in-Jenkins HTML rendering of the coverage/quality
reports, install the **HTML Publisher** plugin (Manage Jenkins > Plugins)
and add an `publishHTML` post step pointing at `reports/test/htmlcov`.

### 6.2 Create the pipeline job

1. Jenkins > New Item > name it `SIT223-7.3HD-IncidentTracker` > **Pipeline** > OK.
2. Build Triggers > check **"Poll SCM"**, schedule `H/5 * * * *`.
   (Why polling and not a webhook: this Jenkins has no public URL, so
   GitHub cannot reach it directly. SCM polling is the practical
   automatic trigger for a local instance; see the comment at the top of
   `Jenkinsfile`.)
3. Pipeline > Definition: **Pipeline script from SCM**.
   - SCM: Git
   - Repository URL: `https://github.com/8ccs/sit223-7.3hd-incident-tracker-new2.git`
   - Branch: `*/main`
   - Script Path: `Jenkinsfile`
4. Save.

### 6.3 Credentials (none hardcoded)

This app has no secrets of its own -- `current.env` (generated by
`scripts/deploy.ps1`, never committed) only ever holds non-secret values
like the port and database path.

The one real secret in this project is the Slack notification webhook
URL, and it belongs to the **monitoring stack**, not to a Jenkins build:
Prometheus/Alertmanager run continuously as local infrastructure,
independent of any single Jenkins run (section 5), so the secret is
configured once, locally, the same way -- see section 5.1 "Real team
notifications" for the exact steps
(`scripts\configure_notifications.ps1`). It is stored at
`monitoring/secrets/slack_webhook_url.txt`, which `.gitignore` excludes;
only `monitoring/secrets/slack_webhook_url.example` (a placeholder) is
committed.

If you would rather manage that secret through Jenkins' own credential
store instead of typing it locally, add it there as a **Secret text**
credential and call the same script from a small manual/administrative
Jenkins job:
```groovy
withCredentials([string(credentialsId: 'slack-webhook-url', variable: 'SLACK_URL')]) {
    powershell '''
        & .\\scripts\\configure_notifications.ps1 -SlackWebhookUrl $env:SLACK_URL
    '''
}
```
This is optional and separate from the main pipeline job -- the
Build/Test/.../Monitoring pipeline itself never needs this credential,
since it only talks to the already-configured Alertmanager, not to
Slack directly.

### 6.4 Run it

Build Now. Watch **Stage View** for the seven stages. Console Output
shows every real command. On success:
- Staging: http://localhost:5001
- Production: http://localhost:5000
- Reports: build page > "Artifacts" (`reports/test`, `reports/quality`,
  `reports/security`, `reports/deploy`, `reports/release`,
  `reports/monitoring`, `dist/`).

## 7. Running tests, quality, and security scans directly (outside Jenkins)

```powershell
# Tests + coverage
.\.venv\Scripts\pytest.exe tests\unit tests\integration --cov=app --cov-report=term-missing --cov-fail-under=80

# Code quality
.\.venv\Scripts\flake8.exe app
.\.venv\Scripts\pylint.exe app --rcfile=.pylintrc
.\.venv\Scripts\radon.exe cc app -s
python scripts\quality_gate.py reports\quality   # after generating the JSON/score files, see Jenkinsfile

# Security
.\.venv\Scripts\bandit.exe -r app -f txt
.\.venv\Scripts\pip-audit.exe -r requirements.txt -f columns
python scripts\security_gate.py reports\security
```

### 7.1 Security findings

**The actual gate policy** (`scripts/security_gate.py`), verified
against the code, not assumed:
- A Bandit finding blocks the build only if its severity is **HIGH**
  *and* its confidence is **MEDIUM or HIGH**. Bandit findings below that
  (including every MEDIUM-severity finding, which is what both checks
  below are) are reported but do not fail the build on their own.
- A pip-audit finding blocks the build only if a **fixed version is
  already available** for that dependency. A known vulnerability with
  no fix released yet is reported but does not block, since there is
  nothing actionable to upgrade to.
- Independently of severity: a report that is missing, unreadable,
  empty, malformed, or reflects a scan that never actually completed
  (a Bandit analysis error, zero lines of code analysed, zero
  dependencies listed) always fails the gate, the same as a missing
  report. This was a real bug in an earlier version of this gate --
  such reports used to look identical to "scanned cleanly, found
  nothing" -- found and fixed during this review, and covered by
  focused unit tests (`tests/unit/test_security_gate.py`).

**Real findings from this codebase**, and why each is below the
blocking threshold or already fixed:

| Finding | Bandit severity/confidence | Location | Status | Explanation |
|---|---|---|---|---|
| B104: hardcoded bind to all interfaces (`0.0.0.0`) | MEDIUM / MEDIUM (Bandit's own fixed classification for this check -- confirmed in Bandit's source, `bandit/plugins/general_bind_all_interfaces.py`) | app startup config | Fixed | Never reached the gate's HIGH-only blocking threshold, but was still fixed as good practice: binding to all interfaces was unnecessary since every environment in this project runs on the same machine. Changed to bind `127.0.0.1` only. |
| B608: possible SQL injection via string-built query | MEDIUM / MEDIUM (`bandit/plugins/injection_sql.py`) | `app/models.py`, `list()` query (search/filter) | Fixed | Also below the blocking threshold, fixed anyway: the filter query originally built its `WHERE` clause with string formatting. Rewritten to fixed SQL text with `?` parameter placeholders for every value; no user input is ever concatenated into the query text. |
| B608: possible SQL injection via string-built query | MEDIUM / MEDIUM | `app/models.py:129`, `update()` | Accepted with mitigation, documented `# nosec B608` | Below the blocking threshold either way, and additionally mitigated rather than left as a bare suppression: `update()` needs a dynamic column list, because only the fields the caller sent should change. Bandit cannot see that the column *names* in the `SET ...` clause come only from `_UPDATABLE_FIELDS`, a fixed five-name allow-list checked in code (`unknown = set(fields) - self._UPDATABLE_FIELDS`, which raises before the query ever runs if an unrecognised field is present) -- never from raw request data. Every *value* is still sent as a `?` parameter, never concatenated. |
| pip-audit: dependency vulnerabilities | -- | 9 runtime dependencies (`requirements.txt`) | None found | A completed scan of all 9 dependencies found zero known CVEs. This is reported as a genuine clean result, not assumed, because the gate first confirms the scan actually produced a non-empty dependency list before treating an empty findings list as "clean" rather than "skipped". |

Bandit also scans for the common Flask `debug=True` misconfiguration and
several other checks; none triggered in this codebase.

## 8. Deploying both environments and demonstrating an incident

```powershell
# Build an artifact by hand (Jenkins normally does this):
.\.venv\Scripts\python.exe -m venv .venv   # if not already created
& .\scripts\package_artifact.ps1 -Version 1.0.0 -GitCommit (git rev-parse --short HEAD) -BuildNumber manual

# Deploy to staging, then production (same artifact, no rebuild):
& .\scripts\deploy.ps1 -Environment staging    -ZipPath (Get-Content dist\artifact-path.txt)
& .\scripts\deploy.ps1 -Environment production -ZipPath (Get-Content dist\artifact-path.txt)

# Smoke test either one:
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://localhost:5001
.\.venv\Scripts\python.exe scripts\smoke_test.py --base-url http://localhost:5000

# Full automated incident-and-recovery check (what the Monitoring stage runs):
.\.venv\Scripts\python.exe scripts\verify_alert_path.py

# Or do it by hand, to narrate live in the demo:
& .\scripts\simulate_incident.ps1   # stops the production process
# watch http://localhost:9090/alerts -- AppDown goes pending -> firing
# watch http://localhost:9099/alerts -- a "firing" entry appears
& .\scripts\recover_incident.ps1    # restarts production on the same version
# watch both pages again -- alert clears, a "resolved" entry appears

# Rollback production to the previous released artifact (no rebuild):
& .\scripts\rollback.ps1 -Environment production
# or, from Jenkins: Build with Parameters > check ROLLBACK_PRODUCTION > Build
```

## 9. Shutdown / restart

```powershell
# Stop the app processes for an environment:
Get-Content C:\devops-demo\staging\app.pid, C:\devops-demo\production\app.pid |
  ForEach-Object { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue }

# Stop monitoring stack: find and stop prometheus.exe, alertmanager.exe,
# and the webhook_receiver.py python process (Task Manager, or
# Get-Process prometheus,alertmanager | Stop-Process).

# To start again, re-run the "Start-Process" commands in section 5, then
# re-deploy staging/production as in section 8 (or just re-run the
# Jenkins job).
```

Data is not lost across restarts: each environment's SQLite file lives
under `C:\devops-demo\<env>\data\incidents.db` and is only touched by
that environment's own process.

## 10. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Invoke-RestMethod : Unable to connect` during Deploy | App process failed to start or is still starting | `deploy.ps1` deliberately does not redirect the app's own stdout/stderr to a log file (see the comment above the `Start-Process` call in `scripts/deploy.ps1` for why -- redirecting any stream there previously hung a real Jenkins build). To see the app's own output while debugging, run it directly in a foreground terminal: `cd C:\devops-demo\<env>\releases\<version>; & C:\devops-demo\<env>\venv\Scripts\waitress-serve.exe --listen=127.0.0.1:<port> --call app.app:create_app`. Common cause of the failure itself: port already in use by a leftover process from a previous failed run -- `deploy.ps1` already tries to free the port; if it still fails, find and stop the process manually: `Get-NetTCPConnection -LocalPort 5000` then `Stop-Process`. |
| `deploy.ps1` / `simulate_incident.ps1` says a process "could not be stopped" | The process was deployed by Jenkins, which runs as the `LocalSystem` service account, so it is SYSTEM-owned | Re-run the script from an elevated PowerShell window (right-click PowerShell > Run as Administrator) -- your Windows account is a local Administrator, so this works once elevated. Or just redeploy/re-run the incident scripts through Jenkins itself, since Jenkins (also SYSTEM) always has permission to stop its own processes. |
| Monitoring stage fails at "preflight" | Prometheus/Alertmanager/webhook inbox not running | Start them (section 5) before running the pipeline; they are long-running infrastructure, not something Jenkins starts. |
| `pytest` can't import `app` | Running pytest from the wrong directory, or `pytest.ini`'s `pythonpath = .` missing | Always run pytest from the repo root; `pytest.ini` already sets `pythonpath = .`. |
| `python app\app.py` fails with `ModuleNotFoundError: No module named 'app.metrics'` | Running the file directly puts `app\` (not the repo root) on `sys.path` | Run `python -m app.app` from the repo root instead (see section 4). |
| Jenkins job stuck "waiting for next available executor" | Another build already running (`disableConcurrentBuilds()`) or agent busy | Wait for the current build, or check Jenkins > Manage Jenkins > Nodes. |
| Quality/Security gate fails unexpectedly | A real new finding, or a tool version drifted | Read `reports/quality/pylint.txt` or `reports/security/bandit.txt` in the build's archived artifacts -- the gate scripts print exactly which check failed and why. |

## 11. Credential setup summary (nothing secret is committed)

| Secret | Where it lives | How it reaches the app |
|---|---|---|
| Slack Incoming Webhook URL (optional, real team channel) | `monitoring/secrets/slack_webhook_url.txt` (gitignored, local file), set once via `scripts\configure_notifications.ps1 -SlackWebhookUrl "..."` -- see section 5.1 | Read directly by Alertmanager (`api_url_file` in `monitoring/alertmanager.yml`); re-read on every notification, so a reload/restart is not needed after changing it. It never passes through Jenkins, `current.env`, or the application process at all -- alerting is local infrastructure, independent of any one build. |

No database password, API key, or token is required for this project
(SQLite is a local file, GitHub access uses the existing `gh` CLI login,
Jenkins access uses your existing Jenkins account). An earlier version of
this project also threaded an `ALERT_WEBHOOK_URL` value through
`scripts/deploy.ps1` into each environment's `current.env` file; that
parameter was dead code (the app never read it) and was removed once the
real Slack integration above replaced it.

## 12. Access checklist (for submission)

The assignment brief requires **both** the Marker and the Unit Chair to
be able to view the codebase, even though the provided answer-sheet
template only mentions the "marking tutor". This repository is public
and anonymous read access was verified directly (an unauthenticated
request to both the repository page and a raw file returned HTTP 200),
so both roles can view it without needing an individual invite. If you
ever switch it to private, add both the Marker's and the Unit Chair's
GitHub usernames as collaborators (Settings > Collaborators) -- this
cannot be completed by an assistant without those usernames, and must
be checked off by hand before submission.

## 13. Handover: what's done, what's pending

**Done and verified:**
- All 7 required stages pass on this repository -- see "Tested Jenkins
  build" below for the exact build number, commit, and evidence
  location once the pipeline has run here.
- 78 unit and integration tests pass, 98% coverage (re-confirmed
  locally: `pytest tests/unit tests/integration --cov=app`).
- The security gate's actual policy (HIGH-severity/MEDIUM-or-higher-
  confidence Bandit findings, and pip-audit findings with an available
  fix) is verified against the code itself, not assumed -- see
  section 7.1.
- Rollback correctly preserves the last known-good release instead of
  overwriting it when recovering the same version -- re-confirmed with
  the regression test (`tests/scripts/test_release_metadata.ps1`, 9/9
  assertions pass) and, earlier, a real
  deploy-release-incident-recover-rollback sequence against the actual
  production environment.
- The Monitoring stage's incident check always attempts recovery, even
  if the check itself fails partway through, and still fails the build
  if verification failed even though recovery succeeded -- re-confirmed
  with a fresh clean run and a fresh deliberately-broken run (production
  was stopped, the alert-name check was made to fail on purpose,
  recovery still restored production to its running version, and the
  final result still reported FAILED).
- Alertmanager is wired to a real Slack receiver (`slack_configs`, not a
  generic webhook), alongside the local inbox the automated Jenkins
  check always relies on -- see section 5.1. Real delivery is now
  verified: a controlled incident-and-recovery run produced a firing
  and a resolved message that both actually arrived in the
  `#incident-tracker-alerts` Slack channel, confirmed both via
  Alertmanager's own notification-count metrics and by checking the
  channel directly.
- Marker/Unit Chair access -- see section 12; the repository is public
  and anonymous read access is verified.

**Tested Jenkins build:** #21, commit `174be0e`, version
`1.0.0+build.21.174be0e` -- all 7 stages passed (78/78 tests, 98%
coverage, pylint 9.96/10, security gate passed with 0 findings and 9/9
dependencies clean, 5/5 smoke tests on both staging and production, and
the full Monitoring alert path -- firing and resolved -- verified
within this same run). See the answer sheet for the Stage View
screenshot. Later documentation-only commits (after this one) do not
change the tested application or pipeline code, so this citation
remains accurate even if `git log` shows a newer HEAD.

**Pending -- needs your input, not something an assistant can complete:**
1. **Recording and uploading the demo video**, following your own
   recording plan. Once you have a file: check its duration is under
   10:00 and audio is audible throughout, upload it (e.g. YouTube
   unlisted), and open the link in a private/incognito window to
   confirm it plays without your personal login.
2. **Inserting the real video link** into answer-sheet item 1 (currently
   `[PENDING - VIDEO LINK]`), then re-exporting the DOCX to PDF.

Until items 1-2 above are done, this package is not submission-ready,
even though the pipeline, codebase, and real Slack notifications are
all complete and verified.
