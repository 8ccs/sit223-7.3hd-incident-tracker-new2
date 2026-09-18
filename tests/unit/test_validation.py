import pytest

from app.validation import (
    ValidationError,
    can_delete,
    validate_description,
    validate_incident_payload,
    validate_severity,
    validate_status,
    validate_title,
)


def test_validate_title_accepts_valid_title():
    assert validate_title("Database outage") == "Database outage"


def test_validate_title_strips_whitespace():
    assert validate_title("  Disk full  ") == "Disk full"


def test_validate_title_rejects_missing():
    with pytest.raises(ValidationError):
        validate_title(None)


def test_validate_title_rejects_too_short():
    with pytest.raises(ValidationError):
        validate_title("ab")


def test_validate_title_rejects_too_long():
    with pytest.raises(ValidationError):
        validate_title("x" * 121)


def test_validate_description_defaults_to_empty_string():
    assert validate_description(None) == ""


def test_validate_description_rejects_too_long():
    with pytest.raises(ValidationError):
        validate_description("x" * 2001)


def test_validate_severity_accepts_known_values():
    for value in ("low", "medium", "high", "critical"):
        assert validate_severity(value) == value


def test_validate_severity_rejects_unknown_value():
    with pytest.raises(ValidationError):
        validate_severity("catastrophic")


def test_validate_status_rejects_unknown_value():
    with pytest.raises(ValidationError):
        validate_status("archived")


def test_validate_incident_payload_full_create():
    cleaned = validate_incident_payload(
        {"title": "API down", "description": "500s on /api", "severity": "high"}
    )
    assert cleaned == {
        "title": "API down",
        "description": "500s on /api",
        "severity": "high",
        "status": "open",
    }


def test_validate_incident_payload_create_requires_title():
    with pytest.raises(ValidationError):
        validate_incident_payload({"severity": "high"})


def test_validate_incident_payload_partial_update_only_touches_given_fields():
    cleaned = validate_incident_payload({"status": "resolved"}, partial=True)
    assert cleaned == {"status": "resolved"}


def test_validate_incident_payload_rejects_non_dict():
    with pytest.raises(ValidationError):
        validate_incident_payload(["not", "a", "dict"])


@pytest.mark.parametrize(
    "status,expected",
    [("open", False), ("in_progress", False), ("resolved", True), ("closed", True)],
)
def test_can_delete_business_rule(status, expected):
    assert can_delete(status) is expected
