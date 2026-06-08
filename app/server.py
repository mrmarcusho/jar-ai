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

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, File, HTTPException, Query, UploadFile

# Configure extraction logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
import requests
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
_scheduler = AsyncIOScheduler()
_scheduler_logger = logging.getLogger("jar.scheduler")

# Serve static files
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@app.on_event("startup")
async def _on_startup() -> None:
    """Apply DB schema migrations and start the nightly email sync scheduler."""
    from core.database.migrate import ensure_schema

    conn = sqlite3.connect(str(DB_PATH))
    try:
        ensure_schema(conn)
    finally:
        conn.close()

    async def _nightly_sync() -> None:
        loop = asyncio.get_event_loop()
        # Email and message syncs are isolated: one failing never blocks the other.
        try:
            from core.email import imap_client, sync as email_sync
            if imap_client.load_account() is not None:
                result = await loop.run_in_executor(None, email_sync.incremental_sync)
                _scheduler_logger.info("Nightly email sync completed: %s", result)
        except Exception:
            _scheduler_logger.exception("Nightly email sync failed")

        try:
            from core.messages import sync as msg_sync
            result = await loop.run_in_executor(None, msg_sync.incremental_sync)
            _scheduler_logger.info("Nightly message sync completed: %s", result)
        except Exception:
            _scheduler_logger.exception("Nightly message sync failed")

    _scheduler.add_job(_nightly_sync, CronTrigger(hour=2, minute=0))
    _scheduler.start()
    _scheduler_logger.info("Scheduler started — nightly email + message sync at 02:00")

    # Warm Presidio so the first chat query doesn't pay the ~1-2s spaCy load, and so
    # degraded redaction (Presidio unavailable) is logged at boot rather than silently.
    try:
        from core.privacy.redactor import _get_shared_presidio
        analyzer, _ = _get_shared_presidio()
        if analyzer is None:
            _scheduler_logger.warning("Presidio unavailable — chat redaction is NER-degraded.")
    except Exception:
        _scheduler_logger.exception("Presidio warm-up failed")


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    _scheduler.shutdown(wait=True)


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
# API: Household Profile (PII redaction seed)
# ---------------------------------------------------------------------------

@app.get("/api/profile/status")
async def profile_status():
    """Return whether the household profile has been configured."""
    from core.privacy.profile import HouseholdProfile
    conn = get_db()
    profile = HouseholdProfile.load(conn)
    conn.close()
    return {"configured": profile.is_configured()}


@app.get("/api/profile")
async def get_profile():
    """Return the current household profile."""
    from core.privacy.profile import HouseholdProfile
    conn = get_db()
    profile = HouseholdProfile.load(conn)
    conn.close()
    return profile.to_dict()


@app.post("/api/profile")
async def save_profile(body: dict):
    """Save household profile. Replaces all existing fields."""
    from core.privacy.profile import HouseholdProfile
    aliases_raw = body.get("aliases", [])
    if isinstance(aliases_raw, str):
        aliases_raw = [a.strip() for a in aliases_raw.split(",") if a.strip()]
    profile = HouseholdProfile(
        first_name=body.get("first_name", "").strip(),
        last_name=body.get("last_name", "").strip(),
        aliases=aliases_raw,
        partner_first=body.get("partner_first", "").strip(),
        partner_last=body.get("partner_last", "").strip(),
        street=body.get("street", "").strip(),
        city=body.get("city", "").strip(),
    )
    conn = get_db()
    try:
        # Ensure table exists (safe on existing DBs)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS household_profile (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                field_type  TEXT NOT NULL,
                value       TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        profile.save(conn)
    finally:
        conn.close()
    return {"status": "ok", "configured": profile.is_configured()}


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

# Model registry: id → display name, engine type
CHAT_MODELS = {
    "gemma4:e4b": {"name": "Gemma 4 — E4B (local)", "type": "ollama"},
    "gemma4:e2b": {"name": "Gemma 4 — E2B (local)", "type": "ollama"},
    "claude":     {"name": "Claude Sonnet (API)",     "type": "claude"},
}

_chat_engine = None
_current_model_id = "gemma4:e2b"


def _ollama_available(model_id: str) -> bool:
    """Check if an Ollama model is pulled and the server is running."""
    try:
        resp = requests.get("http://localhost:11434/api/tags", timeout=3)
        names = [m["name"] for m in resp.json().get("models", [])]
        # Ollama tags may include `:latest` suffix
        return any(model_id in n for n in names)
    except Exception:
        return False


