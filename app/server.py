"""
Jar — local web interface for bank statement extraction + chat.

Usage:
    cd JAR
    python app/server.py
    # → Jar running at http://localhost:5000
"""

import asyncio
import csv
import io
import json
import logging
import os
import queue
import sqlite3
import sys
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Query, UploadFile

# Configure extraction logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "jar.db"
STATEMENTS_DIR = PROJECT_ROOT / "data" / "statements"

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="Jar", version="0.1.0")

# Serve static files
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def _has_data() -> bool:
    """Check if any statements are loaded in the database."""
    try:
        conn = get_db()
        count = conn.execute("SELECT COUNT(*) FROM statements").fetchone()[0]
        conn.close()
        return count > 0
    except Exception:
        return False


@app.get("/")
async def index():
    return RedirectResponse("/dashboard")


@app.get("/api/config")
async def api_config():
    """App config: data status + LLM availability."""
    anthropic_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))

    return {
        "has_data": _has_data(),
        "llm_available": anthropic_key_set,
        "anthropic_key_set": anthropic_key_set,
    }


@app.get("/chat")
async def chat_page():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/scan")
async def scan_page():
    return FileResponse(str(STATIC_DIR / "scan.html"))


@app.get("/dashboard")
async def dashboard_page():
    return FileResponse(str(STATIC_DIR / "dashboard.html"))


# ---------------------------------------------------------------------------
# API: Import
# ---------------------------------------------------------------------------

@app.post("/api/import")
async def import_pdf(file: UploadFile = File(...)):
    """Accept a PDF upload, extract via cloud LLM, stream progress via SSE."""
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Only PDF files are accepted"}, status_code=400)

    STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    pdf_path = STATEMENTS_DIR / file.filename
    content = await file.read()
    pdf_path.write_bytes(content)

    progress_queue: queue.Queue = queue.Queue()

    def on_progress(step_name, elapsed, details):
        progress_queue.put(json.dumps({
            "type": "log", "step": step_name,
            "time": round(elapsed, 2), "details": details,
        }))

    async def event_stream():
        from core.extraction_engine.extract import extract_statement

        progress_queue.put(json.dumps({
            "type": "log", "step": "Upload",
            "time": 0.0, "details": f"Saved {file.filename} ({len(content):,} bytes)",
        }))

        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(
            None, lambda: extract_statement(pdf_path, DB_PATH, progress_callback=on_progress)
        )

        while not task.done():
            try:
                msg = progress_queue.get_nowait()
                yield f"data: {msg}\n\n"
            except queue.Empty:
                await asyncio.sleep(0.1)

        # Drain remaining messages
        while not progress_queue.empty():
            msg = progress_queue.get_nowait()
            yield f"data: {msg}\n\n"

        result = task.result()

        # Build final result payload
        steps = [{"step": "upload", "status": "ok", "message": f"Saved {file.filename} ({len(content):,} bytes)"}]

        if result.status == "skipped":
            steps.append({"step": "extract", "status": "skipped", "message": result.error})
            yield f"data: {json.dumps({'type': 'done', 'status': 'ok', 'steps': steps})}\n\n"
            return

        if result.status == "error":
            steps.append({"step": "extract", "status": "error", "message": result.error})
            yield f"data: {json.dumps({'type': 'done', 'status': 'error', 'steps': steps, 'error': result.error})}\n\n"
            return

        # Success
        steps.append({"step": "extract", "status": "ok", "message": f"Extracted {result.txn_count} transactions"})
        steps.append({"step": "categorize", "status": "ok", "message": f"Categorized {result.categorized_count}/{result.txn_count} transactions"})
        vfy_msg = "Balance verification passed" if result.validation_passed else "Balance verification failed"
        steps.append({"step": "verify", "status": "ok" if result.validation_passed else "warning", "message": vfy_msg})

        # Query statement details for the UI summary
        summary = {
            "statement_id": result.statement_id,
            "txn_count": result.txn_count,
            "categorized_count": result.categorized_count,
            "validation_passed": result.validation_passed,
            "total_time": round(result.total_time, 1),
            "warnings": result.warnings,
        }
        if result.statement_id:
            conn = get_db()
            row = conn.execute(
                "SELECT period_start, period_end, opening_balance, closing_balance, "
                "total_deposits, total_withdrawals, num_deposits, num_withdrawals "
                "FROM statements WHERE statement_id = ?",
                (result.statement_id,),
            ).fetchone()
            if row:
                summary["period_start"] = row[0]
                summary["period_end"] = row[1]
                summary["opening_balance"] = row[2]
                summary["closing_balance"] = row[3]
                summary["total_deposits"] = row[4]
                summary["total_withdrawals"] = row[5]
                summary["num_deposits"] = row[6]
                summary["num_withdrawals"] = row[7]

        # Include HSM findings in the response
        findings = []
        if result.findings:
            findings = result.findings
            steps.append({
                "step": "analyze",
                "status": "ok",
                "message": f"Found {len(findings)} financial insight(s)",
            })
        else:
            steps.append({
                "step": "analyze",
                "status": "ok",
                "message": "No new findings detected",
            })

        yield f"data: {json.dumps({'type': 'done', 'status': 'ok', 'steps': steps, 'summary': summary, 'findings': findings})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# API: User Tagging & Rules
