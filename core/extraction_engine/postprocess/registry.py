"""Auto-discover bank modules and dispatch based on detect() confidence scoring."""

import importlib
from pathlib import Path

from core.extraction_engine.postprocess.base import BankPostProcessor


# All registered processors — populated by discover_processors()
_PROCESSORS: list[BankPostProcessor] = []


def discover_processors():
    """Walk the banks/ directory tree and register all BankPostProcessor subclasses.

    Auto-discovers any class that subclasses BankPostProcessor in any .py file
    under banks/regions/. No manual registration needed — just drop a new bank
    folder with a processor class and it's automatically available.
    """
    global _PROCESSORS
    _PROCESSORS = []

    banks_dir = Path(__file__).parent / "banks"

    if not banks_dir.exists():
        return

    # Walk all .py files under banks/
    for py_file in banks_dir.rglob("*.py"):
        if py_file.name.startswith("_"):
            continue

        # Convert file path to module path
        # e.g., banks/US/boa/boa.py -> core.extraction_engine.postprocess.banks.US.boa.boa
        relative = py_file.relative_to(Path(__file__).parent)
        module_path = "core.extraction_engine.postprocess." + str(relative.with_suffix("")).replace("/", ".").replace("\\", ".")

        try:
            module = importlib.import_module(module_path)

            # Find all BankPostProcessor subclasses in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BankPostProcessor)
                    and attr is not BankPostProcessor
                ):
                    _PROCESSORS.append(attr())
        except Exception as e:
            print(f"Warning: Failed to load bank module {module_path}: {e}")


def detect_bank(md_text: str) -> tuple[BankPostProcessor | None, float]:
    """Identify which bank this markdown is from using confidence scoring.

    Runs detect() on each registered processor. Returns the highest-scoring
    match with its confidence score.

    Confidence thresholds:
    - >= 0.7: High confidence — auto-dispatch, no user confirmation needed
    - 0.4-0.7: Medium confidence — suggest to user, ask for confirmation
    - < 0.4: Low confidence — unknown format

    Args:
        md_text: Raw markdown from Docling conversion.

    Returns:
        Tuple of (BankPostProcessor instance or None, confidence score).
        Returns (None, 0.0) if no bank scores above the low threshold.
    """
    if not _PROCESSORS:
        discover_processors()

    LOW_CONFIDENCE = 0.4

    best_processor = None
    best_score = 0.0
    runner_up_score = 0.0

    for processor in _PROCESSORS:
        try:
            score = processor.detect(md_text)
            if score > best_score:
                runner_up_score = best_score
                best_score = score
                best_processor = processor
            elif score > runner_up_score:
                runner_up_score = score
        except Exception as e:
            print(f"Warning: detect() failed for {processor.bank_name}: {e}")

    # If top two scores are very close, flag ambiguity
    if best_score - runner_up_score < 0.1 and best_score >= LOW_CONFIDENCE:
        print(f"  Warning: Ambiguous detection — top score {best_score:.2f} vs runner-up {runner_up_score:.2f}")
        print(f"  User confirmation strongly recommended.")

    if best_score < LOW_CONFIDENCE:
        return None, best_score

    return best_processor, best_score


def list_supported_banks() -> list[dict]:
    """Return list of all supported banks with metadata."""
    if not _PROCESSORS:
        discover_processors()

    return [
        {
            "bank_name": p.bank_name,
            "country_code": p.country_code,
            "region": p.region,
            "currency": p.currency,
        }
        for p in _PROCESSORS
    ]