def _build_chat_engine(model_id: str):
    """Instantiate the right chat engine for a model id."""
    info = CHAT_MODELS[model_id]

    if info["type"] == "claude":
        from app.chat.chat_engine import JarChat
        engine = JarChat(str(DB_PATH))
    else:
        from app.chat.local_chat_engine import JarLocalChat
        engine = JarLocalChat(str(DB_PATH), model=model_id)

    engine.debug = True
    print(f"  Chat engine: {info['name']} ({model_id})")
    return engine


def _get_chat():
    global _chat_engine
    if _chat_engine is None:
        _chat_engine = _build_chat_engine(_current_model_id)
    return _chat_engine


@app.get("/api/chat/status")
async def chat_status():
    """Return current model and full model list with availability."""
    api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    models = []
    for mid, info in CHAT_MODELS.items():
        if info["type"] == "claude":
            available = api_key
        else:
            available = _ollama_available(mid)
        models.append({"id": mid, "name": info["name"], "available": available})

    return {"current": _current_model_id, "models": models}


@app.post("/api/chat/backend")
async def chat_backend(body: dict):
    """Switch the active chat model."""
    global _chat_engine, _current_model_id

    model_id = body.get("model", "")
    if model_id not in CHAT_MODELS:
        return JSONResponse({"error": f"Unknown model: {model_id}"}, status_code=400)

    info = CHAT_MODELS[model_id]

    # Check availability
    if info["type"] == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=400)
    if info["type"] == "ollama" and not _ollama_available(model_id):
        return JSONResponse({"error": f"{model_id} not available — is Ollama running with the model pulled?"}, status_code=400)

    # Tear down old engine, switch
    if _chat_engine:
        _chat_engine.close()
        _chat_engine = None

    _current_model_id = model_id
    return {"status": "ok", "model": model_id, "name": info["name"]}


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
            if isinstance(msg, dict):
                # Ollama format: tool_calls in message dict
                for tc in msg.get("tool_calls", []):
                    debug_info.append({
                        "function": tc["function"]["name"],
                        "params": tc["function"].get("arguments", {}),
                    })
                # Anthropic format: content is a list of blocks
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if hasattr(block, "type") and block.type == "tool_use":
                            debug_info.append({
                                "function": block.name,
                                "params": block.input,
                            })

        return {"response": response, "debug": debug_info, "model": _current_model_id}

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
# API: Google Drive
# ---------------------------------------------------------------------------

# OAuth state for the in-flight authorization (single-user local app).
_drive_oauth_state: dict = {}


def _drive_redirect_uri() -> str:
    port = int(os.environ.get("JAR_PORT", 5050))
    return f"http://127.0.0.1:{port}/api/drive/callback"


@app.get("/api/drive/status")
async def drive_status():
    """Whether Google Drive is connected, plus the linked account email."""
    from app.scanner import drive_client

    creds = drive_client.load_credentials()
    if creds is None:
        return {"connected": False}
    return {"connected": True, "email": drive_client.get_account_email(creds)}


@app.get("/api/drive/auth")
async def drive_auth():
    """Start the OAuth flow by redirecting to Google's consent screen."""
    from app.scanner import drive_client

    try:
        flow = drive_client.build_flow(_drive_redirect_uri())
    except drive_client.DriveConfigError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    auth_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    # Persist state + PKCE verifier so the callback (a fresh Flow) can complete
    # the token exchange.
    _drive_oauth_state["state"] = state
    _drive_oauth_state["code_verifier"] = flow.code_verifier
    return RedirectResponse(auth_url)


@app.get("/api/drive/callback")
async def drive_callback(code: str = Query(None), state: str = Query(None), error: str = Query(None)):
    """OAuth redirect target: exchange the code for tokens and persist them."""
    from app.scanner import drive_client

    if error or not code:
        logging.error("Drive OAuth callback error param: %s", error)
        return RedirectResponse("/scan?drive=error")

    try:
        # Google may return granted scopes in a different order/superset; don't
        # treat that as a fatal mismatch.
        os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
        flow = drive_client.build_flow(_drive_redirect_uri(), state=state or _drive_oauth_state.get("state"))
        flow.code_verifier = _drive_oauth_state.get("code_verifier")
        flow.fetch_token(code=code)
        drive_client.save_credentials(flow.credentials)
    except Exception:
        logging.exception("Drive OAuth token exchange failed")
        return RedirectResponse("/scan?drive=error")
    finally:
        _drive_oauth_state.pop("state", None)
        _drive_oauth_state.pop("code_verifier", None)

    return RedirectResponse("/scan?drive=connected")