# ---------------------------------------------------------------------------

def _get_user_rules_store():
    from core.categorization.user_rules import UserRulesStore
    conn = get_db()
    return conn, UserRulesStore(conn)


@app.get("/api/transactions/uncategorized")
async def get_uncategorized_transactions(limit: int = 200):
    """Uncategorized transactions with suggested keywords."""
    from core.categorization.user_rules import UserRulesStore

    conn = get_db()
    rows = conn.execute(
        "SELECT t.transaction_id, t.date, t.description_raw, t.amount, "
        "t.is_deposit, t.type, t.category, t.subcategory, "
        "t.ach_company_name, t.ach_description "
        "FROM transactions t "
        "WHERE t.category IS NULL OR t.category = 'uncategorized' "
        "ORDER BY t.date DESC LIMIT ?",
        (limit,),
    ).fetchall()

    count = conn.execute(
        "SELECT COUNT(*) FROM transactions "
        "WHERE category IS NULL OR category = 'uncategorized'"
    ).fetchone()[0]
    conn.close()

    txns = []
    for r in rows:
        d = dict(r)
        d["suggested_keyword"] = UserRulesStore.extract_keyword(
            d.get("description_raw"), d.get("ach_company_name")
        )
        txns.append(d)

    return {"transactions": txns, "count": count}


@app.get("/api/categories")
async def get_categories():
    """Full taxonomy for dropdown (grouped by category)."""
    from core.categorization.categorizer import CategoryStore
    store = CategoryStore()
    cats = store.categories
    result = {}
    for cat_key, cat_data in cats.items():
        subs = {}
        for sub_key, sub_data in cat_data.get("subcategories", {}).items():
            subs[sub_key] = sub_data["label"]
        result[cat_key] = {"label": cat_data["label"], "subcategories": subs}
    return {"categories": result}


