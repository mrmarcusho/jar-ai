"""Cloud extraction engine: PDF → Claude API → SQL → SQLite.

Sends raw bank statement PDFs to the cloud LLM, receives SQL INSERT
statements, validates them, executes locally, and runs post-extraction
categorization.
"""

from __future__ import annotations

import base64
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # pymupdf
import anthropic

logger = logging.getLogger("jar.extraction")

from core.extraction_engine.prompts.master_extraction import build_extraction_prompt
from core.extraction_engine.prompts.hsm_analysis import (
    build_data_context,
    build_hsm_analysis_prompt,
)
from core.extraction_engine.sql_validator import (
    ValidationResult,
    load_schema_metadata,
    to_parameterized,
    validate_sql,
)

DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "jar.db"
SCHEMA_PATH = Path(__file__).resolve().parent.parent / "database" / "schema.sql"
EXTRACTION_LOG = Path(__file__).resolve().parent.parent.parent / "data" / "extraction_log.md"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


def _write_extraction_log(
    source_file: str, status: str, total_time: float,
    timings: list[tuple[str, float, str]],
):
    """Append a markdown entry to data/extraction_log.md."""
    from datetime import datetime

    EXTRACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    write_header = not EXTRACTION_LOG.exists() or EXTRACTION_LOG.stat().st_size == 0

    with open(EXTRACTION_LOG, "a") as f:
        if write_header:
            f.write("# Extraction Log\n\n")

        f.write(f"## {source_file}\n\n")
        f.write(f"- **Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- **Status:** {status}\n")
        f.write(f"- **Total time:** {total_time:.1f}s\n\n")

        f.write("| Step | Time (s) | Details |\n")
        f.write("|------|----------|----------|\n")
        for step_name, elapsed, details in timings:
            f.write(f"| {step_name} | {elapsed:.2f} | {details} |\n")
        f.write(f"| **Total** | **{total_time:.1f}** | |\n")
        f.write("\n---\n\n")


@dataclass
class ExtractionResult:
    """Result of extracting a single bank statement PDF."""
    status: str  # "success", "skipped", "error"
    source_file: str
    statement_id: str | None = None
    txn_count: int = 0
    categorized_count: int = 0
    validation_passed: bool = False
    error: str | None = None
    warnings: list[str] = field(default_factory=list)
    findings: list[dict] = field(default_factory=list)
    total_time: float = 0.0


