"""Validate LLM-generated SQL before execution.

Parses INSERT statements returned by the extraction LLM, validates them
against the JAR database schema, and converts them to parameterized form
for safe SQLite execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


# Tables the LLM is allowed to INSERT into
ALLOWED_TABLES = {"accounts", "statements", "transactions"}

# SQL keywords that must never appear in extraction output
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|UPDATE|ALTER|CREATE|TRUNCATE|REPLACE|ATTACH|DETACH|PRAGMA|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "database" / "schema.sql"


@dataclass
class ParsedInsert:
    """A validated INSERT statement ready for parameterized execution."""
    table: str
    columns: list[str]
    values: list  # list of Python values (str, int, float, None)
    raw_sql: str
    on_conflict: str | None = None  # e.g. "DO UPDATE SET bank_name = excluded.bank_name, ..."


@dataclass
class ValidationResult:
    """Result of validating a block of LLM-generated SQL."""
    valid_statements: list[ParsedInsert] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    rejected_statements: list[str] = field(default_factory=list)

    @property
    def is_fully_valid(self) -> bool:
        return len(self.errors) == 0

    @property
    def total_parsed(self) -> int:
        return len(self.valid_statements) + len(self.rejected_statements)


def load_schema_metadata(schema_path: Path = None) -> dict[str, list[str]]:
    """Parse CREATE TABLE statements from schema.sql.

    Returns:
        Dict mapping table name to list of column names.
        e.g. {"accounts": ["account_id", "bank_name", ...]}
    """
    path = schema_path or SCHEMA_PATH
    sql = path.read_text()

    schema = {}
    # Match CREATE TABLE blocks
    table_pattern = re.compile(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\);",
        re.IGNORECASE | re.DOTALL,
    )
    for match in table_pattern.finditer(sql):
        table_name = match.group(1)
        body = match.group(2)
        columns = []
        for line in body.split("\n"):
            line = line.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            # Skip constraints (FOREIGN KEY, PRIMARY KEY as standalone, etc.)
            first_word = line.split()[0].upper() if line.split() else ""
            if first_word in ("FOREIGN", "PRIMARY", "UNIQUE", "CHECK", "CONSTRAINT"):
                continue
            # Column name is the first token
            col_name = line.split()[0]
            if col_name.upper() not in ("CREATE", "TABLE", "IF", "NOT", "EXISTS"):
                columns.append(col_name)
        schema[table_name] = columns

    return schema


def _strip_sql_comments(sql_text: str) -> str:
    """Remove SQL comments (-- line comments and /* block comments */)."""
    # Remove block comments
    sql_text = re.sub(r"/\*.*?\*/", "", sql_text, flags=re.DOTALL)
    # Remove line comments
    sql_text = re.sub(r"--[^\n]*", "", sql_text)
    return sql_text


def _split_statements(sql_text: str) -> list[str]:
    """Split SQL text into individual statements, respecting quoted strings."""
    statements = []
    current = []
    in_single_quote = False
    i = 0
    while i < len(sql_text):
        ch = sql_text[i]
        if ch == "'" and not in_single_quote:
            in_single_quote = True
            current.append(ch)
        elif ch == "'" and in_single_quote:
            # Check for escaped quote ('')
            if i + 1 < len(sql_text) and sql_text[i + 1] == "'":
                current.append("''")
                i += 1
            else:
                in_single_quote = False
                current.append(ch)
        elif ch == ";" and not in_single_quote:
            stmt = "".join(current).strip()
            if stmt:
                statements.append(stmt)
            current = []
        else:
            current.append(ch)
        i += 1
    # Handle last statement without trailing semicolon
    remaining = "".join(current).strip()
    if remaining:
        statements.append(remaining)
    return statements


def _parse_value(raw: str):
    """Parse a single SQL value literal into a Python value."""
    raw = raw.strip()
    if raw.upper() == "NULL":
        return None
    # Quoted string
    if raw.startswith("'") and raw.endswith("'"):
        inner = raw[1:-1]
        # Unescape doubled single quotes
        return inner.replace("''", "'")
    # Numeric
    try:
        if "." in raw:
            return float(raw)
        return int(raw)
    except ValueError:
        pass
    # Negative numeric
    if raw.startswith("-"):
        try:
            if "." in raw:
                return float(raw)
            return int(raw)
        except ValueError:
            pass
    return raw


def _parse_values_list(values_str: str) -> list:
    """Parse the VALUES (...) portion into a list of Python values."""
    values_str = values_str.strip()
    if values_str.startswith("(") and values_str.endswith(")"):
        values_str = values_str[1:-1]

    values = []
    current = []
    in_quote = False
    depth = 0
    i = 0

    while i < len(values_str):
        ch = values_str[i]
        if ch == "'" and not in_quote:
            in_quote = True
            current.append(ch)
        elif ch == "'" and in_quote:
            # Check for escaped quote ('')
            if i + 1 < len(values_str) and values_str[i + 1] == "'":
                current.append("''")
                i += 2  # skip both quotes
                continue
            else:
                in_quote = False
                current.append(ch)
        elif ch == "(" and not in_quote:
            depth += 1
            current.append(ch)
        elif ch == ")" and not in_quote:
            depth -= 1
            current.append(ch)
        elif ch == "," and not in_quote and depth == 0:
            values.append(_parse_value("".join(current)))
            current = []
        else:
            current.append(ch)
        i += 1

    if current:
        values.append(_parse_value("".join(current)))

    return values


def parse_insert(sql: str) -> ParsedInsert:
    """Parse a single INSERT statement into structured form.

    Supports:
        INSERT INTO table (cols) VALUES (vals);
        INSERT OR IGNORE INTO table (cols) VALUES (vals);
        INSERT INTO table (cols) VALUES (vals) ON CONFLICT(...) DO UPDATE SET ...;

    Raises ValueError if not a valid INSERT.
    """
    sql_upper = sql.strip().upper()

    # Must start with INSERT
    if not sql_upper.startswith("INSERT"):
        raise ValueError(f"Not an INSERT statement: {sql[:50]}...")

    # Check for forbidden keywords (excluding INSERT itself)
    remainder = sql[6:]  # skip "INSERT"
    if FORBIDDEN_KEYWORDS.search(remainder.split("VALUES")[0] if "VALUES" in remainder.upper() else ""):
        raise ValueError(f"Forbidden SQL keyword detected: {sql[:80]}...")

    # Extract ON CONFLICT clause if present
    on_conflict = None
    on_conflict_match = re.search(
        r"\)\s*(ON\s+CONFLICT\s*\(.*?\)\s*DO\s+UPDATE\s+SET\s+.+)$",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    sql_before_conflict = sql
    if on_conflict_match:
        on_conflict = on_conflict_match.group(1).strip()
        sql_before_conflict = sql[:on_conflict_match.start() + 1]  # keep the closing paren of VALUES

    # Parse: INSERT [OR IGNORE] INTO table (col1, col2, ...) VALUES (val1, val2, ...)
    pattern = re.compile(
        r"INSERT\s+(?:OR\s+IGNORE\s+)?INTO\s+(\w+)\s*\(([^)]+)\)\s*VALUES\s*\((.+)\)\s*$",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.match(sql_before_conflict.strip())
    if not match:
        raise ValueError(f"Could not parse INSERT statement: {sql[:80]}...")

    table = match.group(1).lower()
    columns_str = match.group(2)
    values_str = match.group(3)

    columns = [c.strip().lower() for c in columns_str.split(",")]
    values = _parse_values_list(f"({values_str})")

    return ParsedInsert(
        table=table,
        columns=columns,
        values=values,
        raw_sql=sql,
        on_conflict=on_conflict,
    )


def validate_sql(sql_text: str, schema_meta: dict = None) -> ValidationResult:
    """Validate a block of SQL text returned by the LLM.

    Args:
        sql_text: Raw SQL text from the LLM (may contain multiple statements).
        schema_meta: Dict from load_schema_metadata(). Loaded from default path if None.

    Returns:
        ValidationResult with valid_statements, errors, and rejected_statements.
    """
    if schema_meta is None:
        schema_meta = load_schema_metadata()

    result = ValidationResult()

    # Check for forbidden keywords in the full text
    # Strip ON CONFLICT ... DO UPDATE SET clauses first (these are legitimate)
    text_for_check = re.sub(
        r"ON\s+CONFLICT\s*\([^)]*\)\s*DO\s+UPDATE\s+SET\s+[^;]+",
        "",
        sql_text,
        flags=re.IGNORECASE,
    )
    for forbidden_match in FORBIDDEN_KEYWORDS.finditer(text_for_check):
        keyword = forbidden_match.group(0)
        # Only flag if outside a string literal (count preceding quotes)
        before = text_for_check[:forbidden_match.start()]
        single_quotes = before.count("'") - before.count("\\'")
        if single_quotes % 2 == 0:
            result.errors.append(f"Forbidden SQL keyword '{keyword}' found outside string literal")

    # Strip comments and markdown fences
    cleaned = _strip_sql_comments(sql_text)
    cleaned = re.sub(r"```\w*\n?", "", cleaned)  # remove markdown code fences
    cleaned = cleaned.strip()

    if not cleaned:
        result.errors.append("Empty SQL output")
        return result

    statements = _split_statements(cleaned)

    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue

        # Skip any non-INSERT statements
        if not stmt.upper().startswith("INSERT"):
            result.errors.append(f"Non-INSERT statement rejected: {stmt[:60]}...")
            result.rejected_statements.append(stmt)
            continue

        try:
            parsed = parse_insert(stmt)
        except ValueError as e:
            result.errors.append(str(e))
            result.rejected_statements.append(stmt)
            continue

        # Validate table name
        if parsed.table not in ALLOWED_TABLES:
            result.errors.append(f"Table '{parsed.table}' not in allowed set {ALLOWED_TABLES}")
            result.rejected_statements.append(stmt)
            continue

        # Validate column names
        valid_columns = set(schema_meta.get(parsed.table, []))
        unknown_cols = [c for c in parsed.columns if c not in valid_columns]
        if unknown_cols:
            result.errors.append(
                f"Unknown column(s) {unknown_cols} for table '{parsed.table}'. "
                f"Valid: {sorted(valid_columns)}"
            )
            result.rejected_statements.append(stmt)
            continue

        # Validate column count matches value count
        if len(parsed.columns) != len(parsed.values):
            result.errors.append(
                f"Column count ({len(parsed.columns)}) != value count ({len(parsed.values)}) "
                f"for table '{parsed.table}'"
            )
            result.rejected_statements.append(stmt)
            continue

        result.valid_statements.append(parsed)

    return result


def to_parameterized(parsed: ParsedInsert) -> tuple[str, tuple]:
    """Convert a ParsedInsert to parameterized SQL for safe execution.

    Returns:
        (sql_template, values_tuple) ready for cursor.execute().
    """
    placeholders = ", ".join("?" for _ in parsed.columns)
    cols = ", ".join(parsed.columns)

    if parsed.on_conflict:
        sql = f"INSERT INTO {parsed.table} ({cols}) VALUES ({placeholders}) {parsed.on_conflict}"
        # Replace "excluded.col" references — these are SQLite-native and safe
    elif "OR IGNORE" in parsed.raw_sql.upper():
        sql = f"INSERT OR IGNORE INTO {parsed.table} ({cols}) VALUES ({placeholders})"
    else:
        sql = f"INSERT INTO {parsed.table} ({cols}) VALUES ({placeholders})"

    return sql, tuple(parsed.values)
