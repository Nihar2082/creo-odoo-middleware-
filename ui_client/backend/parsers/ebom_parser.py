from __future__ import annotations
import csv
from pathlib import Path
from typing import List
from matching_logic.models.types import EBOMRow

def _to_float(x: str) -> float:
    try:
        return float(str(x).strip().replace(",", "."))
    except Exception:
        return 1.0

def parse_ebom(path: str) -> List[EBOMRow]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    # CSV path
    if p.suffix.lower() in {".csv"}:
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows: List[EBOMRow] = []
            for r in reader:
                rows.append(
                    EBOMRow(
                        qty=_to_float(r.get("Qty") or r.get("Quantity") or "1"),
                        name=(r.get("Name") or r.get("Part Name") or "").strip(),
                        # Keep the source value as-is; user can overwrite via Category assignment.
                        item_type=(r.get("Item Type") or r.get("Type") or "").strip(),
                        revision=(r.get("Rev") or r.get("Revision") or "").strip() or None,
                        description=(r.get("Description") or "").strip() or None,
                    )
                )
            return rows

    
    # TXT: robust delimiter + header detection (handles BOM + leading blank line)
    raw_lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    if not raw_lines:
        return []

    # Find first non-empty line as header
    header_idx = None
    for i, line in enumerate(raw_lines):
        cleaned = line.lstrip("\ufeff").strip()
        if cleaned:
            header_idx = i
            break
    if header_idx is None:
        return []

    header = raw_lines[header_idx].lstrip("\ufeff").strip()
    delimiters = ["\t", ";", "|", ","]
    delim = max(delimiters, key=lambda d: header.count(d))

    headers = [h.strip() for h in header.split(delim)]
    headers_lc = [h.lower() for h in headers]

    def idx(*names: str) -> int:
        for n in names:
            n_lc = n.lower()
            for i, h in enumerate(headers_lc):
                if h == n_lc:
                    return i
        return -1

    i_qty = idx("Qty", "Quantity", "QTY")
    i_name = idx("Name", "Part Name")
    i_type = idx("Item Type", "Type")
    i_rev = idx("Rev", "Revision")
    i_desc = idx("Description")

    def looks_like_header(cols: list[str]) -> bool:
        # common exports repeat header lines; skip if columns resemble header keywords
        joined = " ".join(c.strip().lower() for c in cols if c is not None)
        return ("qty" in joined and "name" in joined and "item" in joined and "type" in joined)

    def is_number(s: str) -> bool:
        try:
            float(str(s).strip().replace(",", "."))
            return True
        except Exception:
            return False

    rows: List[EBOMRow] = []
    for line in raw_lines[header_idx + 1:]:
        if not line.strip():
            continue
        cols = [c.strip() for c in line.split(delim)]
        if looks_like_header(cols):
            continue
        # If qty column exists but is not numeric, skip (another strong header signal)
        if i_qty >= 0 and i_qty < len(cols) and not is_number(cols[i_qty]):
            continue

        rows.append(
            EBOMRow(
                qty=_to_float(cols[i_qty]) if i_qty >= 0 and i_qty < len(cols) else 1.0,
                name=cols[i_name] if i_name >= 0 and i_name < len(cols) else (cols[0] if cols else ""),
                # Keep the source token (e.g. MP/Bought/Normteil) visible initially.
                item_type=cols[i_type] if i_type >= 0 and i_type < len(cols) else "",
                revision=cols[i_rev] if i_rev >= 0 and i_rev < len(cols) and cols[i_rev] else None,
                description=cols[i_desc] if i_desc >= 0 and i_desc < len(cols) and cols[i_desc] else None,
            )
        )
    return rows