def extract_text_with_pymupdf(pdf_path: Path) -> tuple[str, bool]:
    """Extract text from a digital PDF using PyMuPDF.

    Returns (extracted_text, is_scanned).
    is_scanned=True means the PDF has no usable text layer and needs
    the raw-PDF fallback (OCR is a future task).
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        logger.warning("PyMuPDF could not open %s, falling back to raw PDF", pdf_path)
        return "", True

    pages = []
    for page in doc:
        text = page.get_text("text")
        pages.append(text)
    doc.close()

    full_text = "\n\n--- PAGE BREAK ---\n\n".join(pages)
    is_scanned = len(full_text.strip()) < 200
    return full_text, is_scanned


def extract_statement(
    pdf_path: Path,
    db_path: Path = None,
    api_key: str = None,
    model: str = None,
    progress_callback=None,
) -> ExtractionResult:
    """End-to-end extraction: PDF → Claude → SQL → SQLite → categorize.

    Args:
        pdf_path: Path to the bank statement PDF.
        db_path: Path to the SQLite database. Defaults to data/jar.db.
        api_key: Anthropic API key. Falls back to ANTHROPIC_API_KEY env var.
        model: Claude model to use. Defaults to claude-sonnet-4-20250514.

    Returns:
        ExtractionResult with status, transaction count, and any errors.
    """
    start = time.time()
    db_path = db_path or DB_PATH
    model = model or DEFAULT_MODEL
    source_file = pdf_path.name

    result = ExtractionResult(status="error", source_file=source_file)
    _timings: list[tuple[str, float, str]] = []

    def _report(step_name, elapsed, details):
        logger.info("[%6.2fs] %s — %s", elapsed, step_name, details)
        _timings.append((step_name, elapsed, details))
        if progress_callback:
            progress_callback(step_name, elapsed, details)

    logger.info("=" * 60)
    logger.info("EXTRACTION START: %s", source_file)
    logger.info("=" * 60)

    # 1. Check API key
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        result.error = "No API key. Set ANTHROPIC_API_KEY or pass api_key="
        result.total_time = time.time() - start
        return result

    # 2. Check PDF exists
    if not pdf_path.exists():
        result.error = f"PDF not found: {pdf_path}"
        result.total_time = time.time() - start
        return result

    # 3. Check for duplicate import
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        existing = conn.execute(
            "SELECT 1 FROM statements WHERE source_file = ?", (source_file,)
        ).fetchone()
        if existing:
            result.status = "skipped"
            result.error = f"{source_file} already imported"
            result.total_time = time.time() - start
            return result

        # 4. Extract text from PDF with PyMuPDF
        t0 = time.time()
        pdf_text, is_scanned = extract_text_with_pymupdf(pdf_path)
        _report("PyMuPDF extraction", time.time() - t0,
                f"{'scanned fallback' if is_scanned else f'{len(pdf_text)} chars'}")

        # Prepare raw PDF fallback data (only used if scanned)
        pdf_b64 = None
        if is_scanned:
            logger.warning("Scanned PDF detected, falling back to raw PDF upload: %s", pdf_path)
            t0 = time.time()
            pdf_bytes = pdf_path.read_bytes()
            pdf_b64 = base64.standard_b64encode(pdf_bytes).decode("ascii")
            _report("PDF read + encode (fallback)", time.time() - t0, f"{len(pdf_bytes):,} bytes")

        # 5. Build prompt
        t0 = time.time()
        schema_sql = SCHEMA_PATH.read_text()
        system_prompt = build_extraction_prompt(schema_sql, source_file)
        _report("Prompt built", time.time() - t0, "ready")

        # 6. Call Claude for extraction
        t0 = time.time()
        client = anthropic.Anthropic(api_key=key)
        if is_scanned:
            raw_sql = _call_claude_raw_pdf(client, model, system_prompt, pdf_b64)
            _report("Claude API call", time.time() - t0, f"extraction via raw PDF fallback (model={model})")
        else:
            raw_sql = _call_claude_text(client, model, system_prompt, pdf_text)
            _report("Claude API call", time.time() - t0, f"extraction via pre-extracted text (model={model})")

        # 7. Validate SQL
        t0 = time.time()
        schema_meta = load_schema_metadata(SCHEMA_PATH)
        validation = validate_sql(raw_sql, schema_meta)
        _report("SQL validation", time.time() - t0,
               f"{len(validation.valid_statements)} valid, {len(validation.errors)} errors")

        # 8. If validation has errors, retry once
        if not validation.is_fully_valid and validation.valid_statements:
            result.warnings.append(
                f"First attempt had {len(validation.errors)} error(s), "
                f"{len(validation.valid_statements)} valid statement(s). Using partial result."
            )
        elif not validation.is_fully_valid and not validation.valid_statements:
            # Total failure — retry
            t0 = time.time()
            if is_scanned:
                retry_sql = _retry_claude_raw_pdf(client, model, system_prompt, pdf_b64, raw_sql, validation)
            else:
                retry_sql = _retry_claude_text(client, model, system_prompt, pdf_text, raw_sql, validation)
            validation = validate_sql(retry_sql, schema_meta)
            _report("Claude API retry", time.time() - t0, "re-extraction after validation failure")
            if not validation.valid_statements:
                result.error = (
                    f"Extraction failed after retry. Errors: {validation.errors[:3]}"
                )
                result.total_time = time.time() - start
                return result
            result.warnings.append("Succeeded on retry after initial failure.")

        if not validation.valid_statements:
            result.error = f"No valid SQL statements extracted. Errors: {validation.errors[:3]}"
            result.total_time = time.time() - start
            return result

        # 9. Execute SQL in a transaction
        t0 = time.time()
        txn_count = _execute_sql(conn, validation)
        _report("SQL execution", time.time() - t0, f"{txn_count} transactions inserted")

        # 10. Determine statement_id from the inserted data
        stmt_id = _find_statement_id(conn, source_file)

        # 11. Post-extraction categorization
        cat_count = 0
        if stmt_id:
            t0 = time.time()
            cat_count = _categorize_transactions(conn, stmt_id)
            _report("Categorization", time.time() - t0, f"{cat_count}/{txn_count} categorized")

        # 12. Balance verification
        v_passed = False
        if stmt_id:
            t0 = time.time()
            v_passed = _verify_balance(conn, stmt_id)
            _report("Balance verification", time.time() - t0, f"passed={v_passed}")

        # 13. HSM analysis — scan for financial insights
        findings = []
        if stmt_id:
            try:
                t0 = time.time()
                findings = _analyze_hsm(conn, stmt_id, client, model)
                _report("HSM analysis", time.time() - t0, f"{len(findings)} findings")
            except Exception as e:
                result.warnings.append(f"HSM analysis failed: {e}")
                _report("HSM analysis", time.time() - t0, f"failed: {e}")

        result.status = "success"
        result.statement_id = stmt_id
        result.txn_count = txn_count
        result.categorized_count = cat_count
        result.validation_passed = v_passed
        result.findings = findings
        result.total_time = time.time() - start
        _report("Complete", result.total_time, f"{source_file} — {txn_count} transactions")
        _write_extraction_log(source_file, result.status, result.total_time, _timings)
        return result

    except Exception as e:
        result.error = str(e)
        result.total_time = time.time() - start
        _report("Error", result.total_time, str(e))
        _write_extraction_log(source_file, result.status, result.total_time, _timings)
        return result
    finally:
        conn.close()


def _call_claude_text(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    pdf_text: str,
) -> str:
    """Send pre-extracted PDF text to Claude and get raw SQL back."""
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                "Extract all data from this bank statement and return SQL INSERT statements.\n\n"
                "--- BANK STATEMENT TEXT ---\n\n" + pdf_text
            ),
        }],
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts)


def _call_claude_raw_pdf(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    pdf_b64: str,
) -> str:
    """Send raw PDF binary to Claude (fallback for scanned PDFs)."""
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {
                    "type": "text",
                    "text": "Extract all data from this bank statement and return SQL INSERT statements.",
                },
            ],
        }],
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts)


def _retry_claude_text(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    pdf_text: str,
    previous_output: str,
    validation: ValidationResult,
) -> str:
    """Retry extraction with error feedback (text path)."""
    error_summary = "\n".join(validation.errors[:5])
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract all data from this bank statement and return SQL INSERT statements.\n\n"
                    "--- BANK STATEMENT TEXT ---\n\n" + pdf_text
                ),
            },
            {
                "role": "assistant",
                "content": previous_output,
            },
            {
                "role": "user",
                "content": (
                    f"Your SQL output had validation errors:\n{error_summary}\n\n"
                    "Please output the corrected SQL. Remember: output ONLY valid SQL "
                    "INSERT statements, no other text."
                ),
            },
        ],
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts)


def _retry_claude_raw_pdf(
    client: anthropic.Anthropic,
    model: str,
    system_prompt: str,
    pdf_b64: str,
    previous_output: str,
    validation: ValidationResult,
) -> str:
    """Retry extraction with error feedback (raw PDF fallback path)."""
    error_summary = "\n".join(validation.errors[:5])
    response = client.messages.create(
        model=model,
        max_tokens=16384,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_b64,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Extract all data from this bank statement and return SQL INSERT statements.",
                    },
                ],
            },
            {
                "role": "assistant",
                "content": previous_output,
            },
            {
                "role": "user",
                "content": (
                    f"Your SQL output had validation errors:\n{error_summary}\n\n"
                    "Please output the corrected SQL. Remember: output ONLY valid SQL "
                    "INSERT statements, no other text."
                ),
            },
        ],
    )
    text_parts = [block.text for block in response.content if block.type == "text"]
    return "\n".join(text_parts)


def _execute_sql(conn: sqlite3.Connection, validation: ValidationResult) -> int:
    """Execute validated INSERT statements in a single transaction.

    Returns the number of transaction rows inserted.
    """
    txn_count = 0
    try:
        for parsed in validation.valid_statements:
            sql, values = to_parameterized(parsed)
            conn.execute(sql, values)
            if parsed.table == "transactions":
                txn_count += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return txn_count


def _find_statement_id(conn: sqlite3.Connection, source_file: str) -> str | None:
    """Look up the statement_id for a just-inserted source_file."""
    row = conn.execute(
        "SELECT statement_id FROM statements WHERE source_file = ?",
        (source_file,),
    ).fetchone()
    return row[0] if row else None


def _categorize_transactions(conn: sqlite3.Connection, statement_id: str) -> int:
    """Run categorization on newly inserted transactions.

    Returns count of transactions that were categorized (non-uncategorized).
    """
    from core.categorization.categorizer import CategoryStore, TransactionCategorizer

    try:
        from core.categorization.user_rules import UserRulesStore
        user_rules = UserRulesStore(conn)
    except Exception:
        user_rules = None

    store = CategoryStore()
    categorizer = TransactionCategorizer(store, user_rules=user_rules)

    rows = conn.execute(
        "SELECT transaction_id, description_raw, type, ach_company_name "
        "FROM transactions WHERE statement_id = ?",
        (statement_id,),
    ).fetchall()

    categorized = 0
    for row in rows:
        txn_dict = {
            "transaction_id": row[0],
            "description_raw": row[1],
            "type": row[2],
            "parsed_fields": {"ach_company_name": row[3]},
        }
        result = categorizer.categorize(txn_dict)
        cat = result.get("category")
        sub = result.get("subcategory")
        if cat and cat != "uncategorized":
            conn.execute(
                "UPDATE transactions SET category = ?, subcategory = ? WHERE transaction_id = ?",
                (cat, sub, row[0]),
            )
            categorized += 1

    conn.commit()
    return categorized


def _verify_balance(conn: sqlite3.Connection, statement_id: str) -> bool:
    """Verify opening + deposits + withdrawals = closing (within $0.01).

    Updates the validation_passed column accordingly.
    """
    row = conn.execute(
        "SELECT opening_balance, closing_balance, total_deposits, total_withdrawals "
        "FROM statements WHERE statement_id = ?",
        (statement_id,),
    ).fetchone()
    if not row:
        return False

    opening, closing, deposits, withdrawals = row
    if deposits is None or withdrawals is None:
        return False

    expected = opening + deposits + withdrawals
    passed = abs(expected - closing) < 0.01

    conn.execute(
        "UPDATE statements SET validation_passed = ? WHERE statement_id = ?",
        (int(passed), statement_id),
    )
    conn.commit()
    return passed


# ── HSM Analysis ─────────────────────────────────────────────────


def _ensure_findings_table(conn: sqlite3.Connection) -> None:
    """Create the findings table if it doesn't exist (for existing DBs)."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            finding_id      TEXT PRIMARY KEY,
            statement_id    TEXT NOT NULL REFERENCES statements(statement_id),
            hsm_id          TEXT NOT NULL,
            domain          TEXT NOT NULL,
            title           TEXT NOT NULL,
            description     TEXT NOT NULL,
            amount_impact   REAL,
            severity        TEXT NOT NULL DEFAULT 'warning',
            merchant        TEXT,
            dismissed       INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