@app.post("/api/transactions/{txn_id}/tag")
async def tag_transaction(txn_id: str, body: dict):
    """Save a user tag, optionally create a rule, and retroactively apply."""
    from core.categorization.user_rules import UserRulesStore

    category = body.get("category")
    subcategory = body.get("subcategory")
    if not category:
        return JSONResponse({"error": "category is required"}, status_code=400)

    household_tag = body.get("household_tag")
    budget_type = body.get("budget_type")
    custom_tags = body.get("custom_tags")
    create_rule = body.get("create_rule", False)
    keyword = body.get("keyword")

    conn = get_db()
    store = UserRulesStore(conn)

    try:
        # 1. Save override
        store.add_override(txn_id, category, subcategory, household_tag, budget_type, custom_tags)

        # 2. Update the transaction row itself
        conn.execute(
            "UPDATE transactions SET category = ?, subcategory = ? WHERE transaction_id = ?",
            (category, subcategory, txn_id),
        )
        conn.commit()

        rule_created = False
        similar_updated = 0
        rule_keyword = None

        # 3. Optionally create a rule and apply retroactively
        if create_rule and keyword:
            rule_keyword = keyword
            # Get source description for the rule
            row = conn.execute(
                "SELECT description_raw FROM transactions WHERE transaction_id = ?",
                (txn_id,),
            ).fetchone()
            source_desc = row["description_raw"] if row else None

            rule_id = store.add_rule(
                keyword=keyword,
                category=category,
                subcategory=subcategory,
                source_description=source_desc,
                source_transaction_id=txn_id,
            )
            similar_updated = store.apply_rule_retroactively(rule_id)
            rule_created = True

        conn.close()
        return {
            "status": "ok",
            "rule_created": rule_created,
            "similar_updated": similar_updated,
            "keyword": rule_keyword,
        }
    except Exception as e:
        conn.close()
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/transactions/batch-tag")
async def batch_tag_transactions(body: dict):
    """Tag multiple transactions with the same category."""
    from core.categorization.user_rules import UserRulesStore

    txn_ids = body.get("transaction_ids", [])
    category = body.get("category")
    subcategory = body.get("subcategory")
    if not txn_ids or not category:
        return JSONResponse({"error": "transaction_ids and category required"}, status_code=400)

    conn = get_db()
    store = UserRulesStore(conn)

    updated = 0
    for txn_id in txn_ids:
        store.add_override(txn_id, category, subcategory)
        conn.execute(
            "UPDATE transactions SET category = ?, subcategory = ? WHERE transaction_id = ?",
            (category, subcategory, txn_id),
        )
        updated += 1
    conn.commit()
    conn.close()

    return {"status": "ok", "updated": updated}


@app.get("/api/category-rules/user")
async def get_user_rules():
    """List all user-created rules."""
    conn, store = _get_user_rules_store()
    rules = store.get_rules(active_only=False)
    conn.close()
    return {"rules": rules}


@app.delete("/api/category-rules/user/{rule_id}")
async def delete_user_rule(rule_id: int):
    conn, store = _get_user_rules_store()
    store.delete_rule(rule_id)
    conn.close()
    return {"status": "ok"}


@app.put("/api/category-rules/user/{rule_id}")
async def update_user_rule(rule_id: int, body: dict):
    conn, store = _get_user_rules_store()
    store.update_rule(rule_id, **body)
    conn.close()
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API: Transactions
# ---------------------------------------------------------------------------

@app.get("/api/transactions")
async def get_transactions(
    start_date: str = None,
    end_date: str = None,
    type: str = None,
    min_amount: float = None,
    max_amount: float = None,
    payee: str = None,
    is_deposit: bool = None,
    search: str = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
    limit: int = 200,
):
    """Filtered transaction list."""
    conn = get_db()

    clauses = []
    params = []

    if start_date:
        clauses.append("t.date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("t.date <= ?")
        params.append(end_date)
    if type:
        clauses.append("t.type = ?")
        params.append(type)
    if min_amount is not None:
        clauses.append("t.amount >= ?")
        params.append(min_amount)
    if max_amount is not None:
        clauses.append("t.amount <= ?")
        params.append(max_amount)
    if payee:
        clauses.append("(t.description_raw LIKE ? OR t.ach_company_name LIKE ?)")
        params.extend([f"%{payee}%", f"%{payee}%"])
    if is_deposit is not None:
        clauses.append("t.is_deposit = ?")
        params.append(int(is_deposit))
    if search:
        clauses.append(
            "(t.description_raw LIKE ? COLLATE NOCASE "
            "OR t.ach_company_name LIKE ? COLLATE NOCASE "
            "OR t.ach_individual_name LIKE ? COLLATE NOCASE)"
        )
        pattern = f"%{search}%"
        params.extend([pattern, pattern, pattern])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    # Validate sort column
    allowed_sorts = {"date": "t.date", "amount": "t.amount", "type": "t.type"}
    sort_col = allowed_sorts.get(sort_by, "t.date")
    sort_direction = "ASC" if sort_dir.lower() == "asc" else "DESC"

    rows = conn.execute(
        f"SELECT t.transaction_id, t.date, t.description_raw, t.amount, "
        f"t.is_deposit, t.type, t.category, t.subcategory, t.ach_company_name, "
        f"t.ach_description, t.ach_individual_name, t.ach_entry_class, "
        f"t.ach_company_id, t.ach_secondary_co_id "
        f"FROM transactions t {where} "
        f"ORDER BY {sort_col} {sort_direction} LIMIT ?",
        params + [limit],
    ).fetchall()

    count = conn.execute(
        f"SELECT COUNT(*) FROM transactions t {where}", params
    ).fetchone()[0]

    conn.close()

    return {
        "transactions": [dict(r) for r in rows],
        "count": count,
    }


# ---------------------------------------------------------------------------
# API: Summary
# ---------------------------------------------------------------------------

@app.get("/api/summary")
async def get_summary():
    """Balance summary across all statements."""
    conn = get_db()

    stmts = conn.execute(
        "SELECT source_file, period_start, period_end, "
        "opening_balance, closing_balance, total_deposits, "
        "total_withdrawals, num_deposits, num_withdrawals, validation_passed "
        "FROM statements ORDER BY period_start"
    ).fetchall()

    txn_count = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]

    # Get distinct types
    types = conn.execute(
        "SELECT DISTINCT type FROM transactions WHERE type IS NOT NULL ORDER BY type"
    ).fetchall()

    conn.close()

    if not stmts:
        return {"statements": [], "total_transactions": 0, "types": []}

    return {
        "statements": [dict(s) for s in stmts],
        "total_transactions": txn_count,
        "types": [r["type"] for r in types],
    }


