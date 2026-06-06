"""Shared fixtures for the email pipeline tests.

Everything here is offline: a fake Anthropic client (no network) and a real
RedactionEngine over a synthetic household profile. No IMAP connections.
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.database.migrate import ensure_schema
from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# Fake Anthropic client (no network)
# ---------------------------------------------------------------------------

class _FakeContent:
    def __init__(self, text):
        self.text = text


class _FakeResp:
    def __init__(self, text):
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, responder):
        self._responder = responder
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResp(self._responder(kwargs))


class FakeClient:
    """Stand-in for anthropic.Anthropic. `responder(kwargs) -> str`."""

    def __init__(self, responder):
        self.messages = _FakeMessages(responder)


def keep_responder(kwargs) -> str:
    """Triage responder: KEEP for every email in the batch."""
    import re

    user = kwargs["messages"][0]["content"]
    n = len(re.findall(r"(?m)^\d+\. FROM:", user))
    return json.dumps({"labels": ["KEEP"] * n})


@pytest.fixture
def fake_keep_client():
    return FakeClient(keep_responder)


# ---------------------------------------------------------------------------
# Redactor + DB
# ---------------------------------------------------------------------------

@pytest.fixture
def profile():
    return HouseholdProfile(
        first_name="Marcus", last_name="Ho",
        partner_first="Sarah", partner_last="Ho", city="Boston",
    )


@pytest.fixture
def redactor(profile):
    return RedactionEngine(profile)


@pytest.fixture
def db(tmp_path):
    """A migrated jar.db. Yields (conn, db_path)."""
    db_path = tmp_path / "jar.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    ensure_schema(conn)
    yield conn, str(db_path)
    conn.close()


# ---------------------------------------------------------------------------
# Email fixtures loaded from disk
# ---------------------------------------------------------------------------

@pytest.fixture
def envelopes():
    return json.loads((FIXTURES / "envelopes.json").read_text())


@pytest.fixture
def body_text():
    def _load(name):
        return (FIXTURES / "bodies" / name).read_text()
    return _load
