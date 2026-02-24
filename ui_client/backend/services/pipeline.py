from __future__ import annotations

from typing import List

from matching_logic.models.types import EBOMRow, ProcessedRow, MatchSuggestion
from matching_logic.core.normalize import normalize_name, canonical_key
from matching_logic.core.match import match_row
from backend.db.repo import Repo
from backend.services.id_generation import normalize_prefix, format_part_name

STD_PREFIX = "STD"


def compute_part_name(regular_prefix: str, row: ProcessedRow, add_prefix: bool) -> str:
    """Compute the final Part Name based on the designer prefix and standard flag."""
    desired_prefix = STD_PREFIX if row.is_standard else normalize_prefix(regular_prefix)
    return format_part_name(row.name, desired_prefix=desired_prefix, add_prefix=add_prefix)


def process_file(repo: Repo, module: str, ebom_rows: List[EBOMRow], threshold: float = 0.80) -> List[ProcessedRow]:
    """Parse EBOM rows into ProcessedRow objects and perform DB matching.

    Important UX policy:
    - We DO NOT auto-map or transform source 'item_type' tokens (MP/Bought/Normteil/...)
      into the UI categories.
    - The user assigns the category explicitly using the Category feature.

    Matching policy:
    - Name-based (prefix-stripped) matching only.
    - Default threshold (0.80) detects ~80% similar part names to flag as POSSIBLE_MATCH.
    """
    registry = repo.load_registry()

    # For enriching match suggestions with a readable name.
    ext_to_name = {p.get("external_id"): p.get("name_norm") for p in registry.get("parts", [])}

    processed: List[ProcessedRow] = []
    for r in ebom_rows:
        status, ext, suggestions = match_row(r.name, registry, threshold=threshold)

        pr = ProcessedRow(
            qty=r.qty,
            name=normalize_name(r.name),
            # Show the source value from the file initially (e.g. MP/Bought/Normteil).
            # User can overwrite via Category assignment.
            item_type=(r.item_type or "").strip(),
            revision=r.revision,
            description=r.description,
            status=status,
            external_id=ext,
            canonical_key=canonical_key(r.name, r.revision),
            included=(status != "EXISTING"),
        )

        if status == "POSSIBLE_MATCH":
            pr.suggestions = [
                MatchSuggestion(
                    external_id=s[0],
                    name=ext_to_name.get(s[0]) or "",
                    score=s[1],
                    reason=s[2],
                )
                for s in suggestions
            ]
            pr.match_decision = None

        processed.append(pr)

    return processed


"""NOTE:

This PoC intentionally does **not** persist generated IDs until the user exports.

All DB writes (parts + counters) happen in one commit step after a successful export.
"""
