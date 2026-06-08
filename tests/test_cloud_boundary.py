from core.privacy import cloud_boundary
from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine


def test_redact_excerpts_round_trip():
    eng = RedactionEngine(HouseholdProfile(), lowercase_for_ner=False)
    rows = [{"contact_name": "Dr. Smith", "date": "2024-01-01T00:00:00+00:00",
             "text": "Dr. Smith here — call 617-555-0142 to confirm."}]
    out = cloud_boundary.redact_rows(
        eng, rows, text_fields=["text", "contact_name"], seed_names=["Dr. Smith"],
    )
    assert "Dr. Smith" not in out[0]["text"]
    assert "617-555-0142" not in out[0]["text"]
    assert out[0]["date"] == "2024-01-01T00:00:00+00:00"  # untouched
    # answer-time reversal
    answer = f"{out[0]['contact_name']} wants you to call back."
    assert eng.detokenize(answer) == "Dr. Smith wants you to call back."


def test_redact_rows_no_seed_names():
    eng = RedactionEngine(HouseholdProfile(), lowercase_for_ner=False)
    rows = [{"sender": "chase.com", "subject": "x", "date": "d",
             "body": "Email me at john@x.com"}]
    out = cloud_boundary.redact_rows(eng, rows, text_fields=["body"], seed_names=None)
    assert "john@x.com" not in out[0]["body"]