_HSM_DOMAIN_MAP = {
    "A": "subscriptions",
    "B": "promotional_traps",
    "C": "banking",
    "D": "retirement",
    "E": "tax",
    "F": "insurance",
    "G": "utilities",
    "H": "debt",
    "I": "housing",
    "J": "education",
    "K": "travel",
    "L": "income",
    "M": "security",
    "N": "spending_trends",
}


def _query_hsm_context(conn: sqlite3.Connection, statement_id: str) -> dict:
    """Query the DB for all data needed by the HSM analysis prompt.

    Returns a dict with keys: current_stmt, current_txns,
    recurring_history, fee_txns, income_history, category_trends,
    merchant_frequency, balance_history.
    """
    # Current statement info
    row = conn.execute(
        "SELECT statement_id, account_id, period_start, period_end, "
        "opening_balance, closing_balance, total_deposits, total_withdrawals "
        "FROM statements WHERE statement_id = ?",
        (statement_id,),
    ).fetchone()
    current_stmt = {
        "statement_id": row[0],
        "account_id": row[1],
        "period_start": row[2],
        "period_end": row[3],
        "opening_balance": row[4],
        "closing_balance": row[5],
        "total_deposits": row[6],
        "total_withdrawals": row[7],
    }

    # Current statement transactions
    txn_rows = conn.execute(
        "SELECT date, description_raw, amount, type, ach_company_name "
        "FROM transactions WHERE statement_id = ? ORDER BY date",
        (statement_id,),
    ).fetchall()
    current_txns = [
        {
            "date": r[0],
            "description_raw": r[1],
            "amount": r[2],
            "type": r[3],
            "ach_company_name": r[4],
        }
        for r in txn_rows
    ]

    # Recurring charge history — merchants appearing in 2+ statement periods
    recurring_rows = conn.execute(
        """
        SELECT merchant, GROUP_CONCAT(period_end, '|') as periods,
               GROUP_CONCAT(avg_amount, '|') as amounts, COUNT(*) as months_seen
        FROM (
            SELECT
                COALESCE(t.ach_company_name,
                    SUBSTR(t.description_raw, 1, 30)) as merchant,
                s.period_end,
                ROUND(AVG(t.amount), 2) as avg_amount
            FROM transactions t
            JOIN statements s ON t.statement_id = s.statement_id
            WHERE t.is_deposit = 0 AND t.type != 'transfer'
                AND (t.ach_company_name IS NOT NULL
                     OR LENGTH(t.description_raw) > 5)
            GROUP BY merchant, s.period_end
        )
        GROUP BY merchant
        HAVING COUNT(*) >= 2
        ORDER BY months_seen DESC
        LIMIT 50
        """
    ).fetchall()

    recurring_history = []
    for r in recurring_rows:
        amounts = [float(a) for a in r[2].split("|")]
        recurring_history.append({
            "merchant": r[0],
            "months_seen": r[3],
            "amounts": amounts,
        })

    # Fee transactions — sorted ASC by date so cascade clusters are visible
    fee_rows = conn.execute(
        """
        SELECT t.date, t.description_raw, t.amount, s.period_end
        FROM transactions t
        JOIN statements s ON t.statement_id = s.statement_id
        WHERE t.type = 'fee'
        ORDER BY t.date ASC
        LIMIT 100
        """
    ).fetchall()
    fee_txns = [
        {
            "date": r[0],
            "description_raw": r[1],
            "amount": r[2],
            "period_end": r[3],
        }
        for r in fee_rows
    ]

    # Income history — recurring deposit sources across statements (L01/L02)
    income_rows = conn.execute(
        """
        SELECT source, GROUP_CONCAT(period_end, '|') as periods,
               GROUP_CONCAT(total_amount, '|') as amounts, COUNT(*) as months_seen
        FROM (
            SELECT
                COALESCE(t.ach_company_name,
                    SUBSTR(t.description_raw, 1, 30)) as source,
                s.period_end,
                ROUND(SUM(t.amount), 2) as total_amount
            FROM transactions t
            JOIN statements s ON t.statement_id = s.statement_id
            WHERE t.is_deposit = 1
                AND t.type IN ('direct_deposit', 'ach_credit')
                AND (t.ach_company_name IS NOT NULL
                     OR LENGTH(t.description_raw) > 5)
            GROUP BY source, s.period_end
        )
        GROUP BY source
        HAVING COUNT(*) >= 2
        ORDER BY months_seen DESC
        LIMIT 20
        """
    ).fetchall()

    income_history = []
    for r in income_rows:
        amounts = [float(a) for a in r[2].split("|")]
        income_history.append({
            "source": r[0],
            "months_seen": r[3],
            "amounts": amounts,
        })

    # Category spending trends by merchant per period (N01)
    trend_rows = conn.execute(
        """
        SELECT
            COALESCE(t.ach_company_name,
                SUBSTR(t.description_raw, 1, 30)) as merchant,
            s.period_end as period,
            ROUND(SUM(ABS(t.amount)), 2) as total,
            COUNT(*) as cnt
        FROM transactions t
        JOIN statements s ON t.statement_id = s.statement_id
        WHERE t.is_deposit = 0 AND t.type NOT IN ('transfer', 'fee')
            AND (t.ach_company_name IS NOT NULL
                 OR LENGTH(t.description_raw) > 5)
        GROUP BY merchant, period
        ORDER BY merchant, period
        LIMIT 200
        """
    ).fetchall()

    category_trends = [
        {"merchant": r[0], "period": r[1], "total": r[2], "count": r[3]}
        for r in trend_rows
    ]

    # Merchant frequency — merchants with 3+ txns in any single period (N02)
    freq_rows = conn.execute(
        """
        SELECT merchant, period, cnt, total FROM (
            SELECT
                COALESCE(t.ach_company_name,
                    SUBSTR(t.description_raw, 1, 30)) as merchant,
                s.period_end as period,
                COUNT(*) as cnt,
                ROUND(SUM(ABS(t.amount)), 2) as total
            FROM transactions t
            JOIN statements s ON t.statement_id = s.statement_id
            WHERE t.is_deposit = 0
                AND (t.ach_company_name IS NOT NULL
                     OR LENGTH(t.description_raw) > 5)
            GROUP BY merchant, period
        )
        WHERE cnt >= 3
        ORDER BY merchant, period
        LIMIT 100
        """
    ).fetchall()

    merchant_frequency = [
        {"merchant": r[0], "period": r[1], "count": r[2], "total": r[3]}
        for r in freq_rows
    ]

    # Balance history — all statement balances for idle cash detection (C07)
    balance_rows = conn.execute(
        "SELECT period_end, opening_balance, closing_balance "
        "FROM statements ORDER BY period_end"
    ).fetchall()

    balance_history = [
        {"period_end": r[0], "opening_balance": r[1], "closing_balance": r[2]}
        for r in balance_rows
    ]

    return {
        "current_stmt": current_stmt,
        "current_txns": current_txns,
        "recurring_history": recurring_history,
        "fee_txns": fee_txns,
        "income_history": income_history,
        "category_trends": category_trends,
        "merchant_frequency": merchant_frequency,
        "balance_history": balance_history,
    }


