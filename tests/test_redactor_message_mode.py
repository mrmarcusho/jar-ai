from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine


def test_register_person_tokenizes_known_name():
    eng = RedactionEngine(HouseholdProfile(), lowercase_for_ner=False)
    eng.register_person("Dr. Smith")
    out = eng.redact("Reminder from Dr. Smith about your appointment")
    assert "Dr. Smith" not in out
    assert eng.detokenize(out) == "Reminder from Dr. Smith about your appointment"


def test_lowercase_flag_defaults_true_backcompat():
    eng = RedactionEngine(HouseholdProfile())
    assert eng._lowercase_for_ner is True  # legacy bank behavior preserved
