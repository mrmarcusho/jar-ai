# How to Run

## Prerequisites

- macOS
- Python 3.11+ installed (download from https://www.python.org/downloads/ if needed)
- BofA PDF statement placed at `data/statements/eStmt_2023-12-13.pdf`

## Setup

1. Create the virtual environment and activate it:

```bash
source bin/start.sh
```

This creates a `venv/` directory (if it doesn't exist) and drops you into an activated shell.

2. Install dependencies:

```bash
pip install docling "numpy<2"
```

> **Note:** `numpy<2` is required because PyTorch 2.2.x is incompatible with NumPy 2.x.

## Run the Docling test

From the project root (with the venv activated):

```bash
python core/extraction_engine/test_docling.py
```

The first run will be slower as Docling downloads its layout analysis models from HuggingFace.

### Save output to a file

```bash
python core/extraction_engine/test_docling.py > data/docling_output.txt 2>&1
```

## What the test does

`core/extraction_engine/test_docling.py` feeds the BofA PDF through Docling's `DocumentConverter` and prints:

1. **Markdown export** - the document converted to structured markdown (tables, headers, paragraphs)
2. **JSON document structure** - the full Docling document model as JSON
