"""Tests for triage: local prefilter + Haiku cloud pass (mocked)."""
import json

from core.email import triage
from core.email.triage import _parse_labels, prefilter_label

from tests.email.conftest import FakeClient


# ---------------------------------------------------------------------------
# Prefilter (no network)
# ---------------------------------------------------------------------------

def test_prefilter_keeps_known_bank_domain():
    assert prefilter_label("chase.com", "anything at all") == "KEEP"


def test_prefilter_keeps_finance_subject_keyword():
    assert prefilter_label("unknown.example", "Your statement is ready") == "KEEP"


def test_prefilter_skips_social_domain():
    assert prefilter_label("facebook.com", "You have a new friend") == "SKIP"


def test_prefilter_skips_marketing_subject():
    assert prefilter_label("shop.example", "50% off flash sale") == "SKIP"


def test_prefilter_keep_wins_on_conflict():
    # Bank domain (KEEP) + promotional subject (SKIP) -> KEEP
    assert prefilter_label("chase.com", "flash sale 50% off") == "KEEP"


def test_prefilter_uncertain_returns_none():
    assert prefilter_label("randomperson.example", "lunch tomorrow?") is None


# ---------------------------------------------------------------------------
# Label parsing
# ---------------------------------------------------------------------------

def test_parse_labels_extracts_json():
    assert _parse_labels('{"labels": ["KEEP", "SKIP"]}', 2) == ["KEEP", "SKIP"]


def test_parse_labels_wrong_length_is_none():
    assert _parse_labels('{"labels": ["KEEP"]}', 2) is None


def test_parse_labels_garbage_is_none():
    assert _parse_labels("not json", 1) is None


def test_parse_labels_normalizes_unknown_to_keep():
    assert _parse_labels('{"labels": ["maybe"]}', 1) == ["KEEP"]


# ---------------------------------------------------------------------------
# triage() orchestration
# ---------------------------------------------------------------------------

def _env(domain, subject):
    return {
        "from": f"Sender <noreply@{domain}>",
        "subject": subject,
        "date": "Wed, 14 Jan 2026 09:15:00 -0500",
    }


def test_triage_no_client_defaults_uncertain_to_keep():
    envs = [_env("randomperson.example", "lunch tomorrow?")]
    assert triage.triage(envs, client=None) == ["KEEP"]


def test_triage_aligns_labels_to_input_order(redactor):
    envs = [
        _env("chase.com", "x"),            # prefilter KEEP
        _env("facebook.com", "new friend"),  # prefilter SKIP
        _env("random.example", "hi there"),  # uncertain -> cloud
    ]
    client = FakeClient(lambda kw: json.dumps({"labels": ["SKIP"]}))
    labels = triage.triage(envs, client=client, redactor=redactor)
    assert labels == ["KEEP", "SKIP", "SKIP"]


def test_triage_500_in_500_out():
    envs = [_env("random%d.example" % i, "ambiguous subject %d" % i) for i in range(500)]
    client = FakeClient(
        lambda kw: json.dumps(
            {"labels": ["KEEP"] * len(__import__("re").findall(r"(?m)^\d+\. FROM:", kw["messages"][0]["content"]))}
        )
    )
    labels = triage.triage(envs, client=client, batch_size=500)
    assert len(labels) == 500
    assert all(l == "KEEP" for l in labels)


def test_triage_failsafe_keep_on_bad_json(monkeypatch, tmp_path):
    monkeypatch.setattr(triage, "ERROR_LOG", tmp_path / "err.log")
    envs = [_env("random.example", "ambiguous")]
    client = FakeClient(lambda kw: "this is not json at all")
    assert triage.triage(envs, client=client) == ["KEEP"]
