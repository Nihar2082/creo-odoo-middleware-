from __future__ import annotations

from typing import List, Dict, Any

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


def _build_small_registry(parts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build a tiny in-memory registry for one EBOM row.

    This keeps the existing matching logic, but avoids downloading the full DB.
    """
    try:
        from matching_logic.core.normalize import normalize_name as _nn, canonical_key as _ck
    except Exception:
        return {"parts": parts, "index": {}}

    index: Dict[str, str] = {}
    for p in parts:
        p_name = str(p.get("part_name") or "")
        p["name_norm"] = _nn(p_name)
        p["canonical_key"] = _ck(p_name)

        ext = str(p.get("external_id", "")).strip()
        if not ext:
            continue

        if p["name_norm"]:
            index[p["name_norm"]] = ext
        if p["canonical_key"]:
            index[p["canonical_key"]] = ext

        iref = str(p.get("internal_reference") or "")
        if iref:
            n = _nn(iref)
            ck = _ck(iref)
            if n:
                index[n] = ext
            if ck:
                index[ck] = ext

    return {"parts": parts, "index": index}


def process_file(repo: Repo, module: str, ebom_rows: List[EBOMRow], threshold: float = 0.80) -> List[ProcessedRow]:
    """Parse EBOM rows into ProcessedRow objects and perform DB matching.

    Scalable matching strategy:
    - For each EBOM row, ask the backend for a *small* candidate set.
    - Run the existing local scoring on that candidate set only.
    """
    # Build bulk request once (significantly faster than N round-trips).
    queries = []
    for r in ebom_rows:
        queries.append(
            {
                "name": r.name,
                "internal_reference": getattr(r, "internal_reference", None),
                "item_type": (r.item_type or "").strip() or None,
                "limit": 80,
            }
        )

    candidate_lists = repo.get_part_candidates_bulk(queries, global_limit=80)
    if not isinstance(candidate_lists, list) or len(candidate_lists) != len(ebom_rows):
        candidate_lists = [[] for _ in ebom_rows]

    processed: List[ProcessedRow] = []
    for r, candidates in zip(ebom_rows, candidate_lists):
        registry = _build_small_registry(candidates or [])
        ext_to_name = {p.get("external_id"): (p.get("name_norm") or normalize_name(p.get("part_name") or "")) for p in registry.get("parts", [])}

        status, ext, suggestions = match_row(r.name, registry, threshold=threshold)

        pr = ProcessedRow(
            qty=r.qty,
            name=normalize_name(r.name),
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
