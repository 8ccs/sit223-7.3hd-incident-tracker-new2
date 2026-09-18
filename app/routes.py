"""HTTP routes for the incident tracker, as a Blueprint.

Kept separate from app.py (application factory) so that create_app()
stays small: factory wiring in one place, request handling in another.
This also keeps each view function's cyclomatic complexity low and
independently readable, which is what the Code Quality stage measures.
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.metrics import instrument, refresh_incident_gauges
from app.validation import (
    SEVERITIES,
    STATUSES,
    ValidationError,
    can_delete,
    validate_incident_payload,
)

bp = Blueprint("incidents", __name__)


def _store():
    return current_app.config["STORE"]


@bp.get("/health")
@instrument("health")
def health():
    try:
        ok = _store().ping()
    except Exception as exc:  # pragma: no cover - defensive
        return jsonify(status="error", detail=str(exc)), 500
    if not ok:
        return jsonify(status="error", detail="database ping failed"), 500
    return jsonify(
        status="ok",
        version=current_app.config["APP_VERSION"],
        commit=current_app.config["GIT_COMMIT"],
        environment=current_app.config["APP_ENVIRONMENT"],
    ), 200


@bp.get("/metrics")
def metrics():
    refresh_incident_gauges(_store())
    return generate_latest(), 200, {"Content-Type": CONTENT_TYPE_LATEST}


@bp.get("/")
@instrument("index")
def index():
    status = request.args.get("status") or None
    severity = request.args.get("severity") or None
    q = request.args.get("q") or None
    incidents = _store().list(status=status, severity=severity, q=q)
    return render_template(
        "index.html",
        incidents=incidents,
        statuses=STATUSES,
        severities=SEVERITIES,
        selected_status=status or "",
        selected_severity=severity or "",
        query=q or "",
        version=current_app.config["APP_VERSION"],
        environment=current_app.config["APP_ENVIRONMENT"],
    )


def _validate_list_filters(status, severity):
    if status is not None and status not in STATUSES:
        return f"status must be one of {STATUSES}"
    if severity is not None and severity not in SEVERITIES:
        return f"severity must be one of {SEVERITIES}"
    return None


@bp.get("/api/incidents")
@instrument("list_incidents")
def list_incidents():
    status = request.args.get("status") or None
    severity = request.args.get("severity") or None
    q = request.args.get("q") or None
    error = _validate_list_filters(status, severity)
    if error:
        return jsonify(error=error), 400
    return jsonify(_store().list(status=status, severity=severity, q=q)), 200


@bp.post("/api/incidents")
@instrument("create_incident")
def create_incident():
    payload = request.get_json(silent=True) or {}
    try:
        cleaned = validate_incident_payload(payload, partial=False)
    except ValidationError as exc:
        return jsonify(exc.to_dict()), 400
    incident = _store().create(**cleaned)
    return jsonify(incident), 201


@bp.get("/api/incidents/<int:incident_id>")
@instrument("get_incident")
def get_incident(incident_id: int):
    incident = _store().get(incident_id)
    if incident is None:
        return jsonify(error="incident not found"), 404
    return jsonify(incident), 200


@bp.put("/api/incidents/<int:incident_id>")
@instrument("update_incident")
def update_incident(incident_id: int):
    store = _store()
    existing = store.get(incident_id)
    if existing is None:
        return jsonify(error="incident not found"), 404
    payload = request.get_json(silent=True) or {}
    try:
        cleaned = validate_incident_payload(payload, partial=True)
    except ValidationError as exc:
        return jsonify(exc.to_dict()), 400
    if not cleaned:
        return jsonify(error="no valid fields supplied"), 400
    return jsonify(store.update(incident_id, cleaned)), 200


@bp.delete("/api/incidents/<int:incident_id>")
@instrument("delete_incident")
def delete_incident(incident_id: int):
    store = _store()
    existing = store.get(incident_id)
    if existing is None:
        return jsonify(error="incident not found"), 404
    if not can_delete(existing["status"]):
        return jsonify(
            error="only resolved or closed incidents can be deleted",
            status=existing["status"],
        ), 409
    store.delete(incident_id)
    return "", 204


@bp.get("/api/stats")
@instrument("stats")
def stats():
    store = _store()
    return jsonify(
        by_status=store.counts_by("status"),
        by_severity=store.counts_by("severity"),
    ), 200
