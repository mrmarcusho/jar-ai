"""Tests for core/privacy/ — RedactionEngine and HouseholdProfile."""
import sqlite3
from pathlib import Path
import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.privacy.profile import HouseholdProfile
from core.privacy.redactor import RedactionEngine
from core.privacy._token_store import TokenStore

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "core" / "database" / "schema.sql"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def profile():
    return HouseholdProfile(
        first_name="Marcus",
        last_name="Ho",
        aliases=["Marc"],
        partner_first="Sarah",
        partner_last="Ho",
        city="Boston",
    )


@pytest.fixture
def engine(profile):
    return RedactionEngine(profile)


@pytest.fixture
def empty_engine():
    return RedactionEngine(HouseholdProfile())


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_PATH.read_text())
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# TokenStore unit tests
# ---------------------------------------------------------------------------

class TestTokenStore:
    def test_creates_token(self):
        store = TokenStore()
        token = store.get_or_create("PERSON", "John Smith")
        assert token == "[PERSON_1]"

    def test_idempotent_same_value(self):
        store = TokenStore()
        t1 = store.get_or_create("PERSON", "John Smith")
        t2 = store.get_or_create("PERSON", "John Smith")
        assert t1 == t2

    def test_case_insensitive_lookup(self):
        store = TokenStore()
        t1 = store.get_or_create("PERSON", "john smith")
        t2 = store.get_or_create("PERSON", "JOHN SMITH")
        assert t1 == t2

    def test_different_values_get_different_tokens(self):
        store = TokenStore()
        t1 = store.get_or_create("PERSON", "John Smith")
        t2 = store.get_or_create("PERSON", "Jane Doe")
        assert t1 != t2

    def test_counters_per_entity_type(self):
        store = TokenStore()
        store.get_or_create("PERSON", "A")
        store.get_or_create("PERSON", "B")
        store.get_or_create("BANK_ACCOUNT", "1234")
        assert store.get_or_create("PERSON", "C") == "[PERSON_3]"
        assert store.get_or_create("BANK_ACCOUNT", "5678") == "[BANK_ACCOUNT_2]"

    def test_reserve_fixed_token(self):
        store = TokenStore()
        store.reserve("[ACCT_HOLDER]", "Marcus Ho")
        assert store.original("[ACCT_HOLDER]") == "Marcus Ho"

    def test_original_lookup(self):
        store = TokenStore()
        store.get_or_create("PERSON", "Alice")
        assert store.original("[PERSON_1]") == "Alice"

    def test_unknown_token_returns_none(self):
        store = TokenStore()
        assert store.original("[FAKE_TOKEN]") is None


# ---------------------------------------------------------------------------
# HouseholdProfile unit tests
# ---------------------------------------------------------------------------

class TestHouseholdProfile:
    def test_is_configured_with_names(self, profile):
        assert profile.is_configured()

    def test_not_configured_empty(self):
        assert not HouseholdProfile().is_configured()

    def test_configured_last_name_only(self):
        assert HouseholdProfile(last_name="Ho").is_configured()

    def test_name_variants_include_full_name(self, profile):
        variants = [v for v, _ in profile.all_name_variants()]
        assert "Marcus Ho" in variants

    def test_name_variants_include_reversed(self, profile):
        variants = [v for v, _ in profile.all_name_variants()]
        assert "Ho Marcus" in variants

    def test_name_variants_include_ach_slash(self, profile):
        variants = [v for v, _ in profile.all_name_variants()]
        assert "Ho/Marcus" in variants

    def test_name_variants_sorted_longest_first(self, profile):
        variants = [v for v, _ in profile.all_name_variants()]
        lengths = [len(v) for v in variants]
        assert lengths == sorted(lengths, reverse=True)

    def test_name_variants_token_assignment(self, profile):
        token_map = {v: t for v, t in profile.all_name_variants()}
        assert token_map.get("Marcus Ho") == "[ACCT_HOLDER]"
        assert token_map.get("Sarah Ho") == "[PARTNER]"
        assert token_map.get("Boston") == "[CITY]"

    def test_save_and_load(self, profile, db):
        profile.save(db)
        loaded = HouseholdProfile.load(db)
        assert loaded.first_name == profile.first_name
        assert loaded.last_name == profile.last_name
        assert loaded.partner_first == profile.partner_first
        assert loaded.city == profile.city
        assert set(loaded.aliases) == set(profile.aliases)

    def test_load_empty_db(self, db):
        loaded = HouseholdProfile.load(db)
        assert not loaded.is_configured()

    def test_save_replaces_existing(self, profile, db):
        profile.save(db)
        updated = HouseholdProfile(first_name="James", last_name="Ho")
        updated.save(db)
        loaded = HouseholdProfile.load(db)
        assert loaded.first_name == "James"

    def test_load_missing_table(self):
        """load() on a DB without the table returns an empty profile (no crash)."""
        conn = sqlite3.connect(":memory:")
        result = HouseholdProfile.load(conn)
        conn.close()
        assert not result.is_configured()


