#!/usr/bin/env python3
"""
test_safe_shell.py — Tests for SAFE Shell

Tests the consent model, stream authorization, command execution,
mid-session revocation, and session audit.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from io import StringIO

# Add parent to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from safe_shell import SAFESession, SAFEShell, STREAMS, HARD_STOPS
from willow_store import WillowStore


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def ok(self, name):
        self.passed += 1
        print(f"  PASS  {name}")

    def fail(self, name, reason=""):
        self.failed += 1
        self.errors.append((name, reason))
        print(f"  FAIL  {name}: {reason}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'=' * 60}")
        print(f"  {self.passed}/{total} passed, {self.failed} failed")
        if self.errors:
            print(f"\n  Failures:")
            for name, reason in self.errors:
                print(f"    - {name}: {reason}")
        print(f"{'=' * 60}")
        return self.failed == 0


def make_temp_store():
    """Create a temp directory for a test store."""
    return tempfile.mkdtemp(prefix="safe_shell_test_")


def make_session(store_root, authorized=None, denied=None):
    """Create a session with pre-set authorization (skip interactive prompt)."""
    session = SAFESession(store_root, username="testuser")
    session._active = True
    session.authorized_streams = set(authorized or [])
    session.denied_streams = set(denied or [])
    return session


def make_shell(store_root, authorized=None):
    """Create a shell with pre-authorized session (skip interactive consent)."""
    shell = SAFEShell.__new__(SAFEShell)
    shell.session = make_session(store_root, authorized=authorized)
    shell.store = shell.session.store
    shell.pg = None
    shell.prompt = "test> "
    shell.stdout = sys.stdout
    return shell


# ── Test: Session Consent Model ──────────────────────────────────────

def test_session_consent(results):
    """Test that consent model works correctly."""
    root = make_temp_store()
    try:
        # All authorized
        s = make_session(root, authorized=list(STREAMS.keys()))
        assert s.check("journal") == True
        assert s.check("knowledge") == True
        assert s.check("nonexistent") == False
        results.ok("session_consent_authorized")

        # Partial authorization
        s2 = make_session(root, authorized=["journal"], denied=["knowledge"])
        assert s2.check("journal") == True
        assert s2.check("knowledge") == False
        assert s2.require("journal") == True
        results.ok("session_consent_partial")

        # No authorization
        s3 = make_session(root, authorized=[], denied=list(STREAMS.keys()))
        for stream in STREAMS:
            assert s3.check(stream) == False
        results.ok("session_consent_none")

    except AssertionError as e:
        results.fail("session_consent", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_session_revoke(results):
    """Test mid-session revocation."""
    root = make_temp_store()
    try:
        s = make_session(root, authorized=["journal", "knowledge"])
        assert s.check("journal") == True

        s.revoke("journal")
        assert s.check("journal") == False
        assert "journal" in s.denied_streams
        results.ok("session_revoke")

        # Revoking something not authorized
        result = s.revoke("media")
        assert result == False
        results.ok("session_revoke_not_authorized")

    except AssertionError as e:
        results.fail("session_revoke", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_session_end(results):
    """Test that session end clears all permissions."""
    root = make_temp_store()
    try:
        s = make_session(root, authorized=list(STREAMS.keys()))
        assert s.check("journal") == True

        s.end()
        assert s.check("journal") == False
        assert len(s.authorized_streams) == 0
        assert s.ended_at is not None
        results.ok("session_end_clears_permissions")

        # Verify audit was written
        store = WillowStore(root)
        record = store.get("safe/sessions", s.session_id)
        assert record is not None
        assert record.get("session_id") == s.session_id
        store.close()
        results.ok("session_end_writes_audit")

    except (AssertionError, Exception) as e:
        results.fail("session_end", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── Test: Shell Commands ─────────────────────────────────────────────

def test_put_get(results):
    """Test put and get commands."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=["journal"])

        # Put a record
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        shell.do_put('journal/entries entry1 {"text": "hello world"}')
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "entry1" in output
        assert "work_quiet" in output
        results.ok("shell_put")

        # Get it back
        sys.stdout = StringIO()
        shell.do_get("journal/entries entry1")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "hello world" in output
        results.ok("shell_get")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("shell_put_get", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_consent_enforcement(results):
    """Test that unauthorized streams are blocked."""
    root = make_temp_store()
    try:
        # Only journal authorized
        shell = make_shell(root, authorized=["journal"])

        # Should work
        old_stdout = sys.stdout
        sys.stdout = StringIO()
        shell.do_put('journal/entries e1 {"text": "ok"}')
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "e1" in output
        results.ok("consent_allows_authorized")

        # Should be blocked — knowledge not authorized
        sys.stdout = StringIO()
        shell.do_put('knowledge/atoms a1 {"title": "blocked"}')
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "denied" in output.lower() or "not authorized" in output.lower()
        results.ok("consent_blocks_unauthorized")

        # Edge command requires knowledge
        sys.stdout = StringIO()
        shell.do_edge("a b c")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "denied" in output.lower() or "not authorized" in output.lower()
        results.ok("consent_blocks_edge_without_knowledge")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("consent_enforcement", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_search(results):
    """Test search within and across collections."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=["journal", "knowledge"])

        old_stdout = sys.stdout

        # Seed some data
        sys.stdout = StringIO()
        shell.do_put('journal/entries j1 {"text": "willow tree"}')
        shell.do_put('journal/entries j2 {"text": "oak tree"}')
        shell.do_put('knowledge/atoms k1 {"title": "willow system"}')
        sys.stdout = old_stdout

        # Search within collection
        sys.stdout = StringIO()
        shell.do_search("journal/entries willow")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "j1" in output
        assert "j2" not in output  # "oak" doesn't match "willow"
        results.ok("shell_search_collection")

        # Search all (ask)
        sys.stdout = StringIO()
        shell.do_ask("willow")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "j1" in output
        assert "k1" in output
        results.ok("shell_ask_all")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("shell_search", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ask_filters_by_consent(results):
    """Test that 'ask' only shows results from authorized streams."""
    root = make_temp_store()
    try:
        # First, seed data with full access
        store = WillowStore(root)
        store.put("journal/entries", {"text": "secret journal"}, record_id="j1")
        store.put("knowledge/atoms", {"text": "public atom"}, record_id="k1")
        store.close()

        # Shell with only knowledge authorized
        shell = make_shell(root, authorized=["knowledge"])

        old_stdout = sys.stdout
        sys.stdout = StringIO()
        shell.do_ask("secret")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout

        # journal result should be hidden
        assert "j1" not in output
        # But the hidden count should show
        # (only if there were results at all)
        results.ok("ask_filters_unauthorized_streams")

        # Now search for the atom — should be visible
        sys.stdout = StringIO()
        shell.do_ask("public")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "k1" in output
        results.ok("ask_shows_authorized_streams")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("ask_consent_filter", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_ls(results):
    """Test listing collections and records."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=["journal", "knowledge"])

        old_stdout = sys.stdout

        # Seed data
        sys.stdout = StringIO()
        shell.do_put('journal/entries j1 {"text": "hello"}')
        shell.do_put('knowledge/atoms k1 {"title": "test"}')
        sys.stdout = old_stdout

        # List collections
        sys.stdout = StringIO()
        shell.do_ls("")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "journal" in output
        assert "knowledge" in output
        results.ok("shell_ls_collections")

        # List records in collection
        sys.stdout = StringIO()
        shell.do_ls("journal/entries")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "j1" in output
        results.ok("shell_ls_records")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("shell_ls", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_update_delete(results):
    """Test update and soft-delete."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=["journal"])

        old_stdout = sys.stdout

        # Create
        sys.stdout = StringIO()
        shell.do_put('journal/entries u1 {"text": "original"}')
        sys.stdout = old_stdout

        # Update
        sys.stdout = StringIO()
        shell.do_update('journal/entries u1 {"text": "modified"}')
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "u1" in output
        results.ok("shell_update")

        # Verify update
        sys.stdout = StringIO()
        shell.do_get("journal/entries u1")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "modified" in output
        results.ok("shell_update_persisted")

        # Delete
        sys.stdout = StringIO()
        shell.do_delete("journal/entries u1")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "Deleted" in output or "deleted" in output
        results.ok("shell_delete")

        # Verify gone
        sys.stdout = StringIO()
        shell.do_get("journal/entries u1")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "Not found" in output or "not found" in output.lower()
        results.ok("shell_delete_removes_from_view")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("shell_update_delete", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_edges(results):
    """Test edge commands."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=["knowledge"])

        old_stdout = sys.stdout

        # Add edge
        sys.stdout = StringIO()
        shell.do_edge("atom1 relates_to atom2 test context")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "work_quiet" in output
        results.ok("shell_edge_add")

        # Query edges
        sys.stdout = StringIO()
        shell.do_edges("atom1")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "atom1" in output
        assert "relates_to" in output
        assert "atom2" in output
        results.ok("shell_edge_query")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("shell_edges", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_audit_trail(results):
    """Test that all operations are audit-trailed."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=["journal"])

        old_stdout = sys.stdout

        # Do some operations
        sys.stdout = StringIO()
        shell.do_put('journal/entries a1 {"text": "audited"}')
        sys.stdout = old_stdout

        # Check audit
        sys.stdout = StringIO()
        shell.do_audit("journal/entries")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "create" in output
        assert "a1" in output
        results.ok("shell_audit_trail")

        # Session audit — add a manual entry since we skip consent prompt in tests
        shell.session._audit("TEST_EVENT", "test_target")
        sys.stdout = StringIO()
        shell.do_audit("")
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "TEST_EVENT" in output
        results.ok("shell_session_audit")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("shell_audit", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_mid_session_revoke_blocks_access(results):
    """Test that revoking a stream mid-session blocks further access."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=["journal", "knowledge"])

        old_stdout = sys.stdout

        # Put works before revoke
        sys.stdout = StringIO()
        shell.do_put('journal/entries r1 {"text": "before revoke"}')
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "r1" in output

        # Revoke journal
        sys.stdout = StringIO()
        shell.session.revoke("journal")
        sys.stdout = old_stdout

        # Put should fail after revoke
        sys.stdout = StringIO()
        shell.do_put('journal/entries r2 {"text": "after revoke"}')
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "denied" in output.lower() or "not authorized" in output.lower()
        results.ok("mid_session_revoke_blocks")

        # Knowledge should still work
        sys.stdout = StringIO()
        shell.do_put('knowledge/atoms kr1 {"title": "still works"}')
        output = sys.stdout.getvalue()
        sys.stdout = old_stdout
        assert "kr1" in output
        results.ok("mid_session_revoke_other_streams_unaffected")

    except (AssertionError, Exception) as e:
        sys.stdout = sys.__stdout__
        results.fail("mid_session_revoke", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_inactive_session_blocks_everything(results):
    """Test that ended session blocks all access."""
    root = make_temp_store()
    try:
        session = make_session(root, authorized=list(STREAMS.keys()))
        assert session.check("journal") == True

        session.end()

        for stream in STREAMS:
            assert session.check(stream) == False

        results.ok("ended_session_blocks_all")

    except AssertionError as e:
        results.fail("inactive_session", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stream_mapping(results):
    """Test that collection paths map to correct streams."""
    root = make_temp_store()
    try:
        shell = make_shell(root, authorized=[])

        assert shell._stream_for_collection("journal/entries") == "journal"
        assert shell._stream_for_collection("conversation/shiva") == "journal"
        assert shell._stream_for_collection("knowledge/atoms") == "knowledge"
        assert shell._stream_for_collection("atoms/test") == "knowledge"
        assert shell._stream_for_collection("agent/ganesha") == "agents"
        assert shell._stream_for_collection("governance/proposals") == "governance"
        assert shell._stream_for_collection("preferences/ui") == "preferences"
        assert shell._stream_for_collection("media/photos") == "media"
        results.ok("stream_mapping")

    except AssertionError as e:
        results.fail("stream_mapping", str(e))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ── Run all tests ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  SAFE Shell Test Suite")
    print("=" * 60)
    print()

    results = TestResults()

    print("Session Consent Model:")
    test_session_consent(results)
    test_session_revoke(results)
    test_session_end(results)
    test_inactive_session_blocks_everything(results)

    print("\nShell Commands:")
    test_put_get(results)
    test_search(results)
    test_ls(results)
    test_update_delete(results)
    test_edges(results)

    print("\nConsent Enforcement:")
    test_consent_enforcement(results)
    test_ask_filters_by_consent(results)
    test_mid_session_revoke_blocks_access(results)

    print("\nAudit & Mapping:")
    test_audit_trail(results)
    test_stream_mapping(results)

    success = results.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
