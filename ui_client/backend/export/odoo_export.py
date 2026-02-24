from __future__ import annotations

import csv
from typing import Any, Dict, List, Optional, Callable

from matching_logic.models.types import ProcessedRow


def export_odoo_csv(
    rows: List[ProcessedRow],
    out_path: str,
    regular_prefix: str,
    fieldnames: Optional[List[str]] = None,
    row_builder: Optional[Callable[[ProcessedRow], Dict[str, Any]]] = None,
) -> None:
    """Export included rows to an Odoo-compatible CSV.

    Key behaviors:
    - Exports ONLY rows with included=True.
    - Blocks export if any included row is unresolved (status == "POSSIBLE_MATCH").
    - Blocks export if any included row already exists in DB (status == "EXISTING").
    - Ensures required Odoo columns exist in the exported header.
    - Ensures "Internal Reference" is populated with "External ID" when missing.

    Notes:
    - `regular_prefix` is kept for backwards compatibility with older flows.
    - `fieldnames` should normally be provided by the UI as the current table headers
      (including any user-added columns).
    - `row_builder` allows the UI to define how to read values from the table.
    """

    included = [r for r in rows if getattr(r, "included", False)]

    unresolved = [r for r in included if getattr(r, "status", "") == "POSSIBLE_MATCH"]
    if unresolved:
        raise RuntimeError("Cannot export: unresolved POSSIBLE_MATCH rows exist.")

    existing_in_db = [r for r in included if getattr(r, "status", "") == "EXISTING"]
    if existing_in_db:
        raise RuntimeError(
            "Cannot export: some included rows already exist in the database. "
            "Uncheck 'Include' for EXISTING rows or rename them so they become NEW/CREATED."
        )

    missing_ids = [r for r in included if not (getattr(r, "external_id", "") or "").strip()]
    if missing_ids:
        raise RuntimeError("Cannot export: some included rows are missing External ID. Generate IDs first.")

    missing_type = [r for r in included if not (getattr(r, "item_type", "") or "").strip()]
    if missing_type:
        raise RuntimeError(
            "Cannot export: some included rows are missing 'Type of Item'. "
            "Select a category before exporting."
        )

    # Determine export headers
    if fieldnames is None:
        # Fallback legacy default
        fieldnames = ["External ID", "Part Name", "Internal Reference", "Type of Item"]

    # Ensure required columns exist
    required_columns = ["External ID", "Part Name", "Internal Reference", "Type of Item"]
    for col in required_columns:
        if col not in fieldnames:
            fieldnames.append(col)

    # Default row builder (legacy)
    if row_builder is None:
        def row_builder(r: ProcessedRow) -> Dict[str, Any]:
            ext = (r.external_id or "").strip()
            part_name = (r.name or "").strip()
            return {
                "External ID": ext,
                "Part Name": part_name,
                "Internal Reference": ext,
                "Type of Item": (r.item_type or "").strip(),
            }

    # Write CSV
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for r in included:
            row = row_builder(r) or {}

            # Ensure Internal Reference is present and equals External ID if blank
            ext = (getattr(r, "external_id", "") or "").strip()
            if (not row.get("Internal Reference")) and ext:
                row["Internal Reference"] = ext

            # Ensure every header exists in each row
            out_row = {h: ("" if row.get(h) is None else str(row.get(h))) for h in fieldnames}
            writer.writerow(out_row)
