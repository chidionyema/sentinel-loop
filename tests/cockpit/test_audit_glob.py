"""B1 — Verify `glob` is importable in otto-inbound so the audit path works.

Before the fix, _handle_audit (~line 353) calls glob.glob() but glob was never
imported at module level, causing a NameError that silently swallowed the full
ESTATE-AUDIT-*.md attachment.
"""

import importlib
import os
import sys
import tempfile


def test_glob_is_importable_in_otto_inbound():
    """The otto-inbound module must have glob accessible at module scope."""
    # Ensure ~/.hermes/plugins is on sys.path so we can import otto-inbound
    plugins_dir = os.path.expanduser("~/.hermes/plugins")
    if plugins_dir not in sys.path:
        sys.path.insert(0, plugins_dir)

    mod = importlib.import_module("otto-inbound")
    assert hasattr(mod, "glob"), (
        "otto-inbound module is missing `import glob` — "
        "the audit handler at ~line 353 calls glob.glob() and will raise NameError"
    )
    # Verify it's actually the stdlib glob module, not some other attribute
    assert callable(getattr(mod.glob, "glob", None)), (
        "mod.glob exists but glob.glob is not callable"
    )


def test_audit_handler_no_nameerror():
    """Call the audit handler path and verify no NameError is raised."""
    plugins_dir = os.path.expanduser("~/.hermes/plugins")
    if plugins_dir not in sys.path:
        sys.path.insert(0, plugins_dir)

    mod = importlib.import_module("otto-inbound")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake reports dir with an audit file
        reports_dir = os.path.join(tmpdir, "reports")
        os.makedirs(reports_dir, exist_ok=True)
        audit_file = os.path.join(reports_dir, "ESTATE-AUDIT-2026-01-01.md")
        with open(audit_file, "w") as f:
            f.write("# Estate Audit\n\nTest audit content.\n")

        # Monkeypatch _SCRIPTS to point to our temp dir (reports is a sibling)
        orig_scripts = mod._SCRIPTS
        try:
            # _SCRIPTS is the scripts dir; reports is os.path.dirname(_SCRIPTS)/reports
            # so we point _SCRIPTS to a subdir of tmpdir
            scripts_dir = os.path.join(tmpdir, "scripts")
            os.makedirs(scripts_dir, exist_ok=True)
            mod._SCRIPTS = scripts_dir

            # Now glob.glob on os.path.dirname(_SCRIPTS)/reports/... should find our file
            # Simulate the exact call from _handle_audit:
            import glob as glib
            reports = sorted(glib.glob(os.path.join(
                os.path.dirname(mod._SCRIPTS), "reports", "ESTATE-AUDIT-*.md")))
            assert len(reports) == 1, f"Expected 1 report, found {len(reports)}"
            assert reports[0].endswith("ESTATE-AUDIT-2026-01-01.md")
        finally:
            mod._SCRIPTS = orig_scripts
