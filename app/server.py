"""
Jar — local web interface for bank statement extraction + chat.

Usage:
    cd JAR
    python app/server.py
    # → Jar running at http://localhost:5000
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import queue
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Query, Request, UploadFile
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
EXTRACTION_DIR = PROJECT_ROOT / "data" / "extraction_output"

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


def _get_extract_backend() -> str | None:
    """Return the best available extraction backend, or None if no API key is set."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "claude"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


@app.get("/")
async def index():
    return RedirectResponse("/dashboard")


@app.get("/api/config")
async def api_config():
    """App config: data status + LLM availability."""
    import urllib.request

    ollama_running = False
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        urllib.request.urlopen(req, timeout=2)
        ollama_running = True
    except Exception:
        pass

    anthropic_key_set = bool(os.environ.get("ANTHROPIC_API_KEY"))
    gemini_key_set = bool(os.environ.get("GEMINI_API_KEY"))

    return {
        "has_data": _has_data(),
        "llm_available": ollama_running or anthropic_key_set or gemini_key_set,
        "ollama_running": ollama_running,
        "anthropic_key_set": anthropic_key_set,
        "gemini_key_set": gemini_key_set,
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
    """Accept a PDF upload, run the full extraction pipeline, load to SQLite."""
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Only PDF files are accepted"}, status_code=400)

    steps = []

    try:
        # 1. Save the PDF
        STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
        pdf_path = STATEMENTS_DIR / file.filename
        content = await file.read()
        pdf_path.write_bytes(content)
        steps.append({"step": "upload", "status": "ok", "message": f"Saved {file.filename} ({len(content):,} bytes)"})

        stem = pdf_path.stem
        EXTRACTION_DIR.mkdir(parents=True, exist_ok=True)

        # 2. Docling preprocessing
        steps.append({"step": "extracting", "status": "running", "message": "Running Docling OCR extraction..."})
        from docling.document_converter import DocumentConverter

        t0 = time.monotonic()
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))

        md_text = result.document.export_to_markdown()
        md_path = EXTRACTION_DIR / f"{stem}.md"
        md_path.write_text(md_text, encoding="utf-8")

        doc_dict = result.document.export_to_dict()
        json_path = EXTRACTION_DIR / f"{stem}.json"
        json_path.write_text(json.dumps(doc_dict, indent=2, default=str), encoding="utf-8")

        extract_secs = time.monotonic() - t0
        steps[-1] = {"step": "extracting", "status": "ok", "message": f"Docling extraction complete ({extract_secs:.1f}s)"}

        # 3. Post-process + structurize (bank-agnostic pipeline)
        from core.extraction_engine.postprocess.run_postprocess import postprocess_and_structurize

        structured_path = postprocess_and_structurize(md_path, json_path, EXTRACTION_DIR, stem, auto_confirm=True)

        if structured_path is None:
            steps.append({"step": "postprocess", "status": "error", "message": "Unknown bank format"})
            return JSONResponse({"status": "error", "steps": steps, "error": "Unknown bank format"}, status_code=400)

        structured = json.loads(structured_path.read_text(encoding="utf-8"))
        steps.append({"step": "postprocess", "status": "ok", "message": "Post-processing complete"})

        # Extract summary info
        summary = structured.get("account_summary", {})
        validation = structured.get("validation", {})
        deposits = structured.get("deposits", [])
        withdrawals = structured.get("withdrawals", [])
        period = structured.get("metadata", {}).get("statement_period", {})

        num_txns = len(deposits) + len(withdrawals)
        steps.append({"step": "structurize", "status": "ok", "message": f"Parsed {num_txns} transactions"})

        # 5. Validate
        errors = validation.get("errors", [])
        all_valid = all(v for k, v in validation.items() if isinstance(v, bool))

        if all_valid:
            steps.append({"step": "validate", "status": "ok", "message": "Validation passed"})
        else:
            steps.append({"step": "validate", "status": "warning", "message": f"Validation issues: {'; '.join(errors) if errors else 'check failed'}"})

        # 6. Load to SQLite
        from core.database.load_statements import init_db, load_one

        conn = get_db()
        conn.execute("PRAGMA journal_mode=WAL")
        init_db(conn)
        load_result = load_one(conn, structured_path)
        conn.close()

        if load_result.get("skipped"):
            steps.append({"step": "database", "status": "ok", "message": f"Already in database (skipped)"})
        else:
            steps.append({"step": "database", "status": "ok", "message": f"Loaded {load_result['txn_count']} transactions to database"})

        return {
            "status": "ok",
            "steps": steps,
            "summary": {
                "source_file": file.filename,
                "period_start": period.get("start"),
                "period_end": period.get("end"),
                "opening_balance": summary.get("opening_balance"),
                "closing_balance": summary.get("closing_balance"),
                "num_deposits": len(deposits),
                "num_withdrawals": len(withdrawals),
                "total_deposits": summary.get("total_deposits_and_other_additions"),
                "total_withdrawals": summary.get("total_withdrawals_and_other_subtractions"),
                "validation_passed": all_valid,
            },
        }

    except Exception as e:
        steps.append({"step": "error", "status": "error", "message": str(e)})
        traceback.print_exc()
        return JSONResponse({"status": "error", "steps": steps, "error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# API: Redact Preview (redact-only, no Claude call)
# ---------------------------------------------------------------------------

@app.post("/api/redact-preview")
async def redact_preview(file: UploadFile = File(...)):
    """Accept a PDF upload, redact PII, return the redacted PDF for inspection."""
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Only PDF files are accepted"}, status_code=400)

    pii_profile_path = PROJECT_ROOT / "data" / "pii_profile.json"
    if not pii_profile_path.exists():
        return JSONResponse({"error": "PII profile not found at data/pii_profile.json"}, status_code=400)

    from core.redaction.pii_profile import build_token_map, load_profile
    from core.redaction.redact_pdf import redact_pdf

    content = await file.read()

    # Save to a temp file (PyMuPDF needs a file path)
    tmp_path = Path(tempfile.mktemp(suffix=".pdf"))
    tmp_path.write_bytes(content)

    try:
        profile = load_profile(pii_profile_path)
        token_map = build_token_map(profile)
        redacted_bytes = redact_pdf(tmp_path, token_map)
    finally:
        tmp_path.unlink(missing_ok=True)

    redacted_filename = f"{Path(file.filename).stem}.redacted.pdf"
    return StreamingResponse(
        iter([redacted_bytes]),
        media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={redacted_filename}"},
    )


# ---------------------------------------------------------------------------
# API: Import via Claude (Path B — PII-redacted cloud extraction)
# ---------------------------------------------------------------------------

@app.post("/api/import-claude")
async def import_claude_pdf(file: UploadFile = File(...)):
    """Accept a PDF upload, run Path B (redact → Claude extract → restore), load to SQLite."""
    if not file.filename.lower().endswith(".pdf"):
        return JSONResponse({"error": "Only PDF files are accepted"}, status_code=400)

    extract_backend = _get_extract_backend()
    if extract_backend is None:
        return JSONResponse({"error": "No LLM API key set. Set ANTHROPIC_API_KEY or GEMINI_API_KEY."}, status_code=400)

    steps = []

    try:
        # 1. Save the PDF
        STATEMENTS_DIR.mkdir(parents=True, exist_ok=True)
        pdf_path = STATEMENTS_DIR / file.filename
        content = await file.read()
        pdf_path.write_bytes(content)
        steps.append({"step": "upload", "status": "ok", "message": f"Saved {file.filename} ({len(content):,} bytes)"})

        stem = pdf_path.stem
        EXTRACTION_DIR.mkdir(parents=True, exist_ok=True)

        # 2. Run Path B pipeline
        backend_label = "Gemini" if extract_backend == "gemini" else "Claude"
        steps.append({"step": "extracting", "status": "running", "message": f"Running PII-redacted {backend_label} extraction..."})

        from core.redaction.pipeline import run_path_b

        pii_profile_path = PROJECT_ROOT / "data" / "pii_profile.json"
        if not pii_profile_path.exists():
            steps.append({"step": "extracting", "status": "error", "message": "PII profile not found at data/pii_profile.json"})
            return JSONResponse({"status": "error", "steps": steps, "error": "PII profile not found"}, status_code=400)

        t0 = time.monotonic()
        structured_path = run_path_b(pdf_path, pii_profile_path, EXTRACTION_DIR, stem, extract_backend=extract_backend)
        extract_secs = time.monotonic() - t0

        if structured_path is None:
            steps.append({"step": "extracting", "status": "error", "message": "Claude extraction failed"})
            return JSONResponse({"status": "error", "steps": steps, "error": "Claude extraction failed"}, status_code=500)

        structured = json.loads(structured_path.read_text(encoding="utf-8"))
        steps[-1] = {"step": "extracting", "status": "ok", "message": f"Claude extraction complete ({extract_secs:.1f}s)"}

        # 3. Extract summary info
        summary = structured.get("account_summary", {})
        validation = structured.get("validation", {})
        deposits = structured.get("deposits", [])
        withdrawals = structured.get("withdrawals", [])
        period = structured.get("metadata", {}).get("statement_period", {})

        num_txns = len(deposits) + len(withdrawals)
        steps.append({"step": "structurize", "status": "ok", "message": f"Parsed {num_txns} transactions"})

        # 4. Validate
        errors = validation.get("errors", [])
        all_valid = all(v for k, v in validation.items() if isinstance(v, bool))

        if all_valid:
            steps.append({"step": "validate", "status": "ok", "message": "Validation passed"})
        else:
            steps.append({"step": "validate", "status": "warning", "message": f"Validation issues: {'; '.join(errors) if errors else 'check failed'}"})

        # 5. Load to SQLite
        from core.database.load_statements import init_db, load_one

        conn = get_db()
        conn.execute("PRAGMA journal_mode=WAL")
        init_db(conn)
        load_result = load_one(conn, structured_path)
        conn.close()

        if load_result.get("skipped"):
            steps.append({"step": "database", "status": "ok", "message": "Already in database (skipped)"})
        else:
            steps.append({"step": "database", "status": "ok", "message": f"Loaded {load_result['txn_count']} transactions to database"})

        return {
            "status": "ok",
            "steps": steps,
            "summary": {
                "source_file": file.filename,
                "period_start": period.get("start"),
                "period_end": period.get("end"),
                "opening_balance": summary.get("opening_balance"),
                "closing_balance": summary.get("closing_balance"),
                "num_deposits": len(deposits),
                "num_withdrawals": len(withdrawals),
                "total_deposits": summary.get("total_deposits_and_other_additions"),
                "total_withdrawals": summary.get("total_withdrawals_and_other_subtractions"),
                "validation_passed": all_valid,
            },
        }

    except Exception as e:
        steps.append({"step": "error", "status": "error", "message": str(e)})
        traceback.print_exc()
        return JSONResponse({"status": "error", "steps": steps, "error": str(e)}, status_code=500)


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
_chat_backend = None  # e.g. "claude", "ollama:qwen2.5:7b", "ollama:qwen2.5:3b"

# Model definitions: id -> (backend, model, display_name)
MODELS = {
    "ollama:qwen3:8b": ("ollama", "qwen3:8b", "Qwen 3 — 8B"),
    "claude": ("claude", "claude-sonnet-4-20250514", "Claude Sonnet (API)"),
    "gemini": ("gemini", "gemini-2.5-flash", "Gemini 2.5 Flash (API)"),
    "ollama:qwen2.5:7b": ("ollama", "qwen2.5:7b", "Qwen 2.5 — 7B"),
    "ollama:qwen2.5:3b": ("ollama", "qwen2.5:3b", "Qwen 2.5 — 3B"),
}

DEFAULT_LOCAL_MODEL = "ollama:qwen3:8b"


def _get_chat(model_id: str = None):
    global _chat_engine, _chat_backend
    from app.chat.chat_engine import JarChat

    # Determine which model to use
    if model_id is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if os.environ.get("ANTHROPIC_API_KEY"):
            model_id = "claude"
        elif os.environ.get("GEMINI_API_KEY"):
            model_id = "gemini"
        else:
            model_id = DEFAULT_LOCAL_MODEL

    if model_id not in MODELS:
        raise ValueError(f"Unknown model: {model_id}")

    backend, model_name, display = MODELS[model_id]

    # Recreate engine if model changed
    if _chat_engine is not None and model_id != _chat_backend:
        _chat_engine.close()
        _chat_engine = None

    if _chat_engine is None:
        if backend == "claude":
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not set. Switch to a local model or set the env var.")
        elif backend == "gemini":
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not set. Switch to a local model or set the env var.")
        else:
            api_key = None
        _chat_engine = JarChat(str(DB_PATH), backend=backend, model=model_name, api_key=api_key)
        _chat_engine.debug = True
        _chat_backend = model_id
        print(f"  Chat engine: {display} ({model_name})")

    return _chat_engine


@app.get("/api/chat/status")
async def chat_status():
    """Return the current model and available models."""
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY")
    gemini_key = os.environ.get("GEMINI_API_KEY")
    if _chat_backend:
        current = _chat_backend
    elif anthropic_key:
        current = "claude"
    elif gemini_key:
        current = "gemini"
    else:
        current = DEFAULT_LOCAL_MODEL
    models = []
    for mid, (backend, model_name, display) in MODELS.items():
        available = True
        if backend == "claude" and not anthropic_key:
            available = False
        if backend == "gemini" and not gemini_key:
            available = False
        models.append({"id": mid, "name": display, "available": available})
    return {"current": current, "models": models}


@app.post("/api/chat")
async def chat(body: dict):
    """Send a message to the chat engine."""
    message = body.get("message", "").strip()
    if not message:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    try:
        engine = _get_chat(_chat_backend)
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

        return {"response": response, "debug": debug_info, "model": _chat_backend}

    except Exception as e:
        error_msg = str(e)
        # Detect Ollama model-not-found errors
        if "model" in error_msg.lower() and "not found" in error_msg.lower():
            model_name = MODELS.get(_chat_backend, (None, _chat_backend, None))[1]
            error_msg = f"Model {model_name} not found. Run `ollama pull {model_name}` to install it."
        traceback.print_exc()
        return JSONResponse({"error": error_msg}, status_code=500)


@app.post("/api/chat/reset")
async def chat_reset():
    """Reset the chat conversation history."""
    global _chat_engine, _chat_backend
    if _chat_engine:
        _chat_engine.close()
        _chat_engine = None
        _chat_backend = None
    return {"status": "ok"}


@app.post("/api/chat/backend")
async def set_backend(body: dict):
    """Switch the chat model. Resets conversation."""
    global _chat_engine, _chat_backend
    model_id = body.get("model", body.get("backend", "")).strip()

    if model_id not in MODELS:
        return JSONResponse({"error": f"Unknown model: {model_id}. Options: {list(MODELS.keys())}"}, status_code=400)

    backend, model_name, display = MODELS[model_id]

    if backend == "claude" and not os.environ.get("ANTHROPIC_API_KEY"):
        return JSONResponse({"error": "ANTHROPIC_API_KEY not set"}, status_code=400)
    if backend == "gemini" and not os.environ.get("GEMINI_API_KEY"):
        return JSONResponse({"error": "GEMINI_API_KEY not set"}, status_code=400)

    # Close existing engine
    if _chat_engine:
        _chat_engine.close()
        _chat_engine = None
        _chat_backend = None

    # Create new engine to verify it works
    try:
        _get_chat(model_id)
        return {"status": "ok", "model": model_id, "name": display}
    except Exception as e:
        error_msg = str(e)
        if "model" in error_msg.lower() and "not found" in error_msg.lower():
            error_msg = f"Model {model_name} not found. Run `ollama pull {model_name}` to install it."
        return JSONResponse({"error": error_msg}, status_code=500)


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
    """List supported bank parsers."""
    from core.extraction_engine.postprocess.registry import list_supported_banks
    return {"banks": list_supported_banks()}


@app.post("/api/scan/process")
async def scan_process(body: dict):
    """Process confirmed PDF candidates: redact PII → Claude extract → restore → load to DB."""
    paths = body.get("paths", [])
    if not paths:
        return JSONResponse({"error": "No paths provided"}, status_code=400)

    extract_backend = _get_extract_backend()
    if extract_backend is None:
        return JSONResponse({"error": "No LLM API key set. Set ANTHROPIC_API_KEY or GEMINI_API_KEY."}, status_code=400)

    pii_profile_path = PROJECT_ROOT / "data" / "pii_profile.json"
    if not pii_profile_path.exists():
        return JSONResponse({"error": "PII profile not found at data/pii_profile.json"}, status_code=400)

    from core.redaction.pipeline import run_path_b
    from core.database.load_statements import init_db, load_one

    EXTRACTION_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for p in paths:
        pdf_path = Path(p)
        stem = pdf_path.stem
        result = {"source": str(pdf_path), "stem": stem, "status": "pending", "error": None}

        try:
            t0 = time.monotonic()
            structured_path = run_path_b(pdf_path, pii_profile_path, EXTRACTION_DIR, stem, extract_backend=extract_backend)

            if not structured_path:
                result["status"] = "extraction_failed"
                results.append(result)
                continue

            # Load to DB
            conn = get_db()
            conn.execute("PRAGMA journal_mode=WAL")
            init_db(conn)
            load_result = load_one(conn, structured_path)
            conn.close()

            result["total_time"] = round(time.monotonic() - t0, 1)
            result["status"] = "success"
            if load_result.get("skipped"):
                result["status"] = "skipped"
        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
            traceback.print_exc()

        results.append(result)

    return {"results": results}


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
    if os.environ.get("ANTHROPIC_API_KEY"):
        default = "claude"
    elif os.environ.get("GEMINI_API_KEY"):
        default = "gemini"
    else:
        default = DEFAULT_LOCAL_MODEL
    _, _, display = MODELS[default]
    print(f"Default model: {display}")
    print(f"Available models: {', '.join(d for _, _, d in MODELS.values())}")
    print()
    port = int(os.environ.get("JAR_PORT", 5001))
    uvicorn.run(app, host="0.0.0.0", port=port)
