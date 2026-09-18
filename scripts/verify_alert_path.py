#!/usr/bin/env python
"""Monitoring stage: automated, end-to-end verification of the alert path.

This does not just check that Prometheus/Alertmanager are configured; it
actually breaks the production app, waits for Prometheus to notice, waits
for the AppDown alert rule to fire, confirms the local webhook inbox
received a "firing" notification, restores the app, and confirms both
Prometheus and the inbox see the alert as resolved.

Every step is verified over HTTP against the real running services
(Prometheus :9090, Alertmanager :9093, webhook inbox :9099). If any step
does not happen within its timeout, this script exits non-zero and the
Monitoring stage fails -- a configured-but-never-fired alert is treated
as a failure, not a pass.

Usage: python scripts/verify_alert_path.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

PS1_LOG_DIR = Path("reports/monitoring/ps1-logs")

# This script is scoped, on purpose, to ONLY this project's own local demo
# production target on localhost -- it never touches any other host, port,
# or service. See simulate_incident.ps1 / recover_incident.ps1 for the
# same scoping at the process-management level.
PROM_URL = "http://localhost:9090"
ALERTMANAGER_URL = "http://localhost:9093"
INBOX_URL = "http://localhost:9099"
PROD_METRICS_URL = "http://localhost:5000/metrics"
PROD_HEALTH_URL = "http://localhost:5000/health"
# Overridable only so this script's own guaranteed-recovery behaviour can
# be regression-tested by deliberately pointing it at an alert name that
# will never fire (see tests/scripts/test_verify_alert_path_recovery.py).
# Real runs always use the default.
ALERT_NAME = os.environ.get("VERIFY_ALERT_NAME", "AppDown")

SCRIPTS_DIR = Path(__file__).parent
REPORT_PATH = Path("reports/monitoring/alert-path-verification.json")

timeline: dict[str, str] = {}


def log(msg: str) -> None:
    print(f"[verify-alert-path] {msg}", flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_ps1(name: str, timeout_s: int = 90) -> None:
    """Run a PowerShell script and wait for it to finish.

    Output is redirected to FILES rather than captured with pipes,
    because deploy.ps1 launches a detached waitress process with
    Start-Process; on Windows that grandchild can inherit and hold open
    the parent's stdout/stderr PIPE handles even after the parent script
    exits, which makes subprocess.run(capture_output=True) hang forever
    waiting for end-of-pipe that never comes.

    The same inheritance means the detached waitress process can also
    keep a lock on the log FILE itself after deploy.ps1 exits, so reading
    it back for the console is best-effort and never fatal -- only the
    process exit code decides pass/fail. ``timeout_s`` is a second line
    of defence in case anything else hangs.
    """
    script = SCRIPTS_DIR / name
    PS1_LOG_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PS1_LOG_DIR / f"{name}.stdout.log"
    err_path = PS1_LOG_DIR / f"{name}.stderr.log"
    with out_path.open("w") as out_f, err_path.open("w") as err_f:
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                stdout=out_f,
                stderr=err_f,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"{name} did not finish within {timeout_s}s (still running detached "
                "processes it started, e.g. waitress, are not affected by this timeout)"
            ) from exc

    try:
        print(out_path.read_text(errors="replace"))
    except OSError as exc:
        print(f"(could not read {out_path} for console echo: {exc})")

    if result.returncode != 0:
        try:
            print(err_path.read_text(errors="replace"), file=sys.stderr)
        except OSError as exc:
            print(f"(could not read {err_path} for console echo: {exc})", file=sys.stderr)
        raise RuntimeError(f"{name} failed with exit code {result.returncode}")


def wait_for(predicate, timeout_s: int, interval_s: float, description: str):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except requests.RequestException:
            pass
        time.sleep(interval_s)
    raise TimeoutError(f"Timed out after {timeout_s}s waiting for: {description}")


def prometheus_target_health(job: str) -> str | None:
    resp = requests.get(f"{PROM_URL}/api/v1/targets", timeout=5)
    resp.raise_for_status()
    for target in resp.json()["data"]["activeTargets"]:
        if target["labels"].get("job") == job:
            return target["health"]
    return None


def alert_is_firing(alertname: str) -> bool:
    resp = requests.get(f"{PROM_URL}/api/v1/alerts", timeout=5)
    resp.raise_for_status()
    for alert in resp.json()["data"]["alerts"]:
        if alert["labels"].get("alertname") == alertname and alert["state"] == "firing":
            return True
    return False


def alert_is_gone(alertname: str) -> bool:
    resp = requests.get(f"{PROM_URL}/api/v1/alerts", timeout=5)
    resp.raise_for_status()
    return not any(
        a["labels"].get("alertname") == alertname for a in resp.json()["data"]["alerts"]
    )


def inbox_has_status(alertname: str, status: str, after_iso: str) -> bool:
    resp = requests.get(f"{INBOX_URL}/alerts.json", timeout=5)
    resp.raise_for_status()
    for entry in resp.json():
        if (
            entry.get("alertname") == alertname
            and entry.get("status") == status
            and entry.get("received_at", "") >= after_iso
        ):
            return True
    return False


def preflight() -> None:
    log("Checking Prometheus, Alertmanager, and the local webhook inbox are reachable...")
    requests.get(f"{PROM_URL}/-/healthy", timeout=5).raise_for_status()
    requests.get(f"{ALERTMANAGER_URL}/-/healthy", timeout=5).raise_for_status()
    requests.get(f"{INBOX_URL}/health", timeout=5).raise_for_status()
    requests.get(PROD_METRICS_URL, timeout=5).raise_for_status()
    log("All monitoring components reachable.")


def main() -> int:  # noqa: PLR0915 - linear step-by-step script reads clearer flat
    # Tracked across the whole function so the mandatory cleanup in
    # `finally` knows whether there is anything to recover from, and so
    # the original failure and any separate recovery failure can be
    # reported (and reasoned about) independently. A failure verifying
    # the fault-injection path must fail this script's exit code even if
    # cleanup afterwards succeeds -- recovering the demo environment is
    # not the same thing as the monitoring behaviour actually working.
    incident_introduced = False
    original_error: Exception | None = None
    recovery_error: Exception | None = None
    t0: str | None = None

    try:
        preflight()

        timeline["baseline_at"] = now()
        health = prometheus_target_health("incident-tracker-production")
        if health != "up":
            raise RuntimeError(
                f"production target is not healthy before the test (health={health}); "
                "fix the deployment before verifying alerting"
            )
        log("Baseline: production target is up.")

        log("Step 1/5: introducing a reversible incident (stopping the production process)...")
        t0 = now()
        timeline["incident_introduced_at"] = t0
        run_ps1("simulate_incident.ps1")
        # From this point on, the app is deliberately broken. The `finally`
        # block below is now REQUIRED to attempt recovery, no matter what
        # happens in the checks below -- a timeout waiting for the alert to
        # fire must not leave production down.
        incident_introduced = True

        log("Step 2/5: waiting for Prometheus to mark the target down...")
        wait_for(
            lambda: prometheus_target_health("incident-tracker-production") == "down",
            timeout_s=30, interval_s=2,
            description="production target health == down",
        )
        timeline["target_down_detected_at"] = now()
        log("Target confirmed down.")

        log("Step 3/5: waiting for the AppDown alert rule to fire...")
        wait_for(
            lambda: alert_is_firing(ALERT_NAME),
            timeout_s=60, interval_s=2,
            description=f"alert {ALERT_NAME} state == firing",
        )
        timeline["alert_fired_at"] = now()
        log("Alert is firing in Prometheus.")

        log("Step 4/5: waiting for the local webhook inbox to receive the firing notification...")
        wait_for(
            lambda: inbox_has_status(ALERT_NAME, "firing", t0),
            timeout_s=60, interval_s=2,
            description="webhook inbox received a firing notification",
        )
        timeline["notification_received_at"] = now()
        log("Notification received by webhook inbox.")

        timeline["fault_injection_result"] = "PASSED"
        log("Fault-injection verification PASSED (issue introduced, detected, alerted, notified).")

    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed; re-raised below
        original_error = exc
        timeline["fault_injection_result"] = "FAILED"
        timeline["error"] = str(exc)
        log(f"Fault-injection verification FAILED: {exc}")

    finally:
        # GUARANTEED cleanup: once the incident was introduced, always try
        # to restore the app, whether the checks above passed, failed, or
        # raised. This runs even if the try block above threw partway
        # through -- that is the entire point of putting it in `finally`.
        if incident_introduced:
            try:
                log("Step 5/5: recovering the production process (always attempted)...")
                t_recover = now()
                timeline["recovery_started_at"] = t_recover
                run_ps1("recover_incident.ps1")

                wait_for(
                    lambda: prometheus_target_health("incident-tracker-production") == "up",
                    timeout_s=30, interval_s=2,
                    description="production target health == up again",
                )
                timeline["target_up_detected_at"] = now()
                log("Target confirmed up again.")

                # Confirm the app is not just "a" process, but running the
                # INTENDED version -- recover_incident.ps1 always redeploys
                # whatever current_version.txt already said, never a
                # rebuild, so this must be the same version that was
                # running before the incident.
                resp = requests.get(PROD_HEALTH_URL, timeout=5)
                resp.raise_for_status()
                body = resp.json()
                if body.get("status") != "ok":
                    raise RuntimeError(f"post-recovery health check did not report ok: {body}")
                timeline["post_recovery_version"] = body.get("version")
                log(f"Post-recovery health check ok, running version {body.get('version')}.")

                # The alert-cleared / resolved-notification evidence is
                # only meaningful when the fault-injection path above
                # actually got as far as a real firing alert to resolve.
                # If it failed earlier (e.g. the alert never fired), there
                # is nothing to clear, so do not chase that here -- the
                # service being back up and healthy is the recovery
                # contract; the FAILED verdict from fault-injection still
                # stands regardless.
                if original_error is None:
                    wait_for(
                        lambda: alert_is_gone(ALERT_NAME),
                        timeout_s=60, interval_s=2,
                        description=f"alert {ALERT_NAME} cleared in Prometheus",
                    )
                    timeline["alert_cleared_at"] = now()
                    log("Alert cleared in Prometheus.")

                    wait_for(
                        lambda: inbox_has_status(ALERT_NAME, "resolved", t_recover),
                        timeout_s=60, interval_s=2,
                        description="webhook inbox received a resolved notification",
                    )
                    timeline["resolved_notification_received_at"] = now()
                    log("Resolved notification received by webhook inbox.")

                timeline["recovery_result"] = "PASSED"

            except Exception as exc:  # noqa: BLE001 - recorded separately from original_error
                recovery_error = exc
                timeline["recovery_result"] = "FAILED"
                timeline["recovery_error"] = str(exc)
                log(f"RECOVERY FAILED: {exc}")

    # --- final verdict: report both failures if both happened, and never ---
    # --- let a successful cleanup paper over a real verification failure ---
    # `incident_introduced` matters here too: if simulate_incident.ps1
    # itself failed (e.g. permission denied stopping a SYSTEM-owned
    # process), the app was never actually broken, so there is nothing
    # to recover from and recovery is correctly never attempted --
    # that must not be reported as "recovery succeeded".
    if original_error is not None and recovery_error is not None:
        timeline["result"] = "FAILED"
        log(f"RESULT: FAILED -- original error: {original_error}; recovery ALSO failed: {recovery_error}")
    elif original_error is not None and not incident_introduced:
        timeline["result"] = "FAILED"
        log(f"RESULT: FAILED -- {original_error}. The incident was never actually introduced, "
            "so no recovery was needed or attempted; production was not touched.")
    elif original_error is not None:
        timeline["result"] = "FAILED"
        log(f"RESULT: FAILED (fault-injection verification) -- {original_error}. "
            "Recovery succeeded; production should be running again on its intended version.")
    elif recovery_error is not None:
        timeline["result"] = "FAILED"
        log(f"RESULT: FAILED (recovery, after fault-injection verification otherwise passed) -- {recovery_error}")
    else:
        timeline["result"] = "PASSED"

    timeline["incident_introduced"] = incident_introduced
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(timeline, indent=2), encoding="utf-8")

    if timeline["result"] != "PASSED":
        return 1

    log("Full alert path verified: issue -> rule fired -> notification received -> recovered -> resolved.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