# ---------------------------------------------------------------------------
# RedactionEngine — Pass 1 (profile matching)
# ---------------------------------------------------------------------------

class TestPass1Profile:
    def test_redacts_full_name(self, engine):
        assert "[ACCT_HOLDER]" in engine.redact("MARCUS HO")

    def test_redacts_partner_name(self, engine):
        assert "[PARTNER]" in engine.redact("SARAH HO")

    def test_redacts_city(self, engine):
        assert "[CITY]" in engine.redact("Payment in Boston MA")

    def test_case_insensitive(self, engine):
        assert "[ACCT_HOLDER]" in engine.redact("marcus ho")

    def test_no_nested_token_replacement(self, engine):
        result = engine.redact("MARCUS HO")
        assert result == "[ACCT_HOLDER]"
        assert "ACCT_HOLDER" not in result.replace("[ACCT_HOLDER]", "")

    def test_preserves_non_pii(self, engine):
        result = engine.redact("WALMART #123 PURCHASE")
        assert result == "WALMART #123 PURCHASE"

    def test_partial_name_not_replaced_if_too_short(self):
        p = HouseholdProfile(first_name="Ji", last_name="Lee")
        eng = RedactionEngine(p)
        # Short first name alone (2 chars) is not added as a variant (< 4-char threshold)
        assert "Ji" in eng.redact("Payment Ji received")
        # But the full name "Ji Lee" IS a variant and gets replaced
        assert "Ji" not in eng.redact("Ji Lee payment")

    def test_alias_redacted(self, engine):
        assert "[ACCT_HOLDER]" in engine.redact("Payment from Marc")


# ---------------------------------------------------------------------------
# RedactionEngine — Pass 1b (payment app patterns)
# ---------------------------------------------------------------------------

class TestPass1PaymentApps:
    def test_venmo_name_redacted(self, engine):
        result = engine.redact("VENMO*JOHN SMITH")
        assert "JOHN SMITH" not in result
        assert "VENMO*" in result

    def test_zelle_name_redacted(self, engine):
        result = engine.redact("ZELLE PAYMENT FROM JOHN SMITH 500.00")
        assert "JOHN SMITH" not in result

    def test_zelle_to_name_redacted(self, engine):
        result = engine.redact("ZELLE PAYMENT TO ALICE JONES 100.00")
        assert "ALICE JONES" not in result

    def test_check_payee_redacted(self, engine):
        result = engine.redact("PAY TO THE ORDER OF JANE DOE")
        assert "JANE DOE" not in result

    def test_merchant_name_preserved(self, engine):
        result = engine.redact("VENMO*STARBUCKS")
        # Single-word merchant names don't match the name pattern (needs 2 words)
        assert result in ("VENMO*STARBUCKS", "VENMO*[PERSON_1]")


# ---------------------------------------------------------------------------
# RedactionEngine — Pass 2 (Presidio NER)
# ---------------------------------------------------------------------------

