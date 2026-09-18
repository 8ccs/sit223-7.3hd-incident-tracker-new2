"""Regression tests for scripts/security_gate.py.

These specifically target the bug where a Bandit report containing an
analysis error and no analysed code, alongside an empty pip-audit
dependency list, was incorrectly treated as PASSED. Every "incomplete
scan" scenario here must FAIL; only a genuinely complete scan may pass.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "security_gate", Path(__file__).resolve().parents[2] / "scripts" / "security_gate.py"
)
security_gate = importlib.util.module_from_spec(_SPEC)
sys.modules["security_gate"] = security_gate
_SPEC.loader.exec_module(security_gate)


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# --- Bandit: incomplete/invalid scans must FAIL -----------------------

def test_bandit_missing_file_fails(tmp_path):
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is False
    assert "not found" in msg


def test_bandit_empty_file_fails(tmp_path):
    (tmp_path / "bandit.json").write_text("", encoding="utf-8")
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is False
    assert "empty" in msg


def test_bandit_malformed_json_fails(tmp_path):
    (tmp_path / "bandit.json").write_text("{not valid json", encoding="utf-8")
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is False
    assert "not valid JSON" in msg


def test_bandit_missing_results_field_fails(tmp_path):
    write_json(tmp_path / "bandit.json", {"errors": [], "metrics": {"_totals": {"loc": 100}}})
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is False
    assert "results" in msg


def test_bandit_analysis_error_fails_even_with_empty_results(tmp_path):
    """The exact scenario from the bug report: an analysis error and no
    analysed code, previously scored as a clean PASS."""
    write_json(tmp_path / "bandit.json", {
        "errors": [{"filename": "app/app.py", "reason": "syntax error"}],
        "results": [],
        "metrics": {"_totals": {"loc": 0}},
    })
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is False
    assert "analysis error" in msg


def test_bandit_zero_loc_fails_even_with_no_errors(tmp_path):
    """No errors reported, but 0 lines analysed -- the scan still never
    ran against real code, so this must not read as a clean pass."""
    write_json(tmp_path / "bandit.json", {
        "errors": [],
        "results": [],
        "metrics": {"_totals": {"loc": 0}},
    })
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is False
    assert "0 lines" in msg


def test_bandit_clean_complete_scan_passes(tmp_path):
    write_json(tmp_path / "bandit.json", {
        "errors": [],
        "results": [],
        "metrics": {"_totals": {"loc": 423}},
    })
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is True
    assert "423" in msg


def test_bandit_high_severity_finding_fails(tmp_path):
    write_json(tmp_path / "bandit.json", {
        "errors": [],
        "metrics": {"_totals": {"loc": 100}},
        "results": [
            {"test_id": "B608", "issue_severity": "HIGH", "issue_confidence": "HIGH"},
        ],
    })
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is False
    assert "B608" in msg


def test_bandit_low_severity_finding_does_not_block(tmp_path):
    write_json(tmp_path / "bandit.json", {
        "errors": [],
        "metrics": {"_totals": {"loc": 100}},
        "results": [
            {"test_id": "B101", "issue_severity": "LOW", "issue_confidence": "HIGH"},
        ],
    })
    passed, msg = security_gate.check_bandit(tmp_path)
    assert passed is True


# --- pip-audit: incomplete/invalid scans must FAIL ---------------------

def test_pip_audit_missing_file_fails(tmp_path):
    passed, msg = security_gate.check_pip_audit(tmp_path)
    assert passed is False
    assert "not found" in msg


def test_pip_audit_malformed_json_fails(tmp_path):
    (tmp_path / "pip-audit.json").write_text("not json at all", encoding="utf-8")
    passed, msg = security_gate.check_pip_audit(tmp_path)
    assert passed is False
    assert "not valid JSON" in msg


def test_pip_audit_missing_dependencies_field_fails(tmp_path):
    write_json(tmp_path / "pip-audit.json", {"fixes": []})
    passed, msg = security_gate.check_pip_audit(tmp_path)
    assert passed is False
    assert "dependencies" in msg


def test_pip_audit_empty_dependency_list_fails(tmp_path):
    """The exact scenario from the bug report: an empty dependency
    report, previously scored as a clean PASS."""
    write_json(tmp_path / "pip-audit.json", {"dependencies": [], "fixes": []})
    passed, msg = security_gate.check_pip_audit(tmp_path)
    assert passed is False
    assert "0 dependencies" in msg


def test_pip_audit_clean_complete_scan_passes(tmp_path):
    write_json(tmp_path / "pip-audit.json", {
        "dependencies": [
            {"name": "flask", "version": "3.1.3", "vulns": []},
            {"name": "waitress", "version": "3.0.2", "vulns": []},
        ],
        "fixes": [],
    })
    passed, msg = security_gate.check_pip_audit(tmp_path)
    assert passed is True
    assert "2 dependencies" in msg


def test_pip_audit_fixable_vulnerability_fails(tmp_path):
    write_json(tmp_path / "pip-audit.json", {
        "dependencies": [
            {"name": "flask", "version": "1.0.0", "vulns": [
                {"id": "CVE-2024-XXXX", "fix_versions": ["3.1.3"]},
            ]},
        ],
        "fixes": [],
    })
    passed, msg = security_gate.check_pip_audit(tmp_path)
    assert passed is False
    assert "CVE-2024-XXXX" in msg


def test_pip_audit_unfixable_vulnerability_does_not_block(tmp_path):
    """A CVE with no fix released yet is reported, not blocking -- there
    is nothing actionable to change."""
    write_json(tmp_path / "pip-audit.json", {
        "dependencies": [
            {"name": "somepkg", "version": "1.0.0", "vulns": [
                {"id": "CVE-2024-YYYY", "fix_versions": []},
            ]},
        ],
        "fixes": [],
    })
    passed, msg = security_gate.check_pip_audit(tmp_path)
    assert passed is True
    assert "unfixed-upstream" in msg


# --- end-to-end gate decision ------------------------------------------

def test_main_fails_on_incomplete_scans(tmp_path, monkeypatch, capsys):
    write_json(tmp_path / "bandit.json", {"errors": [{"filename": "x", "reason": "boom"}], "results": [], "metrics": {"_totals": {"loc": 0}}})
    write_json(tmp_path / "pip-audit.json", {"dependencies": []})
    monkeypatch.setattr(sys, "argv", ["security_gate.py", str(tmp_path)])
    assert security_gate.main() == 1
    out = capsys.readouterr().out
    assert "FAILED" in out


def test_main_passes_on_clean_complete_scans(tmp_path, monkeypatch, capsys):
    write_json(tmp_path / "bandit.json", {"errors": [], "results": [], "metrics": {"_totals": {"loc": 300}}})
    write_json(tmp_path / "pip-audit.json", {"dependencies": [{"name": "flask", "version": "3.1.3", "vulns": []}]})
    monkeypatch.setattr(sys, "argv", ["security_gate.py", str(tmp_path)])
    assert security_gate.main() == 0
    out = capsys.readouterr().out
    assert "PASSED" in out
