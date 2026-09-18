"""Prometheus metrics for the incident tracker.

Exposes counters/histograms that Prometheus scrapes from /metrics. These
are the "meaningful live metrics" used by the Monitoring stage's alert
rules (see monitoring/alert_rules.yml).
"""
from __future__ import annotations

import time
from functools import wraps

from flask import request
from prometheus_client import Counter, Histogram, Gauge

from app.validation import STATUSES

HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests processed",
    ["method", "endpoint", "status"],
)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)

INCIDENTS_TOTAL = Gauge(
    "incidents_total",
    "Current number of incidents by status",
    ["status"],
)

APP_INFO = Gauge(
    "app_info",
    "Static build/version info, value is always 1",
    ["version", "commit", "environment"],
)


def instrument(endpoint_name: str):
    """Decorator that records request count and latency for a Flask view."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            method = _current_method()
            status = "500"  # default if view_func raises before setting a real status
            try:
                response = view_func(*args, **kwargs)
                status = _response_status(response)
                return response
            finally:
                duration = time.perf_counter() - start
                HTTP_REQUEST_DURATION_SECONDS.labels(method, endpoint_name).observe(duration)
                HTTP_REQUESTS_TOTAL.labels(method, endpoint_name, status).inc()

        return wrapper

    return decorator


def _current_method() -> str:
    return request.method


def _response_status(response) -> str:
    try:
        return str(response.status_code)
    except AttributeError:
        if isinstance(response, tuple) and len(response) >= 2:
            return str(response[1])
        return "200"


def refresh_incident_gauges(store) -> None:
    counts = store.counts_by("status")
    for status in STATUSES:
        INCIDENTS_TOTAL.labels(status).set(counts.get(status, 0))
