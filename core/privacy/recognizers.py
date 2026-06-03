"""Custom Presidio recognizers for bank-statement-specific PII patterns.

These extend Presidio's built-in recognizer set (PERSON, US_SSN, PHONE_NUMBER,
EMAIL_ADDRESS, LOCATION) with patterns specific to financial documents.
"""
from __future__ import annotations

from presidio_analyzer import Pattern, PatternRecognizer


class BankAccountNumberRecognizer(PatternRecognizer):
    """Detects bank account / card / reference numbers (8+ digit runs).

    Privacy policy: ANY run of 8+ digits is redacted unconditionally. This is
    deliberately aggressive — account numbers, card numbers, ACH "CCD ID"s,
    confirmation/reference numbers and the like are indistinguishable by shape,
    and the cloud LLM never needs the raw digits to extract a transaction. The
    real value is restored locally via detokenize(), so over-redaction is free.

    Amounts (1,258.56) and dates (10/01) are NOT bare digit runs and so are not
    matched. A context word ("account", "card") still pushes the score higher.
    """

    PATTERNS = [
        # Grouped 16-digit card / account numbers: "4266 9020 7133 1415" or
        # "4266-9020-7133-1415". Fires on its own (these are unambiguous PII).
        Pattern("Card grouped 16", r"\b\d{4}[ -]\d{4}[ -]\d{4}[ -]\d{4}\b", 0.7),
        # Amex-style 15-digit grouping: "3782 822463 10005"
        Pattern("Card grouped 15", r"\b\d{4}[ -]\d{6}[ -]\d{5}\b", 0.7),
        # Generic grouped account number: 3+ groups of 3+ digits, e.g.
        # "123-456-789" / "1234 5678 90". Requiring 3-digit groups avoids
        # matching dates (01-02-2023 has 2-digit day/month groups).
        Pattern("Grouped account", r"\b\d{3,}(?:[ -]\d{3,}){2,}\b", 0.6),
        # Any 8+ digit run — scores above threshold so it fires WITHOUT context.
        # No upper bound: long OCR/reference blobs (20+ digits) must be caught.
        Pattern("Digit run — long",   r"\b\d{12,}\b", 0.6),
        Pattern("Digit run — medium", r"\b\d{8,11}\b", 0.5),
    ]
    CONTEXT = [
        "account", "acct", "account number", "account no", "account #",
        "acct #", "acct no", "checking", "savings", "deposit account",
        "card", "card number", "card no", "visa", "mastercard", "amex",
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BANK_ACCOUNT",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class RoutingNumberRecognizer(PatternRecognizer):
    """Detects US ABA routing / transit numbers (9 digits, first digit 0-3)."""

    PATTERNS = [
        Pattern("ABA routing", r"\b[0-3]\d{8}\b", 0.5),
    ]
    CONTEXT = [
        "routing", "aba", "transit", "routing number", "routing #",
        "routing no", "wire routing", "ach routing",
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BANK_ROUTING",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class PartialAccountRecognizer(PatternRecognizer):
    """Detects masked account number formats banks print on statements.

    Examples: ****1234, xxxx-5678, ending in 9012, acct ...1234
    """

    PATTERNS = [
        Pattern("Asterisk-masked",  r"\*{3,}\s*\d{3,4}",                   0.9),
        Pattern("X-masked",         r"[xX]{3,}[\-\s]?\d{3,4}",             0.85),
        Pattern("Ending-in phrase", r"(?:ending|ends)\s+in\s+\d{3,4}",     0.9),
        Pattern("Dot-masked",       r"\.{3,}\s*\d{3,4}",                   0.8),
        Pattern("Dash-masked",      r"\-{3,}\s*\d{3,4}",                   0.75),
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BANK_ACCT_PARTIAL",
            patterns=self.PATTERNS,
            supported_language="en",
        )


class SocialSecurityRecognizer(PatternRecognizer):
    """High-confidence SSN recognizer.

    Presidio's built-in UsSsnRecognizer has a base score of exactly 0.5
    which falls on the boundary of the default threshold and often doesn't
    fire. This replacement uses a higher base score.
    """

    PATTERNS = [
        Pattern("SSN dashed",    r"\b\d{3}-\d{2}-\d{4}\b",       0.8),
        Pattern("SSN dotted",    r"\b\d{3}\.\d{2}\.\d{4}\b",     0.8),
        Pattern("SSN plain",     r"\b\d{9}\b",                    0.4),  # low — needs context
    ]
    CONTEXT = ["social", "security", "ssn", "ssns", "ssid", "social security"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="US_SSN",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class StreetAddressRecognizer(PatternRecognizer):
    """Detects US street-address lines: house number + street name + suffix.

    Examples: "310 14TH ST", "3418 NORTHERN BLVD", "123 Main Street".
    spaCy NER tags cities and states (GPE) but NOT street lines, so without
    this recognizer the most identifying part of an address leaks to the cloud.
    """

    # Common US street-type suffixes (full + USPS abbreviations).
    _SUFFIX = (
        r"st|street|ave|avenue|blvd|boulevard|rd|road|dr|drive|ln|lane|"
        r"ct|court|way|pl|place|pkwy|parkway|hwy|highway|ter|terrace|"
        r"cir|circle|sq|square|trl|trail|loop|run|pass|path|aly|alley|"
        r"plaza|pike|row|walk|crossing|xing"
    )

    PATTERNS = [
        # house number + up to 4 name words + a street suffix.
        # - [ \t] (NOT \s) for inter-word spacing so the match cannot span
        #   newlines (else it swallows the previous line + the next).
        # - (?<![\d/.$-]) negative lookbehind so the house number isn't really
        #   the tail of a date (10/01), an amount ($25.01 / .01) or a longer
        #   number — which would mangle transaction lines.
        Pattern(
            "Street address",
            r"(?<![\d/.$-])\b\d{1,6}[ \t]+(?:[A-Za-z0-9][A-Za-z0-9.'\-]*[ \t]+){1,4}(?:" + _SUFFIX + r")\b\.?",
            0.6,
        ),
    ]
    CONTEXT = ["address", "street", "mailing", "billing", "residence", "addr"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="ADDRESS",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class ZipCodeRecognizer(PatternRecognizer):
    """Detects US ZIP codes, prioritising distinctive forms over bare 5-digit runs.

    ZIP+4 (12345-6789) and "STATE ZIP" (CA 92648) are highly distinctive and
    score high.  A bare 5-digit run scores low and only fires with a context
    word nearby, to avoid redacting amounts and other numeric fields.
    """

    # US state / territory two-letter codes — keeps "STATE ZIP" from matching
    # arbitrary two-letter words followed by a number.
    _STATES = (
        r"al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|"
        r"mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|"
        r"vt|va|wa|wv|wi|wy|dc|pr|vi|gu"
    )

    PATTERNS = [
        Pattern("ZIP+4",      r"\b\d{5}-\d{4}\b",                               0.7),
        Pattern("State+ZIP",  r"\b(?:" + _STATES + r")\s+\d{5}(?:-\d{4})?\b",   0.75),
        Pattern("ZIP plain",  r"\b\d{5}\b",                                     0.2),
    ]
    CONTEXT = ["zip", "zipcode", "postal", "address", "city", "state"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="ZIP",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class UKPostcodeRecognizer(PatternRecognizer):
    """Detects UK postcodes (e.g. "SW1A 1AA", "M1 1AE", "YZ985XY").

    Format: 1-2 letters, a digit, an optional letter/digit, optional space,
    then a digit and two letters. US ZIP recognizers don't cover these, so
    without this UK statements leak their most location-identifying field.
    """

    PATTERNS = [
        Pattern(
            "UK postcode",
            r"\b[a-z]{1,2}\d[a-z\d]?[ ]?\d[a-z]{2}\b",
            0.55,
        ),
    ]
    CONTEXT = ["postcode", "post code", "address"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="ZIP",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class IBANRecognizer(PatternRecognizer):
    """Detects international bank account numbers (IBAN).

    Format: 2-letter country code + 2 check digits + up to 30 alphanumerics,
    often printed in 4-char groups (e.g. "GB11 RBOS 1610 0011 1111 11").
    """

    PATTERNS = [
        Pattern(
            "IBAN",
            r"\b[a-z]{2}\d{2}[ ]?(?:[a-z0-9]{2,4}[ ]?){3,8}\b",
            0.6,
        ),
    ]
    CONTEXT = ["iban", "account", "bic", "swift"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BANK_ACCOUNT",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class PhoneRecognizer(PatternRecognizer):
    """Detects US/international phone numbers in common printed formats.

    Presidio's built-in PHONE_NUMBER recognizer misses several statement
    formats (toll-free, dotted, no-separator). This supplements it. Phone
    numbers on statements may be the bank's or the customer's; we cannot tell
    them apart, so we redact both (over-redaction round-trips locally).
    """

    PATTERNS = [
        # (617) 555-1234 / 617-555-1234 / 617.555.1234 / +1 617 555 1234 /
        # (888)850-3958 (no space after the area-code paren)
        Pattern("US phone", r"(?<!\d)(?:\+?1[ \-.]?)?\(?\d{3}\)?[ \-.]?\d{3}[ \-.]\d{4}(?!\d)", 0.6),
        # 1-800-432-1000 / 1 800 432 1000
        Pattern("Toll-free", r"\b1[ \-.]?8(?:00|33|44|55|66|77|88)[ \-.]?\d{3}[ \-.]?\d{4}\b", 0.6),
        # International: +<country> then 7-14 digits with spaces/dashes
        # e.g. +44 20 7946 0958, +33 1 23 45 67 89, +91 98765 43210
        Pattern("Intl phone", r"(?<![\w])\+\d{1,3}(?:[ \-.]?\d){7,13}\b", 0.6),
    ]
    CONTEXT = ["phone", "call", "tel", "telephone", "fax", "mobile", "contact"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="PHONE_NUMBER",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class TaxIdRecognizer(PatternRecognizer):
    """Detects US employer/tax IDs (EIN "12-3456789", ITIN "9XX-XX-XXXX").

    EINs are dash-grouped 2+7 digits, so they escape the bare digit-run rule.
    Context words push the score above threshold.
    """

    PATTERNS = [
        Pattern("EIN", r"\b\d{2}-\d{7}\b", 0.4),
    ]
    CONTEXT = ["ein", "tax id", "tax i.d", "tin", "taxpayer", "employer id", "federal id"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="TAX_ID",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class SortCodeRecognizer(PatternRecognizer):
    """Detects UK sort codes ("12-34-56"). Context-gated to avoid matching dates.

    A bare NN-NN-NN run is ambiguous with dates (01-02-03), so this only fires
    when a "sort code" context word is nearby.
    """

    PATTERNS = [
        Pattern("Sort code", r"\b\d{2}-\d{2}-\d{2}\b", 0.25),
    ]
    # Individual words too — Presidio's context enhancer matches single tokens,
    # so the phrase "sort code" alone never boosts a nearby match.
    CONTEXT = ["sort code", "sortcode", "sort-code", "sort", "code"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BANK_ROUTING",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class DateOfBirthRecognizer(PatternRecognizer):
    """Detects dates of birth — but ONLY when a birth-context word is nearby.

    Transaction dates must NOT be redacted (the LLM needs them), so the bare
    date patterns score below threshold and only a "date of birth"/"DOB"/"born"
    context hit raises them enough to fire.
    """

    _MONTHS = (r"jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec")
    PATTERNS = [
        Pattern("DOB numeric", r"\b\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}\b", 0.25),
        Pattern("DOB worded",  r"\b\d{1,2}\s+(?:" + _MONTHS + r")\w*\.?\s+\d{4}\b", 0.25),
    ]
    CONTEXT = ["birth", "dob", "d.o.b", "born", "date of birth", "birthdate"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="DOB",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
            supported_language="en",
        )


class IPAddressRecognizer(PatternRecognizer):
    """Detects IPv4 addresses (e-statement login/audit info)."""

    PATTERNS = [
        Pattern(
            "IPv4",
            r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b",
            0.6,
        ),
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="IP_ADDRESS",
            patterns=self.PATTERNS,
            supported_language="en",
        )


def build_registry() -> list:
    """Return a list of all custom recognizer instances to add to the registry."""
    return [
        BankAccountNumberRecognizer(),
        RoutingNumberRecognizer(),
        PartialAccountRecognizer(),
        SocialSecurityRecognizer(),
        StreetAddressRecognizer(),
        ZipCodeRecognizer(),
        UKPostcodeRecognizer(),
        IBANRecognizer(),
        PhoneRecognizer(),
        TaxIdRecognizer(),
        SortCodeRecognizer(),
        DateOfBirthRecognizer(),
        IPAddressRecognizer(),
    ]
