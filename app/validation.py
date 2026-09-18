"""Input validation rules for incidents.

Kept as pure functions with no I/O so they are cheap and easy to unit test.
"""
from __future__ import annotations

SEVERITIES = ("low", "medium", "high", "critical")
STATUSES = ("open", "in_progress", "resolved", "closed")

TITLE_MIN_LEN = 3
TITLE_MAX_LEN = 120
DESCRIPTION_MAX_LEN = 2000


class ValidationError(Exception):
    """Raised when incoming incident data fails validation."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field

    def to_dict(self) -> dict:
        return {"error": self.message, "field": self.field}


def validate_title(title) -> str:
    if not isinstance(title, str) or not title.strip():
        raise ValidationError("title is required", "title")
    title = title.strip()
    if len(title) < TITLE_MIN_LEN:
        raise ValidationError(
            f"title must be at least {TITLE_MIN_LEN} characters", "title"
        )
    if len(title) > TITLE_MAX_LEN:
        raise ValidationError(
            f"title must be at most {TITLE_MAX_LEN} characters", "title"
        )
    return title


def validate_description(description) -> str:
    if description is None:
        return ""
    if not isinstance(description, str):
        raise ValidationError("description must be a string", "description")
    if len(description) > DESCRIPTION_MAX_LEN:
        raise ValidationError(
            f"description must be at most {DESCRIPTION_MAX_LEN} characters",
            "description",
        )
    return description.strip()


def validate_severity(severity) -> str:
    if severity not in SEVERITIES:
        raise ValidationError(
            f"severity must be one of {SEVERITIES}", "severity"
        )
    return severity


def validate_status(status) -> str:
    if status not in STATUSES:
        raise ValidationError(f"status must be one of {STATUSES}", "status")
    return status


def validate_incident_payload(data: dict, *, partial: bool = False) -> dict:
    """Validate a create/update payload.

    When ``partial`` is True (PATCH/PUT-style update), only the fields that
    are present in ``data`` are validated; missing fields are left untouched
    by the caller. When False (create), title and severity are mandatory.
    """
    if not isinstance(data, dict):
        raise ValidationError("request body must be a JSON object")

    cleaned: dict = {}

    if "title" in data or not partial:
        cleaned["title"] = validate_title(data.get("title"))

    if "description" in data or not partial:
        cleaned["description"] = validate_description(data.get("description"))

    if "severity" in data or not partial:
        cleaned["severity"] = validate_severity(data.get("severity"))

    if "status" in data:
        cleaned["status"] = validate_status(data.get("status"))
    elif not partial:
        cleaned["status"] = "open"

    return cleaned


def can_delete(status: str) -> bool:
    """Business rule: only resolved/closed incidents may be deleted.

    This protects against accidentally deleting an incident that the team
    is still actively working on.
    """
    return status in ("resolved", "closed")
