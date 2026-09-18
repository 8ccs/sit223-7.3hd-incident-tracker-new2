import json

import pytest

from app.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "integration.db"))
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def post_incident(client, **overrides):
    payload = {
        "title": "Checkout API returning 500",
        "description": "Started after the 14:00 deploy",
        "severity": "high",
    }
    payload.update(overrides)
    return client.post("/api/incidents", json=payload)


def test_health_endpoint_reports_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"


def test_metrics_endpoint_exposes_prometheus_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert b"http_requests_total" in resp.data


def test_create_incident_success(client):
    resp = post_incident(client)
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["status"] == "open"
    assert body["severity"] == "high"
    assert body["id"] > 0


def test_create_incident_missing_title_returns_400(client):
    resp = client.post("/api/incidents", json={"severity": "high"})
    assert resp.status_code == 400
    assert resp.get_json()["field"] == "title"


def test_create_incident_invalid_severity_returns_400(client):
    resp = post_incident(client, severity="apocalyptic")
    assert resp.status_code == 400


def test_create_incident_rejects_non_json_body(client):
    resp = client.post("/api/incidents", data="not json", content_type="text/plain")
    assert resp.status_code == 400


def test_get_incident_not_found_returns_404(client):
    resp = client.get("/api/incidents/99999")
    assert resp.status_code == 404


def test_list_incidents_returns_created_items(client):
    post_incident(client, title="First incident")
    post_incident(client, title="Second incident")
    resp = client.get("/api/incidents")
    assert resp.status_code == 200
    titles = [i["title"] for i in resp.get_json()]
    assert "First incident" in titles
    assert "Second incident" in titles


def test_list_incidents_invalid_status_filter_returns_400(client):
    resp = client.get("/api/incidents?status=archived")
    assert resp.status_code == 400


def test_list_incidents_filters_by_status(client):
    created = post_incident(client, title="Will resolve").get_json()
    client.put(f"/api/incidents/{created['id']}", json={"status": "resolved"})
    post_incident(client, title="Still open")

    resp = client.get("/api/incidents?status=resolved")
    titles = [i["title"] for i in resp.get_json()]
    assert titles == ["Will resolve"]


def test_update_incident_success(client):
    created = post_incident(client).get_json()
    resp = client.put(f"/api/incidents/{created['id']}", json={"status": "in_progress"})
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "in_progress"


def test_update_incident_not_found_returns_404(client):
    resp = client.put("/api/incidents/99999", json={"status": "resolved"})
    assert resp.status_code == 404


def test_update_incident_invalid_status_returns_400(client):
    created = post_incident(client).get_json()
    resp = client.put(f"/api/incidents/{created['id']}", json={"status": "bogus"})
    assert resp.status_code == 400


def test_delete_open_incident_is_rejected(client):
    """Business rule: an open incident must be resolved/closed before deletion."""
    created = post_incident(client).get_json()
    resp = client.delete(f"/api/incidents/{created['id']}")
    assert resp.status_code == 409
    # incident must still exist
    assert client.get(f"/api/incidents/{created['id']}").status_code == 200


def test_delete_resolved_incident_succeeds(client):
    created = post_incident(client).get_json()
    client.put(f"/api/incidents/{created['id']}", json={"status": "resolved"})
    resp = client.delete(f"/api/incidents/{created['id']}")
    assert resp.status_code == 204
    assert client.get(f"/api/incidents/{created['id']}").status_code == 404


def test_delete_missing_incident_returns_404(client):
    resp = client.delete("/api/incidents/99999")
    assert resp.status_code == 404


def test_stats_endpoint_reflects_created_incidents(client):
    post_incident(client, severity="critical")
    post_incident(client, severity="critical")
    resp = client.get("/api/stats")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["by_severity"]["critical"] == 2


def test_index_page_renders_html(client):
    post_incident(client, title="Visible on dashboard")
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Visible on dashboard" in resp.data


def test_index_page_filter_by_severity(client):
    post_incident(client, title="Loud one", severity="critical")
    post_incident(client, title="Quiet one", severity="low")
    resp = client.get("/?severity=critical")
    assert b"Loud one" in resp.data
    assert b"Quiet one" not in resp.data
