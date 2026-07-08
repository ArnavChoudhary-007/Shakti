"""
connectors/excel_connector.py
Handles: Excel (.xlsx, .xls) and CSV (.csv)

Strategy:
  - Each sheet is classified as NARRATIVE or TABULAR:
      * NARRATIVE  → mostly text cells, few numbers → embed as text
      * TABULAR    → ledger/data sheet (>40% numeric) → emit as "table"
        modality so Phase 4 can load it into SQLite
  - Emits one envelope per sheet (Excel) or one per file (CSV).
  - Tabular sheets include the raw dataframe as structured_data for
    the SQLite loader; text summary is always included for vector search.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from .base import envelope, make_source_id

logger = logging.getLogger(__name__)

try:
    import pandas as pd
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False
    logger.warning("pandas not installed — Excel/CSV support disabled.")


# ── Sheet classification ─────────────────────────────────────

def _classify_sheet(df: "pd.DataFrame", numeric_col_threshold: float = 0.30) -> str:
    """
    Returns 'tabular' if the proportion of numeric-typed columns is at or
    above numeric_col_threshold; otherwise 'narrative'.

    Column-level classification is more robust than cell-level ratios
    because a sheet like Payments (Vendor, Amount, Date, Invoice_No, Status)
    has 1 numeric column / 5 total = 20% — but Amount alone signals ledger data.
    We also check for any 'Amount'/'Price'/'Total' header keywords as a fallback.
    """
    if df.empty:
        return "narrative"

    total_cols = len(df.columns)
    if total_cols == 0:
        return "narrative"

    numeric_cols = len(df.select_dtypes(include="number").columns)
    ratio = numeric_cols / total_cols

    if ratio >= numeric_col_threshold:
        return "tabular"

    # Keyword heuristic: ledger-style headers even if ratio is low
    ledger_keywords = {"amount", "price", "total", "cost", "balance", "quantity",
                       "qty", "payment", "invoice", "paid", "debit", "credit", "value"}
    col_names_lower = {str(c).lower() for c in df.columns}
    if col_names_lower & ledger_keywords:
        return "tabular"

    return "narrative"


def _df_to_text_summary(df: "pd.DataFrame", max_rows: int = 50) -> str:
    """
    Convert a dataframe to a natural-language summary for vector embedding.
    Always produced, even for tabular sheets.
    """
    lines: List[str] = []
    lines.append(f"Columns: {', '.join(str(c) for c in df.columns)}")
    lines.append(f"Row count: {len(df)}")

    # Sample rows
    sample = df.head(max_rows)
    for _, row in sample.iterrows():
        row_str = " | ".join(f"{col}: {val}" for col, val in row.items() if pd.notna(val))
        if row_str:
            lines.append(row_str)

    return "\n".join(lines)


def _df_to_records(df: "pd.DataFrame") -> List[Dict[str, Any]]:
    """Serialize dataframe to list of dicts (for structured_data field)."""
    return df.where(df.notna(), other=None).to_dict(orient="records")


# ── Excel ────────────────────────────────────────────────────

def extract_excel(file_path: str) -> Generator[Dict[str, Any], None, None]:
    """Yield one envelope per sheet."""
    if not _HAS_PANDAS:
        raise RuntimeError("pandas is required for Excel extraction.")

    path = Path(file_path)
    source_id = make_source_id(file_path)

    xls = pd.ExcelFile(file_path)
    sheet_names = xls.sheet_names

    for sheet_name in sheet_names:
        try:
            df = pd.read_excel(xls, sheet_name=sheet_name, header=0)
        except Exception as exc:
            logger.warning("Could not read sheet %r: %s", sheet_name, exc)
            continue

        if df.empty:
            logger.debug("Sheet %r is empty — skipping.", sheet_name)
            continue

        sheet_class = _classify_sheet(df)
        text_summary = _df_to_text_summary(df)

        meta: Dict[str, Any] = {
            "file_name": path.name,
            "sheet_name": sheet_name,
            "sheet_index": sheet_names.index(sheet_name),
            "total_sheets": len(sheet_names),
            "row_count": len(df),
            "col_count": len(df.columns),
            "sheet_classification": sheet_class,
            "columns": [str(c) for c in df.columns],
            "full_text": text_summary,
        }

        # For tabular sheets, attach records so Phase 4 can load into SQLite
        if sheet_class == "tabular":
            meta["records"] = _df_to_records(df)

        yield envelope(
            source_type="excel",
            source_id=f"{source_id}_{sheet_name}",
            raw_path=file_path,
            raw_metadata=meta,
            modality="table" if sheet_class == "tabular" else "text",
        )


# ── CSV ──────────────────────────────────────────────────────

def extract_csv(file_path: str, encoding: str = "utf-8") -> Generator[Dict[str, Any], None, None]:
    """Yield a single envelope for a CSV file."""
    if not _HAS_PANDAS:
        raise RuntimeError("pandas is required for CSV extraction.")

    path = Path(file_path)
    source_id = make_source_id(file_path)

    # Try to detect encoding
    try:
        import chardet
        with open(file_path, "rb") as f:
            raw = f.read(65536)
        detected = chardet.detect(raw)
        encoding = detected.get("encoding") or "utf-8"
    except ImportError:
        pass

    try:
        df = pd.read_csv(file_path, encoding=encoding)
    except Exception as exc:
        logger.error("Failed to read CSV %s: %s", path.name, exc)
        return

    if df.empty:
        logger.warning("CSV %s is empty.", path.name)
        return

    sheet_class = _classify_sheet(df)
    text_summary = _df_to_text_summary(df)

    meta: Dict[str, Any] = {
        "file_name": path.name,
        "row_count": len(df),
        "col_count": len(df.columns),
        "sheet_classification": sheet_class,
        "columns": [str(c) for c in df.columns],
        "full_text": text_summary,
    }
    if sheet_class == "tabular":
        meta["records"] = _df_to_records(df)

    yield envelope(
        source_type="csv",
        source_id=source_id,
        raw_path=file_path,
        raw_metadata=meta,
        modality="table" if sheet_class == "tabular" else "text",
    )


# ── Unified entry point ──────────────────────────────────────

SUPPORTED_EXTENSIONS = {".xlsx", ".xls", ".csv"}


def extract(file_path: str) -> Generator[Dict[str, Any], None, None]:
    ext = Path(file_path).suffix.lower()
    if ext in (".xlsx", ".xls"):
        yield from extract_excel(file_path)
    elif ext == ".csv":
        yield from extract_csv(file_path)
    else:
        raise ValueError(f"Unsupported extension {ext!r} for excel_connector.")
