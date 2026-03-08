"""Shared post-processing utilities used by multiple bank processors.

Bank-specific fixes should NOT go here — they belong in the bank's own module.
Only truly generic operations (Docling artifacts, format normalization) belong
in this file.
"""

import copy
import re
from datetime import datetime
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Merge spanning header columns (Docling artifact — affects multiple banks)
# ---------------------------------------------------------------------------


def _find_merge_groups(headers: List[str]) -> List[Tuple[int, int]]:
    """Return list of (start, end) index pairs for consecutive identical headers.

    Only groups of 2+ identical consecutive names are returned.
    """
    groups: List[Tuple[int, int]] = []
    i = 0
    while i < len(headers):
        j = i + 1
        while j < len(headers) and headers[j] == headers[i]:
            j += 1
        if j - i > 1:
            groups.append((i, j))
        i = j
    return groups


def _merge_texts(texts: List[str]) -> str:
    """Deduplicate consecutive identical values then join with space.

    Handles all observed Docling splitting patterns:
      ["A", "A", "A", "A"]  -> "A"            (fully repeated)
      ["A", "B", "C", "C"]  -> "A B C"        (split ACH, trailing dup)
      ["A", "A", "CO", "CO"] -> "A CO"        (partial split)
    """
    deduped: List[str] = []
    for t in texts:
        t = t.strip()
        if not t:
            continue
        if deduped and t == deduped[-1]:
            continue
        deduped.append(t)
    return " ".join(deduped)


def merge_spanning_header_columns_md(md_text: str) -> str:
    """Merge columns in markdown tables where headers are repeated."""
    out_lines: List[str] = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        # Detect start of a markdown table (header row with |)
        if not lines[i].startswith("|"):
            out_lines.append(lines[i])
            i += 1
            continue

        # Collect the full table block
        table_start = i
        while i < len(lines) and lines[i].startswith("|"):
            i += 1
        table_lines = lines[table_start:i]

        if len(table_lines) < 2:
            out_lines.extend(table_lines)
            continue

        # Parse header cells
        header_cells = [c.strip() for c in table_lines[0].split("|")]
        # split("|") gives empty strings at start/end for "|a|b|"
        header_cells = header_cells[1:-1] if len(header_cells) >= 2 else header_cells

        merge_groups = _find_merge_groups(header_cells)
        if not merge_groups:
            out_lines.extend(table_lines)
            continue

        # Build new header
        new_header_cells: List[str] = []
        skip = set()
        for start, end in merge_groups:
            for idx in range(start + 1, end):
                skip.add(idx)
        for idx, cell in enumerate(header_cells):
            if idx not in skip:
                new_header_cells.append(cell)

        # Rebuild header line
        out_lines.append("| " + " | ".join(new_header_cells) + " |")

        # Rebuild separator line (line index 1)
        sep_parts = []
        for cell in new_header_cells:
            sep_parts.append("-" * max(len(cell), 3))
        out_lines.append("|" + "|".join(sep_parts) + "|")

        # Rebuild data rows (lines index 2+)
        merged_indices = set()
        for start, end in merge_groups:
            for idx in range(start, end):
                merged_indices.add(idx)
        mg_starts = {start: end for start, end in merge_groups}

        for row_line in table_lines[2:]:
            row_cells = [c.strip() for c in row_line.split("|")]
            row_cells = row_cells[1:-1] if len(row_cells) >= 2 else row_cells

            final_cells: List[str] = []
            for idx, cell in enumerate(row_cells):
                if idx in mg_starts:
                    final_cells.append(_merge_texts(row_cells[idx:mg_starts[idx]]))
                elif idx not in merged_indices:
                    final_cells.append(cell)

            out_lines.append("| " + " | ".join(final_cells) + " |")

    return "\n".join(out_lines)


