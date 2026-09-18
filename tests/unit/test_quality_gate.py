"""Regression tests for scripts/quality_gate.py.

Mirrors tests/unit/test_security_gate.py: an empty/incomplete radon
report must FAIL the gate, not silently PASS as "no complexity
problems found" (the same class of bug fixed in security_gate.py).
"""
import importlib.util
import json
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "quality_gate", Path(__file__).resolve().parents[2] / "scripts" / "quality_gate.py"
)
quality_gate = importlib.util.module_from_spec(_SPEC)
sys.modules["quality_gate"] = quality_gate
_SPEC.loader.exec_module(quality_gate)


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


# --- pylint score check --------------------------------------------------

def test_pylint_missing_score_file_fails(tmp_path):
    passed, msg = quality_gate.check_pylint(tmp_path)
    assert passed is False
    assert "not found" in msg


def test_pylint_unparseable_score_fails(tmp_path):
    (tmp_path / "pylint-score.txt").write_text("not-a-number", encoding="utf-8")
    passed, msg = quality_gate.check_pylint(tmp_path)
    assert passed is False
    assert "could not parse" in msg


def test_pylint_below_threshold_fails(tmp_path):
    (tmp_path / "pylint-score.txt").write_text("7.50", encoding="utf-8")
    passed, msg = quality_gate.check_pylint(tmp_path)
    assert passed is False


def test_pylint_at_or_above_threshold_passes(tmp_path):
    (tmp_path / "pylint-score.txt").write_text("9.96", encoding="utf-8")
    passed, msg = quality_gate.check_pylint(tmp_path)
    assert passed is True


# --- radon complexity check: incomplete scans must FAIL -------------------

def test_radon_missing_file_fails(tmp_path):
    passed, msg = quality_gate.check_radon(tmp_path)
    assert passed is False
    assert "not found" in msg


def test_radon_empty_file_fails(tmp_path):
    (tmp_path / "radon-cc.json").write_text("", encoding="utf-8")
    passed, msg = quality_gate.check_radon(tmp_path)
    assert passed is False
    assert "empty" in msg


def test_radon_malformed_json_fails(tmp_path):
    (tmp_path / "radon-cc.json").write_text("{broken", encoding="utf-8")
    passed, msg = quality_gate.check_radon(tmp_path)
    assert passed is False
    assert "not valid JSON" in msg


def test_radon_zero_analysed_files_fails(tmp_path):
    """The empty-report scenario: radon produced valid JSON, but it
    analysed nothing. Previously scored as rank A ("clean")."""
    write_json(tmp_path / "radon-cc.json", {})
    passed, msg = quality_gate.check_radon(tmp_path)
    assert passed is False
    assert "0 analysed files" in msg


def test_radon_clean_complete_scan_passes(tmp_path):
    write_json(tmp_path / "radon-cc.json", {
        "app/app.py": [{"name": "create_app", "rank": "A"}],
        "app/routes.py": [{"name": "index", "rank": "B"}],
    })
    passed, msg = quality_gate.check_radon(tmp_path)
    assert passed is True
    assert "rank: B" in msg


def test_radon_over_threshold_complexity_fails(tmp_path):
    write_json(tmp_path / "radon-cc.json", {
        "app/messy.py": [{"name": "do_everything", "rank": "E"}],
    })
    passed, msg = quality_gate.check_radon(tmp_path)
    assert passed is False
    assert "rank: E" in msg


# --- end-to-end gate decision --------------------------------------------

def test_main_fails_on_incomplete_radon_scan(tmp_path, monkeypatch, capsys):
    (tmp_path / "pylint-score.txt").write_text("9.5", encoding="utf-8")
    write_json(tmp_path / "radon-cc.json", {})
    monkeypatch.setattr(sys, "argv", ["quality_gate.py", str(tmp_path)])
    assert quality_gate.main() == 1
    assert "FAILED" in capsys.readouterr().out


def test_main_passes_on_clean_complete_scans(tmp_path, monkeypatch, capsys):
    (tmp_path / "pylint-score.txt").write_text("9.96", encoding="utf-8")
    write_json(tmp_path / "radon-cc.json", {"app/app.py": [{"name": "create_app", "rank": "A"}]})
    monkeypatch.setattr(sys, "argv", ["quality_gate.py", str(tmp_path)])
    assert quality_gate.main() == 0
    assert "PASSED" in capsys.readouterr().out
