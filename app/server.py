"""
Jar — local web interface for bank statement extraction + chat.

Usage:
    cd JAR
    python app/server.py
    # → Jar running at http://localhost:5000
"""

import json
import os
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
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

@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


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

        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))

        md_text = result.document.export_to_markdown()
        md_path = EXTRACTION_DIR / f"{stem}.md"
        md_path.write_text(md_text, encoding="utf-8")

        doc_dict = result.document.export_to_dict()
        json_path = EXTRACTION_DIR / f"{stem}.json"
        json_path.write_text(json.dumps(doc_dict, indent=2, default=str), encoding="utf-8")

        steps[-1] = {"step": "extracting", "status": "ok", "message": "Docling extraction complete"}

        # 3. Post-process + structurize (bank-agnostic pipeline)
        from core.extraction_engine.postprocess.run_postprocess import postprocess_and_structurize

        structured_path = postprocess_and_structurize(md_path, json_path, EXTRACTION_DIR, stem)

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
        f"t.is_deposit, t.type, t.category, t.ach_company_name, "
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
    "ollama:qwen2.5:7b": ("ollama", "qwen2.5:7b", "Qwen 2.5 — 7B"),
    "ollama:qwen2.5:3b": ("ollama", "qwen2.5:3b", "Qwen 2.5 — 3B"),
}

DEFAULT_LOCAL_MODEL = "ollama:qwen3:8b"


def _get_chat(model_id: str = None):
    global _chat_engine, _chat_backend
    from app.chat.chat_engine import JarChat

    # Determine which model to use
    if model_id is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        model_id = "claude" if api_key else DEFAULT_LOCAL_MODEL

    if model_id not in MODELS:
        raise ValueError(f"Unknown model: {model_id}")

    backend, model_name, display = MODELS[model_id]

    # Recreate engine if model changed
    if _chat_engine is not None and model_id != _chat_backend:
        _chat_engine.close()
        _chat_engine = None

    if _chat_engine is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if backend == "claude" and not api_key:
            raise ValueError("ANTHROPIC_API_KEY not set. Switch to a local model or set the env var.")
        _chat_engine = JarChat(str(DB_PATH), backend=backend, model=model_name, api_key=api_key)
        _chat_engine.debug = True
        _chat_backend = model_id
        print(f"  Chat engine: {display} ({model_name})")

    return _chat_engine


@app.get("/api/chat/status")
async def chat_status():
    """Return the current model and available models."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    current = _chat_backend or ("claude" if api_key else DEFAULT_LOCAL_MODEL)
    models = []
    for mid, (backend, model_name, display) in MODELS.items():
        available = True
        if backend == "claude" and not api_key:
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
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"Database: {DB_PATH}")
    print(f"Statements: {STATEMENTS_DIR}")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    default = "claude" if api_key else DEFAULT_LOCAL_MODEL
    _, _, display = MODELS[default]
    print(f"Default model: {display}")
    print(f"Available models: {', '.join(d for _, _, d in MODELS.values())}")
    print()
    uvicorn.run(app, host="0.0.0.0", port=5000)
