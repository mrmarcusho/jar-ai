"""HouseholdProfile — user identity seed for Pass 1 redaction.

Stored in the household_profile SQLite table. Loaded once per extraction
session and used to guarantee the account holder's own name variants are
always redacted, even if the NER model misses them.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field


@dataclass
class HouseholdProfile:
    first_name: str = ""
    last_name: str = ""
    aliases: list[str] = field(default_factory=list)   # nicknames, maiden name, etc.
    partner_first: str = ""
    partner_last: str = ""
    street: str = ""    # street address (e.g. "123 Main St")
    city: str = ""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "HouseholdProfile":
        """Load from SQLite. Returns an empty profile if table or rows don't exist."""
        try:
            rows = conn.execute(
                "SELECT field_type, value FROM household_profile"
            ).fetchall()
        except sqlite3.OperationalError:
            return cls()

        data: dict[str, list[str]] = {}
        for field_type, value in rows:
            data.setdefault(field_type, []).append(value)

        return cls(
            first_name=data.get("holder_first", [""])[0],
            last_name=data.get("holder_last", [""])[0],
            aliases=list(data.get("holder_alias", [])),
            partner_first=data.get("partner_first", [""])[0],
            partner_last=data.get("partner_last", [""])[0],
            street=data.get("street", [""])[0],
            city=data.get("city", [""])[0],
        )

    def save(self, conn: sqlite3.Connection) -> None:
        """Upsert all profile fields, replacing any existing rows."""
        conn.execute("DELETE FROM household_profile")
        rows: list[tuple[str, str]] = []
        if self.first_name:
            rows.append(("holder_first", self.first_name))
        if self.last_name:
            rows.append(("holder_last", self.last_name))
        for alias in self.aliases:
            if alias:
                rows.append(("holder_alias", alias))
        if self.partner_first:
            rows.append(("partner_first", self.partner_first))
        if self.partner_last:
            rows.append(("partner_last", self.partner_last))
        if self.street:
            rows.append(("street", self.street))
        if self.city:
            rows.append(("city", self.city))
        conn.executemany(
            "INSERT INTO household_profile (field_type, value) VALUES (?, ?)", rows
        )
        conn.commit()

    def is_configured(self) -> bool:
        """True if at least a first or last name has been provided."""
        return bool(self.first_name or self.last_name)

    def to_dict(self) -> dict:
        return {
            "first_name": self.first_name,
            "last_name": self.last_name,
            "aliases": self.aliases,
            "partner_first": self.partner_first,
            "partner_last": self.partner_last,
            "street": self.street,
            "city": self.city,
        }

    # ------------------------------------------------------------------
    # Name variant generation
    # ------------------------------------------------------------------

    def all_name_variants(self) -> list[tuple[str, str]]:
        """Return (variant_string, token) pairs for Pass 1 regex matching.

        Variants are sorted longest-first so more specific patterns are
        tried before shorter substrings (e.g. "Marcus Ho" before "Ho").
        """
        variants: list[tuple[str, str]] = []

        def add(name: str, token: str) -> None:
            name = name.strip()
            if name:
                variants.append((name, token))

        fn = self.first_name.strip()
        ln = self.last_name.strip()

        if fn and ln:
            add(f"{fn} {ln}", "[ACCT_HOLDER]")          # Marcus Ho
            add(f"{ln} {fn}", "[ACCT_HOLDER]")          # Ho Marcus
            add(f"{ln}/{fn}", "[ACCT_HOLDER]")          # Ho/Marcus  (ACH format)
            add(f"{ln}, {fn}", "[ACCT_HOLDER]")         # Ho, Marcus
            add(f"{fn[0]} {ln}", "[ACCT_HOLDER]")       # M Ho
            add(f"{fn[0]}. {ln}", "[ACCT_HOLDER]")      # M. Ho
            add(f"{fn[0]}.{ln}", "[ACCT_HOLDER]")       # M.Ho

        if ln:
            add(ln, "[ACCT_HOLDER]")

        # First name only if long enough to avoid false positives
        if len(fn) >= 4:
            add(fn, "[ACCT_HOLDER]")

        for alias in self.aliases:
            add(alias, "[ACCT_HOLDER]")

        # Partner
        pfn = self.partner_first.strip()
        pln = self.partner_last.strip()

        if pfn and pln:
            add(f"{pfn} {pln}", "[PARTNER]")
            add(f"{pln} {pfn}", "[PARTNER]")
            add(f"{pln}/{pfn}", "[PARTNER]")
            add(f"{pfn[0]} {pln}", "[PARTNER]")
            add(f"{pfn[0]}. {pln}", "[PARTNER]")

        if pln:
            add(pln, "[PARTNER]")

        if len(pfn) >= 4:
            add(pfn, "[PARTNER]")

        # Address
        if self.street:
            add(self.street, "[ADDRESS]")
        if self.city:
            add(self.city, "[CITY]")

        # Longest variants first so "Marcus Ho" matches before "Ho"
        variants.sort(key=lambda x: len(x[0]), reverse=True)
        return variants
