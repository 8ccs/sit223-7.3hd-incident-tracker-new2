<#
.SYNOPSIS
  Configures the real team notification channel for Alertmanager and
  reloads it, without ever putting the webhook URL in source control.

  Writes the URL to monitoring/secrets/slack_webhook_url.txt (gitignored
  -- see monitoring/secrets/slack_webhook_url.example for the tracked
  placeholder) and asks the already-running Alertmanager to reload its
  configuration. Alertmanager re-reads *_file fields on every
  notification send, so this alone is normally enough; the reload call
  also picks up any other config change in monitoring/alertmanager.yml.

  This is a one-time (or as-needed) LOCAL SETUP step, run directly by
  you, not part of the Jenkins pipeline: the monitoring stack is
  continuous, always-on local infrastructure independent of any single
  build (see README.md section 5), not something a Jenkins build
  deploys or redeploys. If you DO want to manage this secret through a
  Jenkins credential instead of typing it here, use
  `withCredentials([string(credentialsId: 'slack-webhook-url', variable: 'SLACK_URL')])`
  in a manual/administrative Jenkins job and call this same script with
  `-SlackWebhookUrl $env:SLACK_URL`.

.PARAMETER SlackWebhookUrl
  A real Slack "Incoming Webhook" URL, e.g.
  https://hooks.slack.com/services/T000/B000/XXXX
  Get one from https://api.slack.com/messaging/webhooks (requires a
  Slack workspace you have permission to add an app/webhook to).

.EXAMPLE
  .\scripts\configure_notifications.ps1 -SlackWebhookUrl "https://hooks.slack.com/services/T000/B000/XXXX"
#>
param(
    [Parameter(Mandatory = $true)][string]$SlackWebhookUrl,
    [string]$AlertmanagerUrl = "http://localhost:9093"
)

$ErrorActionPreference = "Stop"

if ($SlackWebhookUrl -notmatch '^https://hooks\.slack\.com/services/') {
    throw "This does not look like a real Slack incoming-webhook URL (expected it to start with https://hooks.slack.com/services/). Get one from https://api.slack.com/messaging/webhooks and pass it as -SlackWebhookUrl."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$secretsDir = Join-Path $repoRoot "monitoring\secrets"
$secretFile = Join-Path $secretsDir "slack_webhook_url.txt"

New-Item -ItemType Directory -Path $secretsDir -Force | Out-Null
Set-Content -Path $secretFile -Value $SlackWebhookUrl -Encoding ascii -NoNewline
Write-Host "Wrote Slack webhook URL to $secretFile (gitignored -- never commit this file)."

try {
    Invoke-RestMethod -Method Post -Uri "$AlertmanagerUrl/-/reload" -TimeoutSec 5 | Out-Null
    Write-Host "Alertmanager reloaded at $AlertmanagerUrl."
} catch {
    Write-Warning "Could not reach Alertmanager at $AlertmanagerUrl to reload it ($($_.Exception.Message)). Start it first (README.md section 5), or it will pick up the new secret on its own next reload/restart."
}

Write-Host ""
Write-Host "Done. To verify delivery, run scripts\verify_alert_path.py (or trigger the Jenkins Monitoring stage) and check your Slack channel for both a firing and a resolved message."