# ---------------------------------------------------------------------------
# API: Chat
# ---------------------------------------------------------------------------

# Keep a single chat session alive across requests
_chat_engine = None


def _get_chat():
    global _chat_engine
    from app.chat.chat_engine import JarChat

    if _chat_engine is None:
        _chat_engine = JarChat(str(DB_PATH))
        _chat_engine.debug = True
        print(f"  Chat engine: Claude ({_chat_engine.model})")

    return _chat_engine


@app.get("/api/chat/status")
async def chat_status():
    """Return the current model."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    return {
        "current": "claude",
        "models": [{"id": "claude", "name": "Claude Sonnet (API)", "available": bool(api_key)}],
    }


@app.post("/api/chat")
async def chat(body: dict):
    """Send a message to the chat engine."""
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    try:
        engine = _get_chat()
        response = engine.chat(message)

        # Capture debug info from conversation history
        debug_info = []
        for msg in engine.conversation_history:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if hasattr(block, "type") and block.type == "tool_use":
                            debug_info.append({
                                "function": block.name,
                                "params": block.input,
                            })

        return {"response": response, "debug": debug_info, "model": "claude"}

    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/chat/reset")
async def chat_reset():
    """Reset the chat conversation history."""
    global _chat_engine
    if _chat_engine:
        _chat_engine.close()
        _chat_engine = None
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API: Scanner
# ---------------------------------------------------------------------------

@app.get("/api/scan/paths")
async def scan_paths():
    """Return default scan directories."""
    from app.scanner.scanner_config import get_default_scan_paths
    paths = get_default_scan_paths()
    return {"paths": [str(p) for p in paths]}


@app.get("/api/scan/start")
async def scan_start(paths: str = Query(None)):
    """SSE stream of scan progress + final candidates."""
    from app.scanner.drive_scanner import scan_all_default_paths

    # Parse optional comma-separated directory paths
    scan_dirs = None
    if paths:
        scan_dirs = []
        for p in paths.split(","):
            d = Path(p.strip())
            if d.is_dir():
                scan_dirs.append(d)

    q: queue.Queue = queue.Queue()

    def progress_cb(prog):
        q.put({
            "type": "progress",
            "dirs_scanned": prog.dirs_scanned,
            "pdfs_found": prog.pdfs_found,
            "candidates_found": prog.candidates_found,
            "current_dir": prog.current_dir,
            "elapsed": round(prog.elapsed_seconds, 1),
        })

    async def event_stream():
        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(None, lambda: scan_all_default_paths(paths=scan_dirs, progress_callback=progress_cb))

        while not task.done():
            try:
                msg = q.get_nowait()
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                await asyncio.sleep(0.1)

        # Drain remaining progress messages
        while not q.empty():
            msg = q.get_nowait()
            yield f"data: {json.dumps(msg)}\n\n"

        candidates = task.result()
        result = []
        for c in candidates:
            result.append({
                "filepath": str(c["filepath"]),
                "filename": c["filename"],
                "score": c["score"],
                "detected_banks": c.get("detected_banks", []),
                "signals": c.get("signals", []),
            })

        yield f"data: {json.dumps({'type': 'done', 'candidates': result})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/api/scan/banks")
async def scan_banks():
    """List supported banks. Cloud LLM handles all bank formats."""
    return {"banks": ["Bank of America", "TD Bank"]}


@app.post("/api/scan/process")
async def scan_process(body: dict):
    """Process confirmed PDF candidates through cloud extraction."""
    paths = body.get("paths", [])
    if not paths:
        return JSONResponse({"error": "No paths provided"}, status_code=400)

    from core.extraction_engine.extract import extract_statement

    progress_queue: queue.Queue = queue.Queue()

    def on_progress(step_name, elapsed, details):
        progress_queue.put(json.dumps({
            "type": "log", "step": step_name,
            "time": round(elapsed, 2), "details": details,
        }))

    async def event_stream():
        loop = asyncio.get_event_loop()
        results = []

        for i, p in enumerate(paths):
            pdf_path = Path(p)
            progress_queue.put(json.dumps({
                "type": "file_start", "file": pdf_path.name,
                "index": i, "total": len(paths),
            }))

            task = loop.run_in_executor(
                None, lambda pp=pdf_path: extract_statement(pp, DB_PATH, progress_callback=on_progress)
            )

            while not task.done():
                try:
                    msg = progress_queue.get_nowait()
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    await asyncio.sleep(0.1)

            # Drain remaining
            while not progress_queue.empty():
                msg = progress_queue.get_nowait()
                yield f"data: {msg}\n\n"

            r = task.result()
            results.append({
                "source": str(pdf_path),
                "stem": pdf_path.stem,
                "status": r.status,
                "txn_count": r.txn_count,
                "total_time": round(r.total_time, 1),
                "error": r.error,
            })

        yield f"data: {json.dumps({'type': 'done', 'results': results})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# API: Recurring bills
# ---------------------------------------------------------------------------

@app.get("/api/recurring")
async def get_recurring():
    """Recurring bills."""
    sys.path.insert(0, str(PROJECT_ROOT))
    from core.api.financial_api import FinancialAPI

    api = FinancialAPI(str(DB_PATH))
    result = api.get_recurring_bills()
    api.close()
    return result


# ---------------------------------------------------------------------------
# API: Spending comparison
# ---------------------------------------------------------------------------

@app.get("/api/comparison")
async def get_comparison(
    period1_start: str = Query(...),
    period1_end: str = Query(...),
    period2_start: str = Query(...),
    period2_end: str = Query(...),
):
    """Compare spending between two periods."""
    from core.api.financial_api import FinancialAPI

    api = FinancialAPI(str(DB_PATH))
    result = api.get_spending_comparison(period1_start, period1_end, period2_start, period2_end)
    api.close()
    return result


# ---------------------------------------------------------------------------
# API: Dashboard
# ---------------------------------------------------------------------------

@app.get("/api/dashboard/has-data")
async def dashboard_has_data():
    """Lightweight check: any statements in DB?"""
    return {"has_data": _has_data()}


@app.get("/api/dashboard/summary")
async def dashboard_summary():
    """Balance summary for dashboard cards."""
    from core.api.financial_api import FinancialAPI
    api = FinancialAPI(str(DB_PATH))
    result = api.get_balance_summary()
    api.close()
    return result


@app.get("/api/dashboard/balance-history")
async def dashboard_balance_history():
    """Time-series balance data for line chart."""
    from core.api.financial_api import FinancialAPI
    api = FinancialAPI(str(DB_PATH))
    result = api.get_balance_history()
    api.close()
    return result


@app.get("/api/dashboard/spending-by-category")
async def dashboard_spending_by_category():
    """Category breakdown for donut chart."""
    conn = get_db()
    rows = conn.execute(
        "SELECT COALESCE(t.category, 'uncategorized') as category, "
        "COUNT(*) as count, ROUND(SUM(ABS(t.amount)), 2) as total "
        "FROM transactions t WHERE t.is_deposit = 0 "
        "GROUP BY category ORDER BY total DESC"
    ).fetchall()
    conn.close()
    return {"categories": [dict(r) for r in rows]}


@app.get("/api/dashboard/monthly-trends")
async def dashboard_monthly_trends():
    """Monthly income/spending for bar chart."""
    from core.api.financial_api import FinancialAPI
    api = FinancialAPI(str(DB_PATH))
    result = api.get_monthly_trends()
    api.close()
    return result


@app.get("/api/dashboard/top-merchants")
async def dashboard_top_merchants():
    """Ranked merchant list."""
    from core.api.financial_api import FinancialAPI
    api = FinancialAPI(str(DB_PATH))
    result = api.get_top_merchants()
    api.close()
    return result


# ---------------------------------------------------------------------------
# API: Findings (HSM)
# ---------------------------------------------------------------------------

@app.get("/api/findings")
async def get_findings(dismissed: bool = False):
    """Return HSM findings, newest first."""
    conn = get_db()
    try:
        # Check if the findings table exists
        table_check = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='findings'"
        ).fetchone()
        if not table_check:
            return {"findings": [], "count": 0}

        rows = conn.execute(
            "SELECT finding_id, statement_id, hsm_id, domain, title, "
            "description, amount_impact, severity, merchant, dismissed, created_at "
            "FROM findings WHERE dismissed = ? "
            "ORDER BY created_at DESC",
            (int(dismissed),),
        ).fetchall()

        findings = [dict(r) for r in rows]
        return {"findings": findings, "count": len(findings)}
    finally:
        conn.close()


@app.post("/api/findings/{finding_id}/dismiss")
async def dismiss_finding(finding_id: str):
    """Mark a finding as dismissed."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE findings SET dismissed = 1 WHERE finding_id = ?",
            (finding_id,),
        )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


