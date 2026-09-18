#!/usr/bin/env python
"""Security gate.

Reads the Bandit (static analysis) and pip-audit (dependency
vulnerability) JSON reports produced by the Jenkins Security stage and
applies an explicit gate:

  - any Bandit finding with severity HIGH and confidence >= MEDIUM fails
    the build (unless explicitly suppressed in code with a justified
    ``# nosec`` comment, which Bandit itself excludes from its output).
  - any pip-audit finding for a dependency that has a known fixed version
    available fails the build (an unpatched CVE with no fix yet is
    reported but does not block, since there is nothing actionable to
    change; this must still be explained in the report).

Before any of that, both reports are checked for COMPLETENESS, not just
presence: a report that exists but reflects a scan that never actually
ran (missing/unreadable/malformed JSON, a Bandit analysis error, zero
lines of code analysed, or zero dependencies listed) fails the gate the
same as a missing report. A valid scan that genuinely found nothing is
still a PASS; a scan that never completed is not, even though both cases
look like "no findings" if you only read the top-level results list.

This never silently downgrades severity or ignores findings; it only
gives an explicit, auditable pass/fail decision.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_REPORTS_DIR = Path("reports/security")


def _load_json(path: Path) -> tuple[dict | list | None, str | None]:
    """Read and parse a JSON report. Returns (data, error_message)."""
    if not path.exists():
        return None, f"{path.name} not found; did the scan step run?"
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return None, f"{path.name} could not be read ({exc})"
    if not text.strip():
        return None, f"{path.name} is empty; the scan did not produce a report"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"{path.name} is not valid JSON ({exc}); the scan did not complete cleanly"


def check_bandit(reports_dir: Path) -> tuple[bool, str]:
    bandit_file = reports_dir / "bandit.json"
    data, err = _load_json(bandit_file)
    if err:
        return False, err
    if not isinstance(data, dict) or "results" not in data:
        return False, "bandit.json is missing the expected 'results' field; the scan did not complete cleanly"

    # Bandit records any file it could not analyse (syntax error, bad
    # encoding, internal error) in "errors", separately from "results".
    # A report with errors and few/no results looks identical to a clean
    # scan unless this is checked explicitly.
    errors = data.get("errors", [])
    if errors:
        detail = "; ".join(
            f"{e.get('filename', '?')}: {e.get('reason', 'unknown error')}" for e in errors
        )
        return False, f"Bandit reported {len(errors)} analysis error(s), scan incomplete: {detail}"

    # "results: []" is ambiguous between "scanned everything, found
    # nothing" and "scanned nothing". Bandit's own metrics report lines
    # of code actually analysed, which resolves that ambiguity.
    loc = data.get("metrics", {}).get("_totals", {}).get("loc", 0)
    if loc <= 0:
        return False, (
            "Bandit's metrics report 0 lines of code analysed; treating this as a "
            "failed/incomplete scan, not a clean one"
        )

    results = data.get("results", [])
    blocking = [
        r for r in results
        if r.get("issue_severity") == "HIGH" and r.get("issue_confidence") in ("MEDIUM", "HIGH")
    ]
    if blocking:
        names = ", ".join(sorted({r["test_id"] for r in blocking}))
        return False, f"{len(blocking)} HIGH-severity Bandit finding(s): {names}"
    return True, f"Bandit: {loc} line(s) of code analysed, {len(results)} finding(s) total, none at blocking severity"


def check_pip_audit(reports_dir: Path) -> tuple[bool, str]:
    audit_file = reports_dir / "pip-audit.json"
    data, err = _load_json(audit_file)
    if err:
        return False, err

    if isinstance(data, list):
        dependencies = data
    elif isinstance(data, dict):
        if "dependencies" not in data:
            return False, "pip-audit.json is missing the expected 'dependencies' field; the scan did not complete cleanly"
        dependencies = data["dependencies"]
    else:
        return False, "pip-audit.json has an unexpected top-level structure; the scan did not complete cleanly"

    # requirements.txt for this app always resolves to several packages
    # (Flask and its own dependencies at minimum). pip-audit lists every
    # dependency it examined, each with vulns: [] if clean -- it never
    # legitimately returns an empty dependency list for this project, so
    # an empty list means the scan was skipped, failed, or was run
    # against the wrong/empty input, not that everything is fine.
    if not dependencies:
        return False, (
            "pip-audit reported 0 dependencies for this application, which is never "
            "correct for requirements.txt -- treating this as a failed/skipped "
            "dependency scan, not a clean one"
        )

    blocking = []
    unfixable = []
    for dep in dependencies:
        vulns = dep.get("vulns", [])
        for v in vulns:
            fix_versions = v.get("fix_versions", [])
            entry = f"{dep.get('name')}=={dep.get('version')} ({v.get('id')})"
            if fix_versions:
                blocking.append(entry)
            else:
                unfixable.append(entry)
    if blocking:
        return False, f"{len(blocking)} dependency vulnerability(ies) with an available fix: {', '.join(blocking)}"
    msg = f"pip-audit: {len(dependencies)} dependencies scanned, no fixable vulnerabilities found"
    if unfixable:
        msg += f"; {len(unfixable)} unfixed-upstream finding(s) reported but not blocking: {', '.join(unfixable)}"
    return True, msg


def main() -> int:
    reports_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_REPORTS_DIR
    results = [check_bandit(reports_dir), check_pip_audit(reports_dir)]
    ok = True
    for passed, message in results:
        prefix = "PASS" if passed else "FAIL"
        print(f"[{prefix}] {message}")
        ok = ok and passed

    if ok:
        print("Security gate: PASSED")
        return 0
    print("Security gate: FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