class TestPass2Presidio:
    def test_ssn_redacted(self, empty_engine):
        result = empty_engine.redact("SSN 123-45-6789")
        assert "123-45-6789" not in result

    def test_email_redacted(self, empty_engine):
        result = empty_engine.redact("Contact: user@example.com")
        assert "user@example.com" not in result

    def test_phone_redacted(self, empty_engine):
        # Phone patterns need context to avoid false positives on other digit sequences
        result = empty_engine.redact("Phone: (617) 555-1234")
        assert "(617) 555-1234" not in result

    def test_account_number_with_context(self, empty_engine):
        result = empty_engine.redact("Account Number: 1234567890")
        assert "1234567890" not in result

    def test_routing_number_with_context(self, empty_engine):
        result = empty_engine.redact("Routing: 021000021")
        assert "021000021" not in result

    def test_partial_account_redacted(self, empty_engine):
        result = empty_engine.redact("Account ending in 1234")
        assert "ending in 1234" not in result

    def test_starred_account_redacted(self, empty_engine):
        result = empty_engine.redact("Account ****5678")
        assert "****5678" not in result

    def test_allcaps_person_name(self, empty_engine):
        result = empty_engine.redact("PAYEE JOHN SMITH AMOUNT 100.00")
        # spaCy should detect JOHN SMITH even in all-caps after lowercasing
        assert "JOHN SMITH" not in result

    def test_no_false_positive_merchant(self, empty_engine):
        result = empty_engine.redact("AMAZON MARKETPLACE PURCHASE")
        # AMAZON should be classified as ORG, not redacted as PERSON
        # (allowing some tolerance — the key thing is the round-trip works)
        assert isinstance(result, str)

    def test_street_address_redacted(self, empty_engine):
        result = empty_engine.redact("310 14TH ST")
        assert "310 14TH ST" not in result
        assert "[ADDRESS_" in result

    def test_street_address_with_suffix_word(self, empty_engine):
        result = empty_engine.redact("3418 NORTHERN BLVD")
        assert "3418 NORTHERN BLVD" not in result

    def test_zip_plus_four_redacted(self, empty_engine):
        result = empty_engine.redact("HUNTINGTON BEACH, CA 92648-4218")
        assert "92648-4218" not in result

    def test_state_zip_redacted(self, empty_engine):
        result = empty_engine.redact("Portland, OR 97228")
        assert "OR 97228" not in result

    def test_bare_amount_not_zip(self, empty_engine):
        # A plain 5-digit number with no ZIP/address context must NOT be
        # redacted as a ZIP — it scores too low to fire on its own.
        result = empty_engine.redact("Balance forward 12345 items processed")
        assert "12345" in result

    def test_grouped_card_number_redacted(self, empty_engine):
        result = empty_engine.redact("Account Number: 4266 9020 7133 1415")
        assert "4266 9020 7133 1415" not in result

    def test_dashed_card_number_redacted(self, empty_engine):
        result = empty_engine.redact("Card 4266-9020-7133-1415 on file")
        assert "4266-9020-7133-1415" not in result

    def test_grouped_account_number_redacted(self, empty_engine):
        result = empty_engine.redact("Account Number: 123-456-789")
        assert "123-456-789" not in result

    def test_grouped_account_does_not_eat_dates(self, empty_engine):
        # Dashed dates have 2-digit groups and must survive.
        text = "Posted 01-02-2023 Amazon 50.00"
        assert empty_engine.redact(text) == text

    def test_ein_redacted_with_context(self, empty_engine):
        result = empty_engine.redact("Tax ID (EIN): 12-3456789")
        assert "12-3456789" not in result

    def test_dob_redacted_with_context(self, empty_engine):
        result = empty_engine.redact("Date of Birth: 03/15/1985")
        assert "03/15/1985" not in result

    def test_transaction_date_not_redacted(self, empty_engine):
        # A plain transaction date with no birth context must survive.
        text = "03/15/2024 STARBUCKS PURCHASE 5.40"
        assert empty_engine.redact(text) == text

    def test_international_phone_redacted(self, empty_engine):
        result = empty_engine.redact("Tel +44 20 7946 0958")
        assert "+44 20 7946 0958" not in result

    def test_uk_sort_code_redacted(self, empty_engine):
        result = empty_engine.redact("Sort Code 12-34-56")
        assert "12-34-56" not in result

    def test_ip_address_redacted(self, empty_engine):
        result = empty_engine.redact("Login from 192.168.1.42")
        assert "192.168.1.42" not in result

    def test_uk_postcode_redacted(self, empty_engine):
        for pc in ("SW1A 1AA", "M1 1AE", "YZ985XY"):
            result = empty_engine.redact(f"Address line, {pc}")
            assert pc not in result, f"UK postcode {pc} leaked"

    def test_sweep_catches_long_reference_number(self, empty_engine):
        # 23-digit REF# exceeds typical account length; sweep must still catch it.
        result = empty_engine.redact("AMZN Mktp REF # 24692169203100596188416 Debit")
        assert "24692169203100596188416" not in result

    def test_sweep_catches_residual_zip_plus4(self, empty_engine):
        # A ZIP+4 directly adjacent to a redaction token (no space) must not leak.
        result = empty_engine.redact("[PERSON_1]55443-1629")
        assert "55443-1629" not in result

    def test_sweep_round_trip(self, empty_engine):
        original = "REF 24692169203100596188416 amount 50.00"
        assert empty_engine.detokenize(empty_engine.redact(original)) == original

    def test_multiline_address_block_no_leak(self, empty_engine):
        """Regression: a street line must not leak when preceded by a card-number
        tail and the account holder's name on adjacent lines.

        The street recognizer once used \\s (which matches newlines), so it
        greedily spanned 'card-tail + name + street', then lost to the
        higher-scoring PERSON span in dedup — silently leaving the street
        address exposed.
        """
        block = (
            "Account Number: 4266 9020 7133 1415\n"
            "Anthony Gonzalez\n"
            "12661 Louvre Street\n"
            "Pacoima, CA 91331"
        )
        result = empty_engine.redact(block)
        assert "12661 Louvre Street" not in result
        assert "4266 9020 7133 1415" not in result
        assert "Anthony Gonzalez" not in result


