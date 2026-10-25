#!/usr/bin/env python3
"""Redact PII from bank-statement PDF text using OpenAI Privacy Filter.

Pulls `openai/privacy-filter` — a 1.5B open-weight token-classification model
(Apache 2.0, released 2026-04-22) — extracts text from bank-statement PDFs with
PyMuPDF, and masks every detected PII span with an ``[ENTITY]`` token.

This is a standalone evaluation harness. It deliberately does NOT touch the
production redaction pipeline (`core/privacy/redactor.py`, Presidio-based); it
exists so we can eyeball how OpenAI's model does on our real statements —
especially the counterparty/uncommon-name recall gap the Presidio path misses.

Usage:
    ./venv/bin/python scripts/privacy_filter_redact.py --limit 20
    ./venv/bin/python scripts/privacy_filter_redact.py --limit 20 --device cpu
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import fitz  # PyMuPDF
import torch
from transformers import AutoModelForTokenClassification, AutoTokenizer

MODEL_ID = "openai/privacy-filter"
REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = REPO_ROOT / "data" / "statements"
OUT_DIR = REPO_ROOT / "data" / "experiments" / "privacy_filter"

# Split long documents on line boundaries into windows this size (chars) so each
# forward pass stays small. Entities almost never span a newline in statement
# text, so line-boundary splitting is safe. Joining the windows with "\n"
# reproduces the original text exactly.
CHUNK_CHARS = 3000


def log(msg: str) -> None:
    print(msg, flush=True)


def pick_device(requested: str | None) -> str:
    if requested:
        return requested
    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def extract_text(pdf_path: Path) -> str:
    """Digital-PDF text via PyMuPDF (same engine as the extraction pipeline)."""
    with fitz.open(str(pdf_path)) as doc:
        return "\n".join(page.get_text() for page in doc)


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Partition `text` into line-aligned windows. "\\n".join(result) == text."""
    lines = text.split("\n")
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    for line in lines:
        add = len(line) + 1  # +1 for the newline that rejoins them
        if cur and cur_len + add > max_chars:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        cur.append(line)
        cur_len += add
    if cur:
        chunks.append("\n".join(cur))
    return chunks or [""]


def parse_label(label: str) -> tuple[str | None, str | None]:
    """Split a BIOES token label into (prefix, entity_type).

    Expected format is ``B-private_person`` etc.; ``O`` is background. Returns
    (None, None) for background or any label we don't recognise.
    """
    if not label or label == "O":
        return None, None
    if "-" not in label:
        return None, None
    prefix, etype = label.split("-", 1)
    if prefix not in ("B", "I", "E", "S"):
        return None, None
    return prefix, etype


def aggregate_bioes(labels: list[str], offsets: list[tuple[int, int]]) -> list[tuple[int, int, str]]:
    """Fold per-token BIOES labels + char offsets into (start, end, type) spans."""
    spans: list[tuple[int, int, str]] = []
    cur_type: str | None = None
    cur_start = 0
    cur_end = 0

    def flush() -> None:
        nonlocal cur_type, cur_start, cur_end
        if cur_type is not None:
            spans.append((cur_start, cur_end, cur_type))
        cur_type = None

    for (start, end), label in zip(offsets, labels):
        if start == end:  # special tokens carry (0, 0) offsets
            continue
        prefix, etype = parse_label(label)
        if prefix is None:
            flush()
            continue
        if prefix in ("B", "S"):
            flush()
            cur_type, cur_start, cur_end = etype, start, end
            if prefix == "S":
                flush()
        else:  # I or E — continue the current span if types match
            if cur_type == etype:
                cur_end = end
            else:
                flush()
                cur_type, cur_start, cur_end = etype, start, end
            if prefix == "E":
                flush()
    flush()
    return spans


def redact_spans(text: str, spans: list[tuple[int, int, str]]) -> str:
    """Replace each span with an ``[ENTITY_TYPE]`` token, right-to-left."""
    for start, end, etype in sorted(spans, key=lambda s: s[0], reverse=True):
        text = text[:start] + f"[{etype.upper()}]" + text[end:]
    return text


def redact_chunk(text, tokenizer, model, device, id2label):
    enc = tokenizer(
        text,
        return_offsets_mapping=True,
        return_tensors="pt",
        truncation=False,
    )
    offsets = [tuple(o) for o in enc.pop("offset_mapping")[0].tolist()]
    enc = {k: v.to(device) for k, v in enc.items()}
    with torch.no_grad():
        logits = model(**enc).logits
    pred_ids = logits.argmax(dim=-1)[0].tolist()
    labels = [id2label[i] for i in pred_ids]
    spans = aggregate_bioes(labels, offsets)
    return redact_spans(text, spans), spans


def redact_document(text, tokenizer, model, device, id2label):
    redacted_chunks: list[str] = []
    counts: Counter[str] = Counter()
    for chunk in chunk_text(text, CHUNK_CHARS):
        red, spans = redact_chunk(chunk, tokenizer, model, device, id2label)
        redacted_chunks.append(red)
        for _, _, etype in spans:
            counts[etype] += 1
    return "\n".join(redacted_chunks), counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=20, help="number of PDFs to process")
    ap.add_argument("--device", default=None, help="cpu | mps | cuda (default: auto)")
    args = ap.parse_args()

    pdfs = sorted(PDF_DIR.glob("*.pdf"))[: args.limit]
    if not pdfs:
        log(f"No PDFs found in {PDF_DIR}")
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    device = pick_device(args.device)
    log(f"Pulling {MODEL_ID} (first run downloads weights to the HF cache)…")
    t0 = time.time()
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForTokenClassification.from_pretrained(
        MODEL_ID, torch_dtype=torch.float32
    )
    model.to(device)
    model.eval()
    id2label = model.config.id2label
    log(f"Model ready on '{device}' in {time.time() - t0:.1f}s "
        f"({len(id2label)} labels, e.g. {list(id2label.values())[:6]})")

    summary = []
    grand = Counter()
    for i, pdf in enumerate(pdfs, 1):
        t = time.time()
        text = extract_text(pdf)
        if not text.strip():
            log(f"[{i}/{len(pdfs)}] {pdf.name}: no extractable text (scanned?), skipping")
            summary.append({"file": pdf.name, "chars": 0, "entities": {}, "skipped": True})
            continue
        redacted, counts = redact_document(text, tokenizer, model, device, id2label)
        out_path = OUT_DIR / f"{pdf.stem}.redacted.txt"
        out_path.write_text(redacted)
        grand.update(counts)
        total = sum(counts.values())
        detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
        log(f"[{i}/{len(pdfs)}] {pdf.name}: {len(text)} chars → "
            f"{total} PII spans ({detail}) [{time.time() - t:.1f}s]")
        summary.append({
            "file": pdf.name,
            "chars": len(text),
            "entities": dict(counts),
            "total_spans": total,
            "output": str(out_path.relative_to(REPO_ROOT)),
        })

    (OUT_DIR / "_summary.json").write_text(json.dumps({
        "model": MODEL_ID,
        "device": device,
        "files": summary,
        "totals": dict(grand),
    }, indent=2))

    log("\n=== Totals across %d files ===" % len([s for s in summary if not s.get("skipped")]))
    for etype, n in sorted(grand.items(), key=lambda kv: -kv[1]):
        log(f"  {etype:20s} {n}")
    log(f"\nRedacted text + _summary.json written to {OUT_DIR.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
