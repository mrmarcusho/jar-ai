"""Post-processing fixes for Docling PDF extraction output."""

import copy
import re
from typing import List, Tuple


def boa_fix_unicode_escapes(text: str) -> str:
    """Replace /uniXXXX escape sequences with the actual Unicode characters.

    Docling emits these for certain glyphs (o, O, /, ñ) due to BofA's
    custom PDF font encoding.
    """
    return re.sub(
        r"/uni([0-9A-Fa-f]{4})",
        lambda m: chr(int(m.group(1), 16)),
        text,
    )


def boa_fix_missing_co_id(text: str) -> str:
    """Restore 'CO' prefix before 'ID:' in ACH descriptions where Docling dropped it.

    In BofA ACH format, 'CO ID:' after 'INDN:...' is a Company Originator ID.
    Docling's table OCR occasionally drops the 'CO' token, producing bare
    'ID:' after the individual name.  This function detects that pattern and
    restores 'CO ID:'.

    Only applies when INDN: is present (ACH context) and an ID: follows the
    individual name without a preceding CO.
    """
    def _restore_co(m):
        if m.group(1).rstrip().endswith("CO"):
            return m.group(0)  # already has CO, leave alone
        return m.group(1) + " CO ID:"

    return re.sub(
        r"(INDN:\S+(?:\s+\S+)*?)\s+ID:",
        _restore_co,
        text,
    )


# ---------------------------------------------------------------------------
# Merge spanning header columns
# ---------------------------------------------------------------------------
# Docling expands a single header cell that spans N grid columns into N
# identical header entries.  Data rows under that span may contain
# duplicate or fragmented text across those same columns.  The helpers
# below detect these repeated headers and collapse the affected columns.


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
      ["A", "A", "A", "A"]  → "A"            (fully repeated)
      ["A", "B", "C", "C"]  → "A B C"        (split ACH, trailing dup)
      ["A", "A", "CO", "CO"] → "A CO"        (partial split)
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


# -- Markdown ---------------------------------------------------------------

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


# -- JSON dict --------------------------------------------------------------

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