def merge_spanning_header_columns_json(doc_dict: dict) -> dict:
    """Merge columns in JSON table structures where headers are repeated."""
    doc_dict = copy.deepcopy(doc_dict)

    for table_entry in doc_dict.get("tables", []):
        table = table_entry.get("data", table_entry)
        grid = table.get("grid", [])
        if not grid:
            continue

        # Extract header texts from row 0
        header_texts = [cell.get("text", "") for cell in grid[0]]
        merge_groups = _find_merge_groups(header_texts)
        if not merge_groups:
            continue

        # Columns to remove (all but the first in each group)
        cols_to_remove = set()
        for start, end in merge_groups:
            for idx in range(start + 1, end):
                cols_to_remove.add(idx)

        new_num_cols = len(header_texts) - len(cols_to_remove)

        # Build column index mapping: old_col -> new_col
        col_map = {}
        new_idx = 0
        for old_idx in range(len(header_texts)):
            if old_idx not in cols_to_remove:
                col_map[old_idx] = new_idx
                new_idx += 1

        # Process each grid row
        new_grid = []
        for row in grid:
            new_row = []
            for start, end in merge_groups:
                # Merge texts from cells in this group
                group_texts = [row[idx].get("text", "") for idx in range(start, end) if idx < len(row)]
                merged = _merge_texts(group_texts)
                # Update the first cell in the group
                row[start]["text"] = merged
                row[start]["col_span"] = 1
                row[start]["end_col_offset_idx"] = row[start]["start_col_offset_idx"] + 1

            # Keep only non-removed columns
            for idx, cell in enumerate(row):
                if idx not in cols_to_remove:
                    new_col = col_map[idx]
                    cell["start_col_offset_idx"] = new_col
                    cell["end_col_offset_idx"] = new_col + cell.get("col_span", 1)
                    new_row.append(cell)
            new_grid.append(new_row)

        table["grid"] = new_grid
        table["num_cols"] = new_num_cols

        # Rebuild table_cells flat list from grid
        new_table_cells = []
        seen = set()
        for row in new_grid:
            for cell in row:
                key = (cell["start_row_offset_idx"], cell["start_col_offset_idx"])
                if key not in seen:
                    seen.add(key)
                    new_table_cells.append(cell)
        table["table_cells"] = new_table_cells

    return doc_dict


def normalize_amount(amount_str: str) -> float:
    """Robustly parse a dollar amount string to float.

    Handles edge cases found in the benchmark:
    - OCR spaces: '5468 .00' -> 5468.00
    - Multiple spaces: '5  700 . 00' -> 5700.00
    - Trailing negative: '650.00-' -> -650.00
    - Parenthetical negatives: '(500.00)' -> -500.00
    - Currency symbols: '$5,468.00' -> 5468.00
    - Leading equals: '= 701.39' -> 701.39

    Returns float amount.
    Raises ValueError if the string cannot be parsed unambiguously.
    """
    s = amount_str.strip()
    if not s:
        raise ValueError("Empty amount string")

    # Detect parenthetical negatives: (500.00) -> -500.00
    paren = re.match(r"^\((.+)\)$", s)
    if paren:
        s = "-" + paren.group(1)

    # Trailing negative: 650.00- -> -650.00
    if s.endswith("-"):
        s = "-" + s[:-1]

    # Strip currency symbols and leading =
    s = re.sub(r"^[=$£€¥]+\s*", "", s)

    # Strip commas
    s = s.replace(",", "")

    # Strip internal spaces (OCR artifacts like '5468 .00')
    s = re.sub(r"\s+", "", s)

    # Reject merged amounts: two negative signs or multiple dots
    if s.count("-") > 1:
        raise ValueError(f"Ambiguous amount (multiple negatives): {amount_str!r}")
    if s.count(".") > 1:
        raise ValueError(f"Ambiguous amount (multiple decimals): {amount_str!r}")

    val = float(s)
    return 0.0 if val == 0 else val


def normalize_date(date_str: str, source_format: str = "MM/DD/YYYY") -> str:
    """Normalize a date string to YYYY-MM-DD format.

    Args:
        date_str: Raw date string from statement
        source_format: Expected format hint

    Returns:
        Date string in YYYY-MM-DD format.
    """
    s = date_str.strip()

    # Try common formats
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    return s  # Return as-is if no format matches
