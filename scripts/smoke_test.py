#!/usr/bin/env python
"""Smoke tests run against a deployed environment (staging or production).

Unlike the pytest suite (which runs against an in-process Flask test
client), this hits the REAL running process over HTTP, proving the
deployment actually works end-to-end: process up, routes wired,
database writable, metrics exposed.

Usage: python scripts/smoke_test.py --base-url http://localhost:5001
Exit code 0 on success, 1 on any failed check (fails the Jenkins stage).
"""
from __future__ import annotations

import argparse
import sys

import requests

CHECKS_RUN = []


def check(name):
    def decorator(fn):
        def wrapper(base_url):
            try:
                fn(base_url)
            except AssertionError as exc:
                CHECKS_RUN.append((name, False, str(exc)))
                return
            except requests.RequestException as exc:
                CHECKS_RUN.append((name, False, f"request error: {exc}"))
                return
            CHECKS_RUN.append((name, True, ""))
        return wrapper
    return decorator


@check("health endpoint reports ok")
def check_health(base_url):
    resp = requests.get(f"{base_url}/health", timeout=5)
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}"
    body = resp.json()
    assert body["status"] == "ok", f"expected status=ok, got {body}"


@check("metrics endpoint exposes prometheus text format")
def check_metrics(base_url):
    resp = requests.get(f"{base_url}/metrics", timeout=5)
    assert resp.status_code == 200
    assert "http_requests_total" in resp.text


@check("create incident persists and is readable back")
def check_create_and_read(base_url):
    payload = {
        "title": "[smoke-test] sample incident",
        "description": "created by scripts/smoke_test.py",
        "severity": "low",
    }
    created = requests.post(f"{base_url}/api/incidents", json=payload, timeout=5)
    assert created.status_code == 201, created.text
    incident_id = created.json()["id"]

    fetched = requests.get(f"{base_url}/api/incidents/{incident_id}", timeout=5)
    assert fetched.status_code == 200
    assert fetched.json()["title"] == payload["title"]


@check("invalid payload is rejected with 400")
def check_validation(base_url):
    resp = requests.post(f"{base_url}/api/incidents", json={"severity": "high"}, timeout=5)
    assert resp.status_code == 400, f"expected 400 for missing title, got {resp.status_code}"


@check("dashboard page renders")
def check_dashboard(base_url):
    resp = requests.get(base_url, timeout=5)
    assert resp.status_code == 200
    assert "Incident Tracker" in resp.text


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--report", help="optional path to write a JSON results file")
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")

    for fn in (check_health, check_metrics, check_create_and_read, check_validation, check_dashboard):
        fn(base_url)

    failed = [c for c in CHECKS_RUN if not c[1]]
    for name, passed, detail in CHECKS_RUN:
        status = "PASS" if passed else "FAIL"
        line = f"[{status}] {name}"
        if detail:
            line += f" -- {detail}"
        print(line)

    if args.report:
        import json
        from pathlib import Path

        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(
            json.dumps(
                {
                    "base_url": base_url,
                    "checks": [
                        {"name": n, "passed": p, "detail": d} for n, p, d in CHECKS_RUN
                    ],
                    "passed_count": len(CHECKS_RUN) - len(failed),
                    "total_count": len(CHECKS_RUN),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    if failed:
        print(f"\nSmoke tests FAILED: {len(failed)}/{len(CHECKS_RUN)} checks failed against {base_url}")
        return 1
    print(f"\nSmoke tests PASSED: {len(CHECKS_RUN)}/{len(CHECKS_RUN)} checks OK against {base_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
