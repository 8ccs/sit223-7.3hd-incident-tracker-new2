import pytest

from app.models import IncidentStore


@pytest.fixture
def store(tmp_path):
    return IncidentStore(str(tmp_path / "test.db"))


def test_ping_returns_true_on_fresh_db(store):
    assert store.ping() is True


def test_create_and_get_incident(store):
    created = store.create("Disk full", "root partition at 98%", "high", "open")
    assert created["id"] is not None
    fetched = store.get(created["id"])
    assert fetched["title"] == "Disk full"
    assert fetched["status"] == "open"


def test_get_missing_incident_returns_none(store):
    assert store.get(9999) is None


def test_list_filters_by_status(store):
    store.create("A", "", "low", "open")
    store.create("B", "", "low", "resolved")
    open_only = store.list(status="open")
    assert len(open_only) == 1
    assert open_only[0]["title"] == "A"


def test_list_filters_by_severity(store):
    store.create("A", "", "critical", "open")
    store.create("B", "", "low", "open")
    critical_only = store.list(severity="critical")
    assert len(critical_only) == 1
    assert critical_only[0]["title"] == "A"


def test_list_search_matches_title_or_description(store):
    store.create("Payment gateway down", "checkout failing", "critical", "open")
    store.create("Unrelated", "nothing here", "low", "open")
    results = store.list(q="checkout")
    assert len(results) == 1
    assert results[0]["title"] == "Payment gateway down"


def test_update_changes_fields_and_updated_at(store):
    created = store.create("A", "", "low", "open")
    updated = store.update(created["id"], {"status": "resolved"})
    assert updated["status"] == "resolved"
    assert updated["updated_at"] >= created["updated_at"]


def test_update_missing_incident_returns_none(store):
    assert store.update(9999, {"status": "resolved"}) is None


def test_delete_existing_incident_returns_true(store):
    created = store.create("A", "", "low", "resolved")
    assert store.delete(created["id"]) is True
    assert store.get(created["id"]) is None


def test_delete_missing_incident_returns_false(store):
    assert store.delete(9999) is False


def test_counts_by_status(store):
    store.create("A", "", "low", "open")
    store.create("B", "", "low", "open")
    store.create("C", "", "low", "resolved")
    counts = store.counts_by("status")
    assert counts["open"] == 2
    assert counts["resolved"] == 1