def _analyze_hsm(
    conn: sqlite3.Connection,
    statement_id: str,
    client: "anthropic.Anthropic",
    model: str,
) -> list[dict]:
    """Run Phase I HSM analysis on extracted data.

    Makes a second Claude API call with transaction context,
    parses JSON findings, stores them in the findings table.

    Returns list of finding dicts for the API response.
    """
    import json as _json

    _ensure_findings_table(conn)

    # Build context from DB
    ctx = _query_hsm_context(conn, statement_id)

    system_prompt = build_hsm_analysis_prompt()
    data_context = build_data_context(
        ctx["current_stmt"],
        ctx["current_txns"],
        ctx["recurring_history"],
        ctx["fee_txns"],
        income_history=ctx["income_history"],
        category_trends=ctx["category_trends"],
        merchant_frequency=ctx["merchant_frequency"],
        balance_history=ctx["balance_history"],
    )

    # Call Claude for HSM analysis
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        system=system_prompt,
        messages=[{
            "role": "user",
            "content": (
                "Analyze the following bank statement data and identify any "
                "Phase I HSMs. Return ONLY a JSON array.\n\n" + data_context
            ),
        }],
    )

    raw_text = ""
    for block in response.content:
        if block.type == "text":
            raw_text += block.text

    # Parse JSON — strip markdown fences if present
    raw_text = raw_text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

    findings_list = _json.loads(raw_text)
    if not isinstance(findings_list, list):
        return []

    # Store findings in DB
    stored = []
    for i, f in enumerate(findings_list):
        hsm_id = f.get("hsm_id", "UNK")
        domain_letter = hsm_id[0] if hsm_id else "U"
        domain = _HSM_DOMAIN_MAP.get(domain_letter, "other")

        finding_id = f"{statement_id}_hsm_{hsm_id}_{i:02d}"

        finding = {
            "finding_id": finding_id,
            "statement_id": statement_id,
            "hsm_id": hsm_id,
            "domain": domain,
            "title": f.get("title", "Finding"),
            "description": f.get("description", ""),
            "amount_impact": f.get("amount_impact"),
            "severity": f.get("severity", "warning"),
            "merchant": f.get("merchant"),
        }

        conn.execute(
            "INSERT OR IGNORE INTO findings "
            "(finding_id, statement_id, hsm_id, domain, title, description, "
            "amount_impact, severity, merchant, dismissed) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                finding["finding_id"],
                finding["statement_id"],
                finding["hsm_id"],
                finding["domain"],
                finding["title"],
                finding["description"],
                finding["amount_impact"],
                finding["severity"],
                finding["merchant"],
            ),
        )
        stored.append(finding)

    conn.commit()
    return stored