@app.post("/api/drive/disconnect")
async def drive_disconnect():
    """Forget the stored Google Drive token."""
    from app.scanner import drive_client

    drive_client.disconnect()
    return {"connected": False}


@app.get("/api/drive/folders")
async def drive_folders():
    """List the user's Drive folders for the folder picker."""
    from app.scanner import drive_client

    creds = drive_client.load_credentials()
    if creds is None:
        return JSONResponse({"error": "Not connected"}, status_code=401)
    return {"folders": drive_client.list_folders(creds)}


@app.get("/api/drive/scan")
async def drive_scan(folder_id: str = Query(None)):
    """SSE stream of Drive scan progress + final candidates."""
    from app.scanner import drive_client
    from app.scanner.filename_classifier import classify_name

    creds = drive_client.load_credentials()
    if creds is None:
        return JSONResponse({"error": "Not connected"}, status_code=401)

    q: queue.Queue = queue.Queue()

    def progress_cb(count):
        q.put({"type": "progress", "pdfs_found": count})

    def run_scan():
        files = drive_client.list_pdf_files(creds, folder_id=folder_id, progress_cb=progress_cb)
        candidates = []
        for f in files:
            c = classify_name(f["name"], f["size"], f["file_id"])
            if c["score"] >= 0.3:
                candidates.append(c)
        candidates.sort(key=lambda c: (-c["score"], c["filename"]))
        return candidates

    async def event_stream():
        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(None, run_scan)

        while not task.done():
            try:
                msg = q.get_nowait()
                yield f"data: {json.dumps(msg)}\n\n"
            except queue.Empty:
                await asyncio.sleep(0.1)

        while not q.empty():
            msg = q.get_nowait()
            yield f"data: {json.dumps(msg)}\n\n"

        candidates = task.result()
        result = [{
            "file_id": c["file_id"],
            "filename": c["filename"],
            "score": c["score"],
            "detected_banks": c.get("detected_banks", []),
        } for c in candidates]

        yield f"data: {json.dumps({'type': 'done', 'candidates': result})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/drive/process")
async def drive_process(body: dict):
    """Download selected Drive files, then run them through cloud extraction."""
    from app.scanner import drive_client
    from core.extraction_engine.extract import extract_statement

    creds = drive_client.load_credentials()
    if creds is None:
        return JSONResponse({"error": "Not connected"}, status_code=401)

    files = body.get("files", [])
    if not files:
        return JSONResponse({"error": "No files provided"}, status_code=400)

    progress_queue: queue.Queue = queue.Queue()

    def on_progress(step_name, elapsed, details):
        progress_queue.put(json.dumps({
            "type": "log", "step": step_name,
            "time": round(elapsed, 2), "details": details,
        }))

    def download_and_extract(file_id, name):
        dest = STATEMENTS_DIR / name
        if dest.exists():
            dest = STATEMENTS_DIR / f"{file_id[:8]}_{name}"
        drive_client.download_file(creds, file_id, dest)
        return extract_statement(dest, DB_PATH, progress_callback=on_progress)

    async def event_stream():
        loop = asyncio.get_event_loop()
        results = []

        for i, f in enumerate(files):
            file_id = f.get("file_id")
            name = f.get("filename") or f"{file_id}.pdf"
            progress_queue.put(json.dumps({
                "type": "file_start", "file": name,
                "index": i, "total": len(files),
            }))

            task = loop.run_in_executor(
                None, lambda fid=file_id, nm=name: download_and_extract(fid, nm)
            )

            while not task.done():
                try:
                    msg = progress_queue.get_nowait()
                    yield f"data: {msg}\n\n"
                except queue.Empty:
                    await asyncio.sleep(0.1)

            while not progress_queue.empty():
                msg = progress_queue.get_nowait()
                yield f"data: {msg}\n\n"

            try:
                r = task.result()
                results.append({
                    "source": name,
                    "stem": Path(name).stem,
                    "status": r.status,
                    "txn_count": r.txn_count,
                    "total_time": round(r.total_time, 1),
                    "error": r.error,
                })
            except Exception as e:
                results.append({
                    "source": name, "stem": Path(name).stem,
                    "status": "error", "txn_count": 0,
                    "total_time": 0, "error": str(e),
                })

        yield f"data: {json.dumps({'type': 'done', 'results': results})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# API: Email ingestion
