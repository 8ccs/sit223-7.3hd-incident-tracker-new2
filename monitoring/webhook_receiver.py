"""Local webhook inbox for Alertmanager notifications.

This is a LOCAL DEVELOPMENT STAND-IN for a real team notification channel
(e.g. a Slack/Discord/Teams incoming webhook). It exists so the full alert
path -- rule fires, Alertmanager sends, notification is received -- can be
demonstrated without needing a paid or externally-hosted service. Every
alert it receives is appended to alerts_received.log as one JSON object
per line, and shown at /alerts (human-readable) and /alerts.json
(machine-readable, used by scripts/verify_alert_path.py).

To use a real channel instead, point monitoring/alertmanager.yml's
webhook_configs.url at that channel's real webhook URL; this receiver is
then no longer needed.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request

LOG_PATH = Path(__file__).parent / "alerts_received.log"

app = Flask(__name__)


def _read_entries() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    entries = []
    for line in LOG_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


@app.post("/webhook")
def webhook():
    payload = request.get_json(force=True, silent=True) or {}
    received_at = datetime.now(timezone.utc).isoformat()
    alerts = payload.get("alerts", [])
    entries = []
    for alert in alerts:
        entries.append(
            {
                "received_at": received_at,
                "status": alert.get("status", "unknown"),
                "alertname": alert.get("labels", {}).get("alertname", "unknown"),
                "severity": alert.get("labels", {}).get("severity", "unknown"),
                "summary": alert.get("annotations", {}).get("summary", ""),
                "starts_at": alert.get("startsAt", ""),
                "ends_at": alert.get("endsAt", ""),
            }
        )
    with LOG_PATH.open("a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
        if not entries:
            f.write(
                json.dumps(
                    {"received_at": received_at, "status": "empty", "raw": payload}
                )
                + "\n"
            )
    return jsonify(received=len(entries)), 200


@app.get("/alerts.json")
def alerts_json():
    """Machine-readable feed used by scripts/verify_alert_path.py."""
    return jsonify(_read_entries()), 200


@app.get("/alerts")
def list_alerts():
    entries = list(reversed(_read_entries()[-200:]))
    if not entries:
        return "<h1>Local Alert Inbox (dev stand-in)</h1><p>No alerts received yet.</p>", 200
    rows = "".join(
        f"<li>{e.get('received_at')} | status={e.get('status')} | "
        f"alertname={e.get('alertname')} | severity={e.get('severity')} | {e.get('summary')}</li>"
        for e in entries
    )
    return f"<h1>Local Alert Inbox (dev stand-in)</h1><ul>{rows}</ul>", 200


@app.get("/health")
def health():
    return jsonify(status="ok"), 200


if __name__ == "__main__":  # pragma: no cover
    app.run(host="0.0.0.0", port=9099)