@app.get("/api/export/csv")
async def export_csv(
    start_date: str = None,
    end_date: str = None,
    type: str = None,
    is_deposit: bool = None,
    search: str = None,
):
    """Export filtered transactions as CSV."""
    conn = get_db()
    clauses = []
    params = []

    if start_date:
        clauses.append("t.date >= ?")
        params.append(start_date)
    if end_date:
        clauses.append("t.date <= ?")
        params.append(end_date)
    if type:
        clauses.append("t.type = ?")
        params.append(type)
    if is_deposit is not None:
        clauses.append("t.is_deposit = ?")
        params.append(int(is_deposit))
    if search:
        clauses.append(
            "(t.description_raw LIKE ? COLLATE NOCASE "
            "OR t.ach_company_name LIKE ? COLLATE NOCASE)"
        )
        pattern = f"%{search}%"
        params.extend([pattern, pattern])

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    rows = conn.execute(
        f"SELECT t.date, t.description_raw, t.amount, t.is_deposit, "
        f"t.type, t.category, t.ach_company_name "
        f"FROM transactions t {where} ORDER BY t.date DESC",
        params,
    ).fetchall()
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Description", "Amount", "Type", "Direction", "Category", "Merchant"])
    for r in rows:
        writer.writerow([
            r["date"], r["description_raw"], r["amount"],
            r["type"], "Deposit" if r["is_deposit"] else "Withdrawal",
            r["category"], r["ach_company_name"] or "",
        ])

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=jar_transactions.csv"},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Database: {DB_PATH}")
    print(f"Statements: {STATEMENTS_DIR}")
    print(f"API key: {'set' if os.environ.get('ANTHROPIC_API_KEY') else 'NOT SET'}")
    print()
    port = int(os.environ.get("JAR_PORT", 5050))
    uvicorn.run(app, host="127.0.0.1", port=port)