# ---------------------------------------------------------------------------

@app.get("/api/email/status")
async def email_status():
    """Whether an IMAP account is linked, plus last-sync and embedded-email count."""
    from core.email import imap_client, vector_store

    account = imap_client.load_account()
    if account is None:
        return {"connected": False}

    conn = get_db()
    try:
        row = conn.execute(
            "SELECT last_sync_at FROM email_sync_state WHERE username = ?",
            (account["username"],),
        ).fetchone()
    finally:
        conn.close()

    return {
        "connected": True,
        "username": account["username"],
        "last_sync": row[0] if row else None,
        "total_emails": vector_store.count(),
    }


@app.post("/api/email/connect")
async def email_connect(body: dict):
    """Validate IMAP credentials, save them to the gitignored token file."""
    from core.email import imap_client

    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    host = body.get("host")
    port = body.get("port")
    if not username or not password:
        return JSONResponse({"error": "username and password required"}, status_code=400)

    try:
        resolved_host, resolved_port = imap_client.resolve_server(username, host, port)
    except imap_client.IMAPConfigError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    try:
        conn = imap_client.connect(username, password, resolved_host, resolved_port)
    except Exception as e:
        return JSONResponse(
            {"error": f"Could not log in: {e}"}, status_code=401
        )

    try:
        typ, data = conn.uid("SEARCH", None, "ALL")
        email_count = len(data[0].split()) if typ == "OK" and data and data[0] else 0
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    imap_client.save_account(username, password, resolved_host, resolved_port)
    return {"success": True, "email_count": email_count}


@app.get("/api/email/scan")
async def email_scan(days_back: int = Query(None)):
    """SSE stream of a full mailbox scan: stage progress, then a final summary."""
    from core.email import imap_client, pipeline

    if imap_client.load_account() is None:
        return JSONResponse({"error": "Not connected"}, status_code=401)

    q: queue.Queue = queue.Queue()

    def progress_cb(update: dict):
        q.put({"type": "progress", **update})

    def run():
        kwargs = {"progress_cb": progress_cb}
        if days_back:
            kwargs["days_back"] = days_back
        return pipeline.run_full_scan(**kwargs)

    async def event_stream():
        loop = asyncio.get_event_loop()
        task = loop.run_in_executor(None, run)

        while not task.done():
            try:
                yield f"data: {json.dumps(q.get_nowait())}\n\n"
            except queue.Empty:
                await asyncio.sleep(0.1)

        while not q.empty():
            yield f"data: {json.dumps(q.get_nowait())}\n\n"

        try:
            summary = task.result()
            yield f"data: {json.dumps({'type': 'done', 'summary': summary})}\n\n"
        except Exception as e:
            logging.exception("Email scan failed")
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/email/sync")
async def email_sync():
    """Incremental sync: scan only messages newer than the last run."""
    from core.email import imap_client, sync

    if imap_client.load_account() is None:
        return JSONResponse({"error": "Not connected"}, status_code=401)

    loop = asyncio.get_event_loop()
    summary = await loop.run_in_executor(None, sync.incremental_sync)
    return {
        "new_emails": summary.get("new_emails", 0),
        "summary": summary,
    }


@app.post("/api/messages/scan")
async def messages_scan():
    """Full scan of the local Messages database into the vector store."""
    from core.messages import pipeline
    from core.messages.chat_db import MessagesAccessError

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, pipeline.run_full_scan)
    except MessagesAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/messages/sync")
async def messages_sync():
    """Incremental message sync: ingest only messages newer than the last run."""
    from core.messages import sync as msg_sync
    from core.messages.chat_db import MessagesAccessError

    loop = asyncio.get_event_loop()
    try:
        return await loop.run_in_executor(None, msg_sync.incremental_sync)
    except MessagesAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.delete("/api/email/disconnect")
async def email_disconnect():
    """Forget the stored IMAP account and its sync state (keeps extracted data)."""
    from core.email import imap_client

    account = imap_client.load_account()
    imap_client.clear_account()
    if account is not None:
        conn = get_db()
        try:
            conn.execute(
                "DELETE FROM email_sync_state WHERE username = ?",
                (account["username"],),
            )
            conn.commit()
        finally:
            conn.close()
    return {"connected": False}


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