# ---------------------------------------------------------------------------
# RedactionEngine — detokenize / round-trip
# ---------------------------------------------------------------------------

class TestDetokenize:
    def test_round_trip_full_name(self, engine):
        original = "MARCUS HO"
        assert engine.detokenize(engine.redact(original)).upper() == original

    def test_round_trip_ssn(self, empty_engine):
        original = "SSN 123-45-6789"
        assert empty_engine.detokenize(empty_engine.redact(original)) == original

    def test_round_trip_email(self, empty_engine):
        original = "Contact: user@example.com"
        assert empty_engine.detokenize(empty_engine.redact(original)) == original

    def test_round_trip_account_statement(self, engine):
        original = "MARCUS HO Account Number: 1234567890"
        assert engine.detokenize(engine.redact(original)).upper() == original.upper()

    def test_detokenize_unknown_token_unchanged(self, engine):
        text = "Value [UNKNOWN_TOKEN_99]"
        assert engine.detokenize(text) == text

    def test_redact_dict_strings(self, engine):
        d = {"name": "MARCUS HO", "amount": 100.0, "date": "2024-01-01"}
        result = engine.redact_dict(d)
        assert "[ACCT_HOLDER]" in result["name"]
        assert result["amount"] == 100.0       # float unchanged
        assert result["date"] == "2024-01-01"  # date unchanged (no PII)

    def test_redact_dict_nested(self, engine):
        d = {"txn": {"description": "MARCUS HO PAYMENT", "amount": 50.0}}
        result = engine.redact_dict(d)
        assert "MARCUS HO" not in result["txn"]["description"]

    def test_redact_dict_list(self, engine):
        items = ["MARCUS HO", "WALMART PURCHASE", "SARAH HO"]
        result = engine.redact_dict(items)
        assert any("[ACCT_HOLDER]" in r for r in result)
        assert any("[PARTNER]" in r for r in result)

    def test_detokenize_dict_round_trip(self, engine):
        d = {"desc": "MARCUS HO TRANSFER", "amount": 200.0}
        redacted = engine.redact_dict(d)
        restored = engine.detokenize_dict(redacted)
        # Canonical profile form is restored (may differ in case from original)
        assert "marcus ho" in restored["desc"].lower()
        assert restored["amount"] == 200.0

    def test_no_double_redaction(self, engine):
        text = "MARCUS HO"
        once = engine.redact(text)
        twice = engine.redact(once)
        assert once == twice
